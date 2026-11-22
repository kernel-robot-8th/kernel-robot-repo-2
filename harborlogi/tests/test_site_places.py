import copy
import json
from pathlib import Path
import tempfile
import unittest

from harbor.site_places import (
    business_location_for,
    load_site_places,
    parse_site_places,
    resolve_intake_destination,
    resolve_outbound_dispatch_stops,
    validate_trip_places,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "site-places.example.json"


def _verified_payload() -> dict:
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for p in doc["vehicle_places"]:
        if p["place_id"] == "S_OUT":
            p["status"] = "verified"
            p["dispatchable"] = True
    doc["vehicle_places"].append({
        "place_id": "S_EXIT",
        "port_role": "outbound_exit",
        "status": "verified",
        "dispatchable": True,
        "label": "반출 전용 출구",
    })
    for z in doc["cell_zones"]:
        z["status"] = "verified"
    doc["intake_handoffs"][0]["status"] = "verified"
    doc["intake_handoffs"][0]["dispatchable"] = True
    doc["outbound_handoffs"][0]["vehicle_exit_stop_id"] = "S_EXIT"
    doc["outbound_handoffs"][0]["status"] = "verified"
    doc["outbound_handoffs"][0]["dispatchable"] = True
    return doc


class SitePlacesTests(unittest.TestCase):
    def test_example_config_parses_and_fails_closed(self):
        places = load_site_places(CONFIG_PATH)
        self.assertEqual(places.schema, "rosy.port-site-places.v1")
        intake_stop, intake_reason = resolve_intake_destination(places)
        self.assertIsNone(intake_stop)
        self.assertEqual(intake_reason, "INTAKE_ZONE_UNVERIFIED")

        outbound_stops, outbound_reason = resolve_outbound_dispatch_stops(places)
        self.assertIsNone(outbound_stops)
        self.assertEqual(outbound_reason, "OUTBOUND_ZONE_UNVERIFIED")

        with self.assertRaises(ValueError):
            validate_trip_places(places, "S_OUT")
        with self.assertRaises(ValueError):
            validate_trip_places(places, "C1")
        with self.assertRaises(ValueError):
            validate_trip_places(places, "belt")

    def test_verified_payload_resolves_inbound_and_outbound_two_leg(self):
        places = parse_site_places(_verified_payload())
        self.assertEqual(resolve_intake_destination(places), ("S3", "OK"))
        self.assertEqual(resolve_outbound_dispatch_stops(places), (("S_OUT", "S_EXIT"), "OK"))
        validate_trip_places(places, "S_OUT", ("S2", "S4"))
        validate_trip_places(places, "S_EXIT")
        with self.assertRaises(ValueError):
            validate_trip_places(places, "S1", ("S3",))  # S3 is not a wait stop

        self.assertEqual(business_location_for(places, "S1"), "vessel")
        self.assertEqual(business_location_for(places, "S_EXIT"), "vessel")
        self.assertEqual(business_location_for(places, "yard"), "yard")
        self.assertEqual(business_location_for(places, "temp_a"), "yard")
        self.assertEqual(business_location_for(places, "stand"), "outbound_point")
        self.assertIsNone(business_location_for(places, "S3"))
        self.assertIsNone(business_location_for(places, "belt"))

    def test_rejects_geometry_device_ids_and_legacy_aliases(self):
        base = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for key, value in [
            ("edges", []),
            ("polyline", []),
            ("workcell_id", "cell_ab"),
            ("marker_id", 100),
            ("aliases", {"EXP_S1": "stand:r1-s1"}),
        ]:
            bad = copy.deepcopy(base)
            bad[key] = value
            with self.assertRaises(ValueError):
                parse_site_places(bad)

        bad_coord = copy.deepcopy(base)
        bad_coord["vehicle_places"][0]["x"] = 0.5
        with self.assertRaises(ValueError):
            parse_site_places(bad_coord)

        bad_alias = copy.deepcopy(base)
        bad_alias["vehicle_places"].append({
            "place_id": "EXP_S1",
            "port_role": "wait",
            "status": "verified",
            "dispatchable": True,
        })
        with self.assertRaises(ValueError):
            parse_site_places(bad_alias)

        bad_np = copy.deepcopy(base)
        bad_np["vehicle_places"].append({
            "place_id": "belt",
            "port_role": "wait",
            "status": "verified",
            "dispatchable": True,
        })
        with self.assertRaises(ValueError):
            parse_site_places(bad_np)

    def test_rejects_verified_handoff_over_unverified_stop_or_shared_exit(self):
        bad_s1_exit = _verified_payload()
        bad_s1_exit["outbound_handoffs"][0]["vehicle_exit_stop_id"] = "S1"
        with self.assertRaises(ValueError):
            parse_site_places(bad_s1_exit)

        bad_unverified_pickup = _verified_payload()
        for p in bad_unverified_pickup["vehicle_places"]:
            if p["place_id"] == "S_OUT":
                p["status"] = "planned_unverified"
                p["dispatchable"] = False
        with self.assertRaises(ValueError):
            parse_site_places(bad_unverified_pickup)

    def test_supports_adr_0030_multiple_outbound_stands_partially_verified(self):
        doc = _verified_payload()
        doc["cell_zones"].append({"zone_id": "stand_b", "role": "outbound", "status": "planned_unverified"})
        doc["outbound_handoffs"].append({
            "handoff_id": "outbound_stand_b_load",
            "cell_zone_id": "stand_b",
            "wire_slot_ref": "stand_b-r1-c1-l1",
            "vehicle_pickup_stop_id": None,
            "vehicle_exit_stop_id": "S_EXIT",
            "status": "planned_unverified",
            "dispatchable": False,
        })
        places = parse_site_places(doc)
        self.assertEqual(resolve_outbound_dispatch_stops(places, "stand"), (("S_OUT", "S_EXIT"), "OK"))
        self.assertEqual(resolve_outbound_dispatch_stops(places, "stand_b"), (None, "OUTBOUND_ZONE_UNVERIFIED"))

    def test_load_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dup.json"
            p.write_text('{"schema":"rosy.port-site-places.v1","schema":"x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_site_places(p)


if __name__ == "__main__":
    unittest.main()
