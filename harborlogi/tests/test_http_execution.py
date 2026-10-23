from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError

from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger, MoveEvidence
from test_execution import FakeFleet, bound_moves
from test_policy import NOW


@contextmanager
def server(fleet, *, redirect=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.handle_request("POST")

        def do_GET(self):
            self.handle_request("GET")

        def handle_request(self, method):
            if self.headers.get("Authorization") != "Bearer synthetic-operator":
                self.send_error(403)
                return
            if redirect:
                fleet.calls.append((method, self.path, None))
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(raw) if raw else None
            response = json.dumps(fleet(method, self.path, body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


class HttpTests(unittest.TestCase):
    def test_inventory_to_proposal_to_observed_business_completion_over_http(self):
        fleet = FakeFleet()
        with tempfile.TemporaryDirectory() as tmp, server(fleet) as origin:
            ledger = BusinessLedger(Path(tmp) / "business.db", evidence_sources=("installed-camera",))
            ledger.prepare("retrieval", 3, bound_moves(), target_id="target", supply_slot="supply")
            client = RosyClient(origin, "synthetic-operator")
            for index, move in enumerate(bound_moves()):
                mission = ledger.propose("retrieval", client)
                ledger.refresh("retrieval", client, now=NOW)
                ledger.confirm("retrieval", MoveEvidence(f"e{index}", mission, "attempt-1", 3,
                               "installed-camera", NOW, move.cargo_id, move.destination, True), now=NOW)
            ledger.finish("retrieval", MoveEvidence("unload", "mission-2", "attempt-1", 3,
                          "installed-camera", NOW, "target", "supply", True, unloaded=True), now=NOW)
            self.assertEqual(ledger.read("retrieval")["state"], "COMPLETED")
        self.assertEqual(sum(path.endswith("/proposals") for _, path, _ in fleet.calls), 3)

    def test_auth_failure_and_redirect_never_retry_or_forward_credentials(self):
        for token, redirect in [("wrong", False), ("synthetic-operator", True)]:
            fleet = FakeFleet()
            with self.subTest(token=token), server(fleet, redirect=redirect) as origin:
                with self.assertRaises(HTTPError):
                    RosyClient(origin, token).propose(bound_moves()[0], request_key="key")
            self.assertLessEqual(len(fleet.calls), 1)

    def test_nonlocal_plain_http_and_embedded_credentials_are_rejected(self):
        for origin in ("http://example.test", "https://user:pass@example.test", "https://example.test/?token=x"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                RosyClient(origin, "synthetic-operator")

    def test_concurrent_submissions_have_one_durable_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = BusinessLedger(Path(tmp) / "business.db", evidence_sources=("installed-camera",))
            ledger.prepare("retrieval", 3, bound_moves(), target_id="target", supply_slot="supply")
            barrier = threading.Barrier(2)
            fleet = FakeFleet()
            def transport(method, path, body):
                if path.endswith("/compile"):
                    barrier.wait(timeout=5)
                return fleet(method, path, body)
            client = RosyClient(transport=transport)
            def submit():
                try:
                    return ledger.propose("retrieval", client)
                except ValueError:
                    return "refused"
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: submit(), range(2)))
            self.assertEqual(results.count("refused"), 1)
            self.assertEqual(sum(path.endswith("/proposals") for _, path, _ in fleet.calls), 1)

    def test_restart_of_an_unanswered_durable_intent_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "business.db"
            ledger = BusinessLedger(path, evidence_sources=("installed-camera",))
            ledger.prepare("retrieval", 3, bound_moves(), target_id="target", supply_slot="supply")
            with closing(sqlite3.connect(path)) as c, c:
                c.execute("UPDATE business SET state='SUBMITTING'")
            recovered = BusinessLedger(path, evidence_sources=("installed-camera",))
            self.assertEqual(recovered.read("retrieval")["state"], "HOLD")

    def test_legacy_business_rows_gain_a_version_without_losing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            with closing(sqlite3.connect(path)) as c, c:
                c.execute("""CREATE TABLE business (
                    request_key TEXT PRIMARY KEY,digest TEXT,payload TEXT,state TEXT,step_index INTEGER,
                    mission_id TEXT,attempt_id TEXT,goal_confirmed INTEGER,
                    target_mission_id TEXT,target_attempt_id TEXT)""")
                c.execute("INSERT INTO business VALUES('old','digest','{}','WAITING',0,'mission',NULL,0,NULL,NULL)")
            recovered = BusinessLedger(path, evidence_sources=("installed-camera",))
            row = recovered.read("old")
            self.assertEqual((row["row_version"], row["state"], row["mission_id"]), (0, "WAITING", "mission"))


if __name__ == "__main__":
    unittest.main()
