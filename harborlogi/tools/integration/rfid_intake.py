"""Exercise RFID intake through the Cell and Fleet R HTTP routes and a temporary Harbor ledger."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

from harbor.port_events import IntakeLedger
from harbor.rosy_http import RosyClient
from tools.integration.real_fleet_http import verify_checkout

WORKCELL = "test-supply"
CARGO = "TEST-CARGO"
EPC = "432D31320000000000000000"
WORKCELL_TOKEN = "test-reader"
PEER_AUTH = {"Authorization": "Bearer test-fleet-r"}
HARBOR_AUTH = {"Authorization": "Bearer test-harbor"}
INGRESS = f"/api/fleet/workcell-observations/{WORKCELL}/intake-readings"


def run(platform, temporary):
    from tools.integration.fleet_harness import enable

    enable(platform)
    sys.path.insert(0, str(platform / "deploy/robot/omx"))

    import httpx
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from fleet.server.intake_transfer import _BINDINGS
    from fleet.server.port_event_routes import install_port_event_routes
    import port_event_outbox

    class TestObservations:
        def authorize_workcell(self, workcell_id, token):
            if (workcell_id, token) != (WORKCELL, WORKCELL_TOKEN):
                raise PermissionError

    principals = {
        PEER_AUTH["Authorization"]: "control-unit:R",
        HARBOR_AUTH["Authorization"]: "harbor-integration",
    }

    def authorize(authorization: str = Header("")):
        if authorization not in principals:
            raise HTTPException(401)
        return SimpleNamespace(role="service", principal_id=principals[authorization])

    def make_fleet(path, observations=None):
        app = FastAPI()
        service = SimpleNamespace(store=SimpleNamespace(path=path))
        install_port_event_routes(app, observations, service, authorize=authorize, require_viewer=authorize)
        return app

    path = temporary / "cell.sqlite3"
    cell = make_fleet(path, TestObservations())
    cell.state.control_unit = SimpleNamespace(unit=SimpleNamespace(unit_id="cell-a", parent="R"))
    cell.state.intake = {"intake_point": "test-intake", "container_epcs": {EPC: CARGO}}
    cell.state.arm_transfer_job_store = SimpleNamespace(
        _journal=SimpleNamespace(_connect=lambda: sqlite3.connect(path)),
    )
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(_BINDINGS)
        db.execute("INSERT INTO fleet_intake_bindings VALUES (?, ?, ?)", ("supply-test", "trip-test", CARGO))
        db.execute("CREATE TABLE fleet_cell_jobs(mission_id TEXT, status TEXT)")
        db.execute("INSERT INTO fleet_cell_jobs VALUES (?, ?)", ("supply-test", "ADMITTED"))

    class CellPeer:
        async def port_events(self, unit_id, after_seq, limit):
            assert unit_id == "cell-a"
            transport = httpx.ASGITransport(app=cell)
            async with httpx.AsyncClient(transport=transport, base_url="http://cell.test") as client:
                reply = await client.get("/api/fleet/control-unit/port-events",
                                         params={"after_seq": after_seq, "limit": limit}, headers=PEER_AUTH)
                return reply.raise_for_status().json()

    fleet_r = make_fleet(temporary / "root.sqlite3")
    r_unit = SimpleNamespace(unit_id="R", parent=None, resources=())
    child = SimpleNamespace(unit_id="cell-a", parent="R", resources=(f"workcell:{WORKCELL}",))
    fleet_r.state.control_unit = SimpleNamespace(
        unit=r_unit, topology=SimpleNamespace(units={"R": r_unit, "cell-a": child}), peer=CellPeer(),
    )
    now = datetime.now(timezone.utc)
    ledger = IntakeLedger(temporary / "harbor.sqlite3")
    ledger.expect(CARGO, now=now)
    ledger.arrive(CARGO, now=now)

    with TestClient(cell) as cell_http, TestClient(fleet_r) as root_http:
        def http(method, route, body):
            return root_http.request(method, route, json=body, headers=HARBOR_AUTH).raise_for_status().json()

        class ReaderLink:
            def __init__(self):
                self.acknowledgements = []

            def post_intake_reading(self, reading):
                reply = cell_http.post(INGRESS, json=reading.model_dump(mode="json"),
                                       headers={"X-Workcell-Observation-Token": WORKCELL_TOKEN})
                self.acknowledgements.append(reply.raise_for_status().json())
                if len(self.acknowledgements) == 1:
                    raise OSError("test lost the reply after Fleet recorded the scan")

        harbor = RosyClient(transport=http)
        early = port_event_outbox.reading("rfid_intake", {"state": "read", "epc": EPC}, observed_at=now)
        assert cell_http.post(INGRESS, json=early.model_dump(mode="json")).status_code == 401
        cell_http.post(INGRESS, json=early.model_dump(mode="json"),
                       headers={"X-Workcell-Observation-Token": WORKCELL_TOKEN}).raise_for_status()
        first = ledger.poll(harbor, connection="fleet-r", now=now)
        assert list(first["outcomes"].values()) == ["HOLD_UNKNOWN"]
        assert ledger.read(CARGO)["state"] == "TRIP_ARRIVED"

        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE fleet_cell_jobs SET status='GOAL_CONFIRMED' WHERE mission_id='supply-test'")
        confirmed = port_event_outbox.reading("rfid_intake", {"state": "read", "epc": EPC}, observed_at=now)
        outbox = temporary / "outbox"
        stored = port_event_outbox.queue(confirmed, outbox)
        link = ReaderLink()
        assert not port_event_outbox.send(link, outbox) and stored.exists()
        assert port_event_outbox.send(link, outbox) and not stored.exists()
        assert link.acknowledgements[0] == link.acknowledgements[1]

        applied = ledger.poll(harbor, connection="fleet-r", now=now)
        assert applied["complete"] and list(applied["outcomes"].values()) == ["APPLIED"]
        assert ledger.read(CARGO)["state"] == "RECEIVED_AT_INTAKE"
        assert ledger.poll(harbor, connection="fleet-r", now=now)["outcomes"] == {}
        with closing(ledger._connect()) as db:
            [(payload,)] = db.execute(
                "SELECT payload FROM port_events WHERE kind='container.received_at_intake'",
            ).fetchall()
        assert EPC not in payload
        print("PASS: authorized RFID -> Cell Fleet -> Fleet R -> Harbor, exactly one intake receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-root", type=Path, required=True)
    parser.add_argument("--platform-sha", required=True)
    args = parser.parse_args()
    if not __debug__:
        parser.error("run with assertions enabled")
    platform = args.platform_root.resolve()
    verify_checkout(platform, args.platform_sha)
    with tempfile.TemporaryDirectory(prefix="harbor-rfid-test-") as temporary:
        run(platform, Path(temporary))


if __name__ == "__main__":
    main()
