"""HarborLogi domain-pure view of ROSY site points, vehicle stops, and handoff bridges.

Consumes the geometry-free ``rosy.port-site-places.v1`` contract (either from Fleet
``GET /api/fleet/site-points?view=port`` or a static site profile) and strictly rejects
platform geometry, lane polylines, hardware ids, marker ids, or legacy station aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from harbor.model import identifier, identifiers
from harbor.site import BUSINESS_LOCATIONS

SCHEMA = "rosy.port-site-places.v1"

VEHICLE_PLACE_ROLES = frozenset({
    "inbound_load",
    "intake_dropoff",
    "outbound_pickup",
    "outbound_exit",
    "wait",
    "charge",
})
CELL_ZONE_ROLES = frozenset({"storage", "temporary", "outbound"})
PLACE_STATUSES = frozenset({"verified", "planned_unverified"})

FORBIDDEN_KEYS = frozenset({
    "x",
    "y",
    "yaw",
    "pose",
    "edges",
    "edges_in",
    "edges_out",
    "polyline",
    "stop_zone",
    "approach_m",
    "approach_pose",
    "pinned",
    "width_m",
    "speed_cap_mps",
    "turn_bans",
    "drive_mode",
    "junction",
    "workcell_id",
    "instance_id",
    "marker_id",
    "arm_fk",
    "arms",
    "physical",
    "item",
    "aliases",
    "legacy_aliases",
})

LEGACY_ALIAS_IDS = frozenset({
    "EXP_S1",
    "EXP_S5_PALLET",
    "EXP_S3_BELT",
    "STG_C2",
    "STG_S3_WAIT",
})

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_WIRE_SLOT_RE = re.compile(r"^[a-z0-9_]+-r[1-9][0-9]*-c[1-9][0-9]*-l[1-9][0-9]*$")
MAX_SITE_PLACES_BYTES = 1024 * 1024


def _reject_forbidden_tree(node) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS:
                raise ValueError(f"Forbidden platform geometry or device field in site places: {key!r}")
            _reject_forbidden_tree(value)
    elif isinstance(node, list):
        for item in node:
            _reject_forbidden_tree(item)


def _place_ref(value: str, label: str) -> str:
    identifier(value)
    if len(value) > 64 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {label} reference")
    if value in LEGACY_ALIAS_IDS:
        raise ValueError(f"Legacy ROSY alias is not allowed in site places: {value!r}")
    return value


def _check_status(value: object, label: str) -> str:
    if not isinstance(value, str) or value not in PLACE_STATUSES:
        raise ValueError(f"Invalid {label} status")
    return value


def _require_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"Expected boolean for {label}")
    return value


@dataclass(frozen=True)
class VehiclePlace:
    place_id: str
    port_role: str
    status: str
    dispatchable: bool
    label: str = ""

    def __post_init__(self):
        _place_ref(self.place_id, "vehicle place_id")
        if self.port_role not in VEHICLE_PLACE_ROLES:
            raise ValueError(f"Unknown vehicle port_role: {self.port_role!r}")
        _check_status(self.status, "vehicle place")
        _require_bool(self.dispatchable, "vehicle place dispatchable")
        if not isinstance(self.label, str):
            raise ValueError("Expected string vehicle place label")
        if self.port_role == "charge" and self.dispatchable:
            raise ValueError("Charge bays are never Harbor trip destinations")
        if self.status != "verified" and self.dispatchable:
            raise ValueError("Unverified vehicle place cannot be dispatchable")


@dataclass(frozen=True)
class CellZoneRef:
    zone_id: str
    role: str
    status: str
    label: str = ""

    def __post_init__(self):
        _place_ref(self.zone_id, "cell zone_id")
        if self.role not in CELL_ZONE_ROLES:
            raise ValueError(f"Unknown cell zone role: {self.role!r}")
        _check_status(self.status, "cell zone")
        if not isinstance(self.label, str):
            raise ValueError("Expected string cell zone label")


@dataclass(frozen=True)
class IntakeHandoff:
    handoff_id: str
    vehicle_stop_id: str
    target_cell_zones: tuple[str, ...]
    status: str
    dispatchable: bool
    intake_point_ref: str = ""

    def __post_init__(self):
        _place_ref(self.handoff_id, "intake handoff_id")
        _place_ref(self.vehicle_stop_id, "intake vehicle_stop_id")
        if not self.target_cell_zones:
            raise ValueError("Expected at least one target cell zone")
        identifiers(self.target_cell_zones)
        for z in self.target_cell_zones:
            _place_ref(z, "intake target_cell_zone")
        _check_status(self.status, "intake handoff")
        _require_bool(self.dispatchable, "intake handoff dispatchable")
        if not isinstance(self.intake_point_ref, str):
            raise ValueError("Expected string intake_point_ref")
        if self.status != "verified" and self.dispatchable:
            raise ValueError("Unverified intake handoff cannot be dispatchable")


@dataclass(frozen=True)
class OutboundHandoff:
    handoff_id: str
    cell_zone_id: str
    wire_slot_ref: str
    vehicle_pickup_stop_id: str | None
    vehicle_exit_stop_id: str | None
    status: str
    dispatchable: bool

    def __post_init__(self):
        _place_ref(self.handoff_id, "outbound handoff_id")
        _place_ref(self.cell_zone_id, "outbound cell_zone_id")
        if not isinstance(self.wire_slot_ref, str) or not _WIRE_SLOT_RE.fullmatch(self.wire_slot_ref):
            raise ValueError("Invalid outbound wire_slot_ref")
        if self.vehicle_pickup_stop_id is not None:
            _place_ref(self.vehicle_pickup_stop_id, "outbound vehicle_pickup_stop_id")
        if self.vehicle_exit_stop_id is not None:
            _place_ref(self.vehicle_exit_stop_id, "outbound vehicle_exit_stop_id")
        _check_status(self.status, "outbound handoff")
        _require_bool(self.dispatchable, "outbound handoff dispatchable")
        if self.status != "verified" and self.dispatchable:
            raise ValueError("Unverified outbound handoff cannot be dispatchable")


@dataclass(frozen=True)
class SitePlaces:
    schema: str
    digest: str
    sources: dict
    vehicle_places: tuple[VehiclePlace, ...]
    cell_zones: tuple[CellZoneRef, ...]
    intake_handoffs: tuple[IntakeHandoff, ...]
    outbound_handoffs: tuple[OutboundHandoff, ...]
    not_places: frozenset[str]

    def vehicle_place(self, place_id: str) -> VehiclePlace | None:
        return next((p for p in self.vehicle_places if p.place_id == place_id), None)

    def cell_zone(self, zone_id: str) -> CellZoneRef | None:
        return next((z for z in self.cell_zones if z.zone_id == zone_id), None)


def parse_site_places(data: dict) -> SitePlaces:
    if not isinstance(data, dict):
        raise ValueError("Expected a site places object")
    _reject_forbidden_tree(data)
    expected = {
        "schema",
        "sources",
        "digest",
        "vehicle_places",
        "cell_zones",
        "intake_handoffs",
        "outbound_handoffs",
        "not_places",
    }
    if set(data) != expected:
        raise ValueError("Unknown or missing site places fields")
    if data["schema"] != SCHEMA:
        raise ValueError(f"Unsupported site places schema: {data['schema']!r}")
    digest = data["digest"]
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ValueError("Expected a 64-hex SHA-256 digest")
    sources = data["sources"]
    if not isinstance(sources, dict) or not {"site_map", "cell_site_map"} <= set(sources):
        raise ValueError("Invalid site places sources")

    not_places_raw = data["not_places"]
    if not isinstance(not_places_raw, list) or not all(isinstance(v, str) for v in not_places_raw):
        raise ValueError("Expected not_places identifier list")
    for np_id in not_places_raw:
        identifier(np_id)
    not_places = frozenset(not_places_raw)

    if not isinstance(data["vehicle_places"], list) or not data["vehicle_places"]:
        raise ValueError("Expected non-empty vehicle_places list")
    v_places = []
    seen_vp: set[str] = set()
    for row in data["vehicle_places"]:
        if not isinstance(row, dict) or not set(row) <= {"place_id", "port_role", "status", "dispatchable", "label"}:
            raise ValueError("Invalid vehicle_place entry")
        if row.get("place_id") in not_places:
            raise ValueError(f"not_places entry cannot appear in vehicle_places: {row.get('place_id')!r}")
        vp = VehiclePlace(
            place_id=row["place_id"],
            port_role=row["port_role"],
            status=row["status"],
            dispatchable=row["dispatchable"],
            label=row.get("label", ""),
        )
        if vp.place_id in seen_vp:
            raise ValueError(f"Duplicate vehicle place_id: {vp.place_id!r}")
        seen_vp.add(vp.place_id)
        v_places.append(vp)

    if not isinstance(data["cell_zones"], list) or not data["cell_zones"]:
        raise ValueError("Expected non-empty cell_zones list")
    c_zones = []
    seen_cz: set[str] = set()
    for row in data["cell_zones"]:
        if not isinstance(row, dict) or not set(row) <= {"zone_id", "role", "status", "label"}:
            raise ValueError("Invalid cell_zone entry")
        if row.get("zone_id") in not_places:
            raise ValueError(f"not_places entry cannot appear in cell_zones: {row.get('zone_id')!r}")
        cz = CellZoneRef(
            zone_id=row["zone_id"],
            role=row["role"],
            status=row["status"],
            label=row.get("label", ""),
        )
        if cz.zone_id in seen_cz:
            raise ValueError(f"Duplicate cell zone_id: {cz.zone_id!r}")
        seen_cz.add(cz.zone_id)
        c_zones.append(cz)

    vp_by_id = {p.place_id: p for p in v_places}
    cz_by_id = {z.zone_id: z for z in c_zones}

    if not isinstance(data["intake_handoffs"], list):
        raise ValueError("Expected intake_handoffs list")
    i_handoffs = []
    for row in data["intake_handoffs"]:
        if not isinstance(row, dict) or not set(row) <= {
            "handoff_id",
            "vehicle_stop_id",
            "intake_point_ref",
            "target_cell_zones",
            "status",
            "dispatchable",
        }:
            raise ValueError("Invalid intake_handoff entry")
        targets = row["target_cell_zones"]
        if not isinstance(targets, list):
            raise ValueError("Expected target_cell_zones list")
        ih = IntakeHandoff(
            handoff_id=row["handoff_id"],
            vehicle_stop_id=row["vehicle_stop_id"],
            target_cell_zones=tuple(targets),
            status=row["status"],
            dispatchable=row["dispatchable"],
            intake_point_ref=row.get("intake_point_ref", ""),
        )
        if ih.vehicle_stop_id in not_places:
            raise ValueError("Intake handoff vehicle_stop_id cannot reference not_places")
        if any(z in not_places for z in ih.target_cell_zones):
            raise ValueError("Intake handoff target_cell_zones cannot reference not_places")
        if ih.status == "verified":
            stop = vp_by_id.get(ih.vehicle_stop_id)
            if stop is None or stop.status != "verified" or not stop.dispatchable or stop.port_role != "intake_dropoff":
                raise ValueError("Verified intake handoff requires a verified intake_dropoff vehicle stop")
            for zid in ih.target_cell_zones:
                cz = cz_by_id.get(zid)
                if cz is None or cz.status != "verified":
                    raise ValueError(f"Verified intake handoff requires verified cell zone: {zid!r}")
        i_handoffs.append(ih)

    if not isinstance(data["outbound_handoffs"], list):
        raise ValueError("Expected outbound_handoffs list")
    o_handoffs = []
    for row in data["outbound_handoffs"]:
        if not isinstance(row, dict) or not set(row) <= {
            "handoff_id",
            "cell_zone_id",
            "wire_slot_ref",
            "vehicle_pickup_stop_id",
            "vehicle_exit_stop_id",
            "status",
            "dispatchable",
        }:
            raise ValueError("Invalid outbound_handoff entry")
        oh = OutboundHandoff(
            handoff_id=row["handoff_id"],
            cell_zone_id=row["cell_zone_id"],
            wire_slot_ref=row["wire_slot_ref"],
            vehicle_pickup_stop_id=row.get("vehicle_pickup_stop_id"),
            vehicle_exit_stop_id=row.get("vehicle_exit_stop_id"),
            status=row["status"],
            dispatchable=row["dispatchable"],
        )
        if oh.cell_zone_id in not_places:
            raise ValueError("Outbound handoff cell_zone_id cannot reference not_places")
        if oh.vehicle_pickup_stop_id in not_places or oh.vehicle_exit_stop_id in not_places:
            raise ValueError("Outbound handoff vehicle stop cannot reference not_places")
        if oh.status == "verified":
            cz = cz_by_id.get(oh.cell_zone_id)
            if cz is None or cz.status != "verified" or cz.role != "outbound":
                raise ValueError(f"Verified outbound handoff requires verified outbound cell zone: {oh.cell_zone_id!r}")
            pickup = vp_by_id.get(oh.vehicle_pickup_stop_id or "")
            if (
                pickup is None
                or pickup.status != "verified"
                or not pickup.dispatchable
                or pickup.port_role != "outbound_pickup"
            ):
                raise ValueError("Verified outbound handoff requires verified outbound_pickup vehicle stop")
            exit_stop = vp_by_id.get(oh.vehicle_exit_stop_id or "")
            if (
                exit_stop is None
                or exit_stop.status != "verified"
                or not exit_stop.dispatchable
                or exit_stop.port_role != "outbound_exit"
            ):
                raise ValueError("Verified outbound handoff requires dedicated verified outbound_exit vehicle stop")
        o_handoffs.append(oh)

    return SitePlaces(
        schema=data["schema"],
        digest=digest,
        sources=dict(sources),
        vehicle_places=tuple(v_places),
        cell_zones=tuple(c_zones),
        intake_handoffs=tuple(i_handoffs),
        outbound_handoffs=tuple(o_handoffs),
        not_places=not_places,
    )


def load_site_places(path: str | Path) -> SitePlaces:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_SITE_PLACES_BYTES:
        raise ValueError("Site places profile exceeds 1 MiB")

    def _no_duplicate_keys(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError(f"Duplicate JSON key: {k!r}")
            obj[k] = v
        return obj

    def _reject_bad_const(val):
        raise ValueError(f"Non-finite JSON constant: {val!r}")

    parsed = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_bad_const,
    )
    return parse_site_places(parsed)


def resolve_intake_destination(places: SitePlaces, handoff_id: str = "s3_cell_s_unload") -> tuple[str | None, str]:
    handoff = next((h for h in places.intake_handoffs if h.handoff_id == handoff_id), None)
    if handoff is None:
        return None, "INTAKE_HANDOFF_UNCONFIGURED"
    stop = places.vehicle_place(handoff.vehicle_stop_id)
    if stop is None or stop.port_role != "intake_dropoff" or stop.status != "verified" or not stop.dispatchable:
        return None, "INTAKE_STOP_UNVERIFIED"
    if any((z := places.cell_zone(zid)) is None or z.status != "verified" for zid in handoff.target_cell_zones):
        return None, "INTAKE_ZONE_UNVERIFIED"
    if handoff.status != "verified" or not handoff.dispatchable:
        return None, "INTAKE_HANDOFF_UNVERIFIED"
    return stop.place_id, "OK"


def resolve_outbound_dispatch_stops(
    places: SitePlaces,
    stand_zone_id: str = "stand",
) -> tuple[tuple[str, str] | None, str]:
    handoff = next((h for h in places.outbound_handoffs if h.cell_zone_id == stand_zone_id), None)
    if handoff is None:
        return None, "OUTBOUND_HANDOFF_UNCONFIGURED"
    zone = places.cell_zone(handoff.cell_zone_id)
    if zone is None or zone.role != "outbound" or zone.status != "verified":
        return None, "OUTBOUND_ZONE_UNVERIFIED"
    if not handoff.vehicle_pickup_stop_id:
        return None, "OUTBOUND_PICKUP_STOP_UNCONFIGURED"
    pickup = places.vehicle_place(handoff.vehicle_pickup_stop_id)
    if pickup is None or pickup.port_role != "outbound_pickup" or pickup.status != "verified" or not pickup.dispatchable:
        return None, "OUTBOUND_PICKUP_STOP_UNVERIFIED"
    if not handoff.vehicle_exit_stop_id:
        return None, "OUTBOUND_EXIT_STOP_UNCONFIGURED"
    exit_stop = places.vehicle_place(handoff.vehicle_exit_stop_id)
    if (
        exit_stop is None
        or exit_stop.port_role != "outbound_exit"
        or exit_stop.status != "verified"
        or not exit_stop.dispatchable
    ):
        return None, "OUTBOUND_EXIT_STOP_UNVERIFIED"
    if handoff.status != "verified" or not handoff.dispatchable:
        return None, "OUTBOUND_HANDOFF_UNVERIFIED"
    return (pickup.place_id, exit_stop.place_id), "OK"


def validate_trip_places(places: SitePlaces, destination: str, via: tuple[str, ...] = ()) -> None:
    if destination in places.not_places or destination in LEGACY_ALIAS_IDS:
        raise ValueError(f"Destination is a non-place or legacy alias: {destination!r}")
    dest = places.vehicle_place(destination)
    if dest is None:
        raise ValueError(f"Unknown destination vehicle place: {destination!r}")
    if dest.port_role == "charge":
        raise ValueError(f"Charge bay cannot be a Harbor trip destination: {destination!r}")
    if dest.status != "verified" or not dest.dispatchable:
        raise ValueError(f"Destination vehicle place is unverified or not dispatchable: {destination!r}")
    for step in via:
        if step in places.not_places or step in LEGACY_ALIAS_IDS:
            raise ValueError(f"Via place is a non-place or legacy alias: {step!r}")
        vp = places.vehicle_place(step)
        if vp is None or vp.port_role != "wait" or vp.status != "verified" or not vp.dispatchable:
            raise ValueError(f"Via stop must be a verified wait place: {step!r}")


def business_location_for(places: SitePlaces, ref: str) -> str | None:
    if ref in places.not_places:
        return None
    if (vp := places.vehicle_place(ref)) is not None:
        if vp.port_role in ("inbound_load", "outbound_exit"):
            loc = "vessel"
            assert loc in BUSINESS_LOCATIONS
            return loc
        return None
    if (cz := places.cell_zone(ref)) is not None:
        if cz.role in ("storage", "temporary"):
            return "yard"
        if cz.role == "outbound":
            return "outbound_point"
    raise ValueError(f"Unknown place or zone reference: {ref!r}")
