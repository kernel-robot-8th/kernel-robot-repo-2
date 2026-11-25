"""Exercise the ROSY/Harbor intake and placement round trip with synthetic device evidence."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from harbor.port_events import IntakeLedger
from harbor.rosy_http import RosyClient
from tools.integration.fleet_harness import enable
from tools.integration.real_fleet_http import verify_checkout
from tools.integration.rfid_intake import WORKCELL, sample_epc


def run(platform, temp):
    enable(platform)
    import tools
    tools.__path__.append(str(platform / "tools"))
    sys.path.insert(0, str(platform / "operations/account"))
    sys.path.insert(0, str(platform / "deploy/robot/omx"))
    from tools.sim.bay_departure_sim import build_world_261026_site_map
    from rfd40_ordered_outbox import Capture, drain, open_spool
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from fleet.pipeline.cell_site_targets import list_canonical_sites
    from fleet.server.site_auth import build_authorize, build_role_guards
    from fleet.server.cell_site_recognition_routes import install_cell_site_recognition_routes
    from fleet.server.pipeline_routes import install_pipeline_routes
    from fleet.server.port_event_routes import install_port_event_routes
    from rosy_account.principal import SitePrincipal

    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth.models import Group, User
    from django.test import Client
    from harbor_web import services
    from harbor_web.cell_plan_dispatch import dispatch_harbor_plan
    from harbor_web.intake_occurrences import project_occurrences
    from harbor_web.models import IntakeOccurrence, Observation, Plan
    from harbor_web.runtime_models import DeploymentState

    call_command("migrate", verbosity=0)
    actor = User.objects.create_user("synthetic-operator")
    actor.groups.add(Group.objects.get_or_create(name="operator")[0], Group.objects.get_or_create(name="admin")[0])
    DeploymentState.objects.get_or_create(pk=1, defaults={"mode": "test"})
    config = Path(__file__).resolve().parents[2] / "config"
    policy = json.loads((config / "spatial-policy-3x6.json").read_text(encoding="utf-8"))
    policy["yard_layout"] = json.loads((config / "yard-layout-3x6.json").read_text(encoding="utf-8"))
    condition = services.register_condition(actor, name="Synthetic", calibration_ref="fixture", payload=policy)
    cargo = "C-12"
    services.register_cargo(actor, code=cargo, name=cargo, weight_kg=2, volume_m3="0.012",
                            destination="test", length_cells=3)
    now = datetime.now(timezone.utc)
    yard = dict(schema="grid-placement-v1", observed_areas=["yard", "temp_a", "temp_b", "stand"],
                holdings=[], placements=[])
    services.record_observation(actor, source_id="yard", source_revision="empty", observed_at=now,
                                condition=condition, provenance="DECLARED", payload=yard)
    principals = {sha256(token.encode()).hexdigest(): SitePrincipal(name, "service")
                  for token, name in (("parent-test", "control-unit:R"), ("harbor-test", "harbor-test"))}
    auth = build_authorize(None, principals, None)
    viewer, operator, _, proposer = build_role_guards(auth, principals)
    peer_headers = {"Authorization": "Bearer parent-test"}
    app_headers = {"Authorization": "Bearer harbor-test"}
    sensor_header = "X-Workcell-Observation-Token"

    class Sensor:
        def authorize_workcell(self, workcell, token):
            if (workcell, token) != (WORKCELL, "reader-test"):
                raise PermissionError

        def uses_token(self, token):
            return token == "camera-test"

        def authenticated_camera(self, source, token):
            return (source, token) == ("camera", "camera-test")

    def fleet(name, setting):
        app = FastAPI()
        with patch.dict(os.environ, {setting: WORKCELL, "ROSY_INTAKE_RFID_READER_EPOCH": "test-epoch"}):
            install_port_event_routes(app, Sensor(), SimpleNamespace(store=SimpleNamespace(path=temp / name)),
                                      authorize=auth, require_viewer=viewer)
        return app

    child = fleet("cell.db", "ROSY_INTAKE_RFID_ENCODED_WORKCELL")
    root = fleet("r.db", "ROSY_INTAKE_RFID_WORKCELL")
    site, graph, _ = build_world_261026_site_map()
    maps = SimpleNamespace(active=lambda: (1, site, graph, None), rules=SimpleNamespace(active_rules=lambda: None))
    root.state.operator_guard, root.state.proposer_guard = [Depends(operator)], [Depends(proposer)]
    root.state.pipeline_governance_db_path = ":memory:"
    with patch("fleet.server.pipeline_routes._storage_dir", return_value=temp):
        install_pipeline_routes(root, site_maps=maps, read_guard=[Depends(viewer)])
    with patch.dict(os.environ, {"ROSY_PLACEMENT_CAMERA_SOURCE": "camera", "ROSY_PLACEMENT_CALIBRATION_REVISION": "test"}):
        install_cell_site_recognition_routes(root, service=Sensor(), require_viewer=viewer)
    parent = SimpleNamespace(unit_id="R", parent=None, resources=())
    unit = SimpleNamespace(unit_id="A", parent="R", resources=("workcell:" + WORKCELL,))
    child.state.control_unit = SimpleNamespace(unit=unit)
    with TestClient(child) as cell, TestClient(root) as upstream:
        class Peer:
            def page(self, route, after, limit):
                reply = cell.get("/api/fleet/control-unit/" + route, headers=peer_headers,
                                 params=dict(after_seq=after, limit=limit))
                assert reply.is_success, (route, reply.text)
                return reply.json()

            async def intake_candidates(self, name, after, limit):
                assert name == unit.unit_id
                return self.page("intake-candidates", after, limit)

            async def port_events(self, name, after, limit):
                assert name == unit.unit_id
                return self.page("port-events", after, limit)

        root.state.control_unit = SimpleNamespace(unit=parent, peer=Peer(),
            topology=SimpleNamespace(units={"A": unit, "R": parent}))
        def transport(method, path, body):
            response = upstream.request(method, path, json=body, headers=app_headers)
            assert response.is_success, (path, response.text)
            return response.json()
        harbor = RosyClient(transport=transport)
        ledger = IntakeLedger(temp / "receipts.db")
        occurrence = "synthetic-inbound-12"
        root.state.omx_cell_tasks["supply"] = dict(task_id="supply", cell_id="cell_s", arm_id="arm_s",
            src_site="vehicle", dst_site="belt", status="COMPLETED", intake_workcell=WORKCELL,
            intake_occurrence_id=occurrence, created_at=now.timestamp()-8, completed_at=now.timestamp()-1,
            claimed_at=now.timestamp()-5, handoff_lease_id="test-lease",
            receipt={"physical_completion": True, "simulated_success": False, "phase": "RELEASED",
                     "stage_id": "supply", "handoff_lease_id": "test-lease"}, stage_result={"task_verified": True})
        route = f"/api/fleet/workcell-observations/{WORKCELL}/intake-readings"
        class Link:
            def post_intake_reading(self, item):
                response = cell.post(route, json=item.model_dump(mode="json"), headers={sensor_header: "reader-test"})
                assert response.is_success, response.text
                return response.json()
        def project():
            result = ledger.poll(harbor, connection="fleet-r", now=datetime.now(timezone.utc))
            assert result["complete"], result
            project_occurrences(ledger)
        with closing(open_spool(temp / "reader.db", "test-epoch")) as spool:
            elapsed = 0
            capture = Capture(spool, utc=lambda: now + timedelta(seconds=elapsed - 10), monotonic=lambda: elapsed)
            elapsed = 7
            capture.record(sample_epc(cargo), triggered=True, max_interval=12)
            drain(spool, Link(), Event())
            project()
            assert not IntakeOccurrence.objects.exists()
            elapsed = 10
            capture.record(None, triggered=False, max_interval=12)
            drain(spool, Link(), Event())
            project()
        receipt = IntakeOccurrence.objects.get(pk=occurrence)
        dispatch = dispatch_harbor_plan(actor, plan_id=Plan.objects.get(order=receipt.order).pk, client=harbor)
        assert dispatch.rosy_plan_id, dispatch.state
        plan = harbor.cell_plan_request(dispatch.request_key)["plan"]
        assert plan["plan_id"] == dispatch.rosy_plan_id
        task, = [t for t in root.state.omx_cell_tasks.values() if t.get("plan_id") == dispatch.rosy_plan_id]
        assert task["intake_occurrence_id"] == occurrence and task["placement_attempt_id"] == dispatch.request_key
        requested = dispatch.request_payload["steps"][0]["dst_site"]
        actual = "yard-r1-c4-l1" if requested != "yard-r1-c4-l1" else "yard-r1-c1-l1"
        # The hardware owner and the camera are synthetic. Fleet plan creation and event processing above/below are real.
        finished = datetime.now(timezone.utc)
        task.update(status="COMPLETED", completed_at=finished.timestamp(), handoff_lease_id="placement-lease",
                    receipt={"physical_completion": True, "simulated_success": False, "phase": "RELEASED",
                             "stage_id": task["task_id"], "handoff_lease_id": "placement-lease"},
                    stage_result={"task_verified": True})
        frame = dict(schema="rosy.cell-site-recognition.v1", snapshot_id="test-frame", source_id="camera",
                     calibration_revision="test", site_map_revision=list_canonical_sites()["revision"],
                     observed_at=datetime.now(timezone.utc).isoformat(), sites=[dict(site_ref=actual, state="OCCUPIED",
                     object_class=None, object_ref_read=cargo, confidence=1)])
        for _ in range(2):
            upstream.post("/api/fleet/cell-site-recognition", json=frame,
                          headers={sensor_header: "camera-test"}).raise_for_status()
            project()
        receipt.refresh_from_db()
        assert (receipt.status, receipt.observed_slot) == ("SLOT_MISMATCH", actual)
        assert Observation.objects.filter(provenance="PORT_EVENT").count() == 1
        assert len(ledger.occurrence_events()) == 2
        browser = Client(HTTP_HOST="localhost")
        browser.force_login(actor)
        inventory = browser.get("/inventory/")
        assert inventory.status_code == 200
        html = inventory.content.decode()
        assert all(text in html for text in (occurrence, requested, actual, "요청·관측 위치 불일치"))
        print(f"PASS: {cargo}, one receipt, one proposal, requested {requested}, observed {actual}, replay idempotent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-root", type=Path, required=True)
    parser.add_argument("--platform-sha", required=True)
    args = parser.parse_args()
    if not __debug__:
        parser.error("assertions must be enabled")
    platform = args.platform_root.resolve()
    verify_checkout(platform, args.platform_sha)
    with tempfile.TemporaryDirectory(prefix="rfid-placement-") as directory:
        with patch.dict(os.environ, {
            "DJANGO_SETTINGS_MODULE": "harbor_web.settings", "HARBOR_WEB_MODE": "test",
            "HARBOR_WEB_SECRET_KEY": "ci-test-only-00000000000000000000000000000000000000000",
            "HARBOR_WEB_DATABASE": str(Path(directory) / "harbor.db"), "ROSY_FLEET_CELL_AB_RELEASE_ENABLED": "1",
            "ROSY_INTAKE_RFID_MANIFEST": "", "ROSY_INTAKE_RFID_ENCODED_WORKCELL": "", "ROSY_INTAKE_RFID_WORKCELL": "",
        }):
            try:
                run(platform, Path(directory))
            finally:
                from django.db import connections
                connections.close_all()
