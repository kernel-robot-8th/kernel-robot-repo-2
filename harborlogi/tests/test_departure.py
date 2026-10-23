from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from harbor.departure import DepartureLedger, SupplyClearance
from test_policy import NOW

# Phase 1 the clearance source is a human confirmation; camera sources come later
# (ADR 0005 decision 6, CONTEXT.md "공급암 S의 퇴피").
SOURCES = ("operator-1",)


def clearance(evidence_id="s-clear-1", *, vehicle="pinky-1", when=NOW, source="operator-1"):
    return SupplyClearance(evidence_id, source, vehicle, when)


class SupplyClearanceTests(unittest.TestCase):
    def test_blank_or_padded_identifiers_are_rejected(self):
        for bad in ("", " pad", "pad "):
            with self.assertRaises(ValueError):
                SupplyClearance(bad, "operator-1", "pinky-1", NOW)

    def test_naive_timestamp_is_rejected(self):
        from datetime import datetime
        with self.assertRaises(ValueError):
            SupplyClearance("s-clear-1", "operator-1", "pinky-1", datetime(2026, 10, 21))


class DepartureLedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "departure.sqlite3"
        self.ledger = DepartureLedger(self.path, clearance_sources=SOURCES)

    def test_recorded_clearance_authorizes_one_departure(self):
        self.ledger.record(clearance(), now=NOW)
        self.ledger.authorize("pinky-1", now=NOW + timedelta(seconds=1))

    def test_second_authorize_requires_a_new_clearance(self):
        self.ledger.record(clearance(), now=NOW)
        self.ledger.authorize("pinky-1", now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.authorize("pinky-1", now=NOW + timedelta(seconds=1))

    def test_unrecorded_vehicle_is_not_authorized(self):
        with self.assertRaises(ValueError):
            self.ledger.authorize("pinky-1", now=NOW)

    def test_clearance_for_another_vehicle_does_not_authorize(self):
        self.ledger.record(clearance(vehicle="pinky-2"), now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.authorize("pinky-1", now=NOW)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.record(clearance(source="stray-camera"), now=NOW)

    def test_future_observation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.record(clearance(when=NOW + timedelta(seconds=1)), now=NOW)

    def test_stale_observation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.record(clearance(when=NOW - timedelta(seconds=6)), now=NOW)

    def test_reused_evidence_identity_is_rejected(self):
        self.ledger.record(clearance(), now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.record(clearance(vehicle="pinky-2"), now=NOW + timedelta(seconds=1))

    def test_stale_recorded_clearance_does_not_authorize(self):
        self.ledger.record(clearance(), now=NOW)
        with self.assertRaises(ValueError):
            self.ledger.authorize("pinky-1", now=NOW + timedelta(seconds=6))

    def test_freshest_clearance_is_authorized_first(self):
        self.ledger.record(clearance("s-old", when=NOW), now=NOW)
        self.ledger.record(clearance("s-new", when=NOW + timedelta(seconds=1)),
                           now=NOW + timedelta(seconds=1))
        self.ledger.authorize("pinky-1", now=NOW + timedelta(seconds=2))
        row = self.ledger.read("s-new", source_id="operator-1")
        self.assertEqual(row["state"], "AUTHORIZED")
        self.assertEqual(self.ledger.read("s-old", source_id="operator-1")["state"], "CLEARANCE")

    def test_restart_keeps_consumed_state(self):
        self.ledger.record(clearance(), now=NOW)
        self.ledger.authorize("pinky-1", now=NOW)
        reopened = DepartureLedger(self.path, clearance_sources=SOURCES)
        with self.assertRaises(ValueError):
            reopened.authorize("pinky-1", now=NOW + timedelta(seconds=1))

    def test_constructor_validates_configuration(self):
        path = Path(self._tmp.name) / "other.sqlite3"
        with self.assertRaises(ValueError):
            DepartureLedger(path, clearance_sources=())
        with self.assertRaises(ValueError):
            DepartureLedger(path, clearance_sources=SOURCES, freshness=timedelta(0))


if __name__ == "__main__":
    unittest.main()
