"""Immutable Python 3.10+ typed models and strict parsers for read-only ROSY Fleet snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Literal

from harbor.model import identifier

FleetRole = Literal['service', 'operator', 'viewer', 'admin']
_ROLES = frozenset({'service', 'operator', 'viewer', 'admin'})


def _clean_text(value: Any, *, max_len: int = 192, allow_empty: bool = False, label: str = 'text') -> str:
    if not isinstance(value, str):
        raise ValueError(f'INVALID_FLEET_FIELD: {label} must be a string')
    text = value.strip()
    if (not allow_empty and not text) or len(text) > max_len or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError(f'INVALID_FLEET_FIELD: {label}')
    return text


def _req_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f'INVALID_FLEET_FIELD: {label} must be bool')
    return value


def _req_int(value: Any, *, minimum: int = 0, label: str) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'INVALID_FLEET_FIELD: {label} must be int >= {minimum}')
    return value


@dataclass(frozen=True)
class FleetSession:
    principal_id: str
    role: FleetRole
    estop: bool | None = None

    def __post_init__(self):
        object.__setattr__(self, 'principal_id', _clean_text(self.principal_id, max_len=128, label='principal_id'))
        if self.role not in _ROLES:
            raise ValueError('INVALID_FLEET_ROLE')
        if self.estop is not None:
            _req_bool(self.estop, label='estop')


@dataclass(frozen=True)
class ExecutionProfile:
    profile: str
    arm_execution: str
    observation_producers: tuple[str, ...]
    evidence_producers: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, 'profile', _clean_text(self.profile, max_len=64, label='profile'))
        object.__setattr__(self, 'arm_execution', _clean_text(self.arm_execution, max_len=64, allow_empty=True, label='arm_execution'))


@dataclass(frozen=True)
class WorkcellCamera:
    source_id: str
    label: str = ''
    role: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'source_id', _clean_text(self.source_id, max_len=96, label='source_id'))
        object.__setattr__(self, 'label', _clean_text(self.label, max_len=120, allow_empty=True, label='label'))
        object.__setattr__(self, 'role', _clean_text(self.role, max_len=64, allow_empty=True, label='role'))


@dataclass(frozen=True)
class WorkcellArmIncident:
    workcell_id: str
    arm_id: str
    incident_id: str
    state: str
    reason: str
    held: bool

    def __post_init__(self):
        object.__setattr__(self, 'workcell_id', _clean_text(self.workcell_id, max_len=96, label='workcell_id'))
        object.__setattr__(self, 'arm_id', _clean_text(self.arm_id, max_len=96, label='arm_id'))
        object.__setattr__(self, 'incident_id', _clean_text(self.incident_id, max_len=64, allow_empty=True, label='incident_id'))
        object.__setattr__(self, 'state', _clean_text(self.state, max_len=32, allow_empty=True, label='state'))
        object.__setattr__(self, 'reason', _clean_text(self.reason, max_len=192, label='reason'))
        _req_bool(self.held, label='held')


@dataclass(frozen=True)
class WorkcellObservationSnapshot:
    read_only: bool
    cameras: tuple[WorkcellCamera, ...]
    open_incident_holds: tuple[WorkcellArmIncident, ...]

    def __post_init__(self):
        _req_bool(self.read_only, label='read_only')


@dataclass(frozen=True)
class PortEventRecord:
    event_id: str
    kind: Literal['container.received_at_intake', 'container.stored', 'container.handling_failed', 'vehicle.release_permitted']
    observation_ref: str
    observed_at: datetime
    object_ref: str | None = None
    intake_point: str | None = None
    slot_ref: str | None = None
    vehicle_id: str | None = None
    reason: str | None = None
    identity: str | None = None


@dataclass(frozen=True)
class PortEventPageRecord:
    events: tuple[PortEventRecord, ...]
    cursor: str
    complete: bool

    def __post_init__(self):
        if not isinstance(self.cursor, str):
            raise ValueError('INVALID_PORT_EVENT_CURSOR')
        _req_bool(self.complete, label='complete')


@dataclass(frozen=True)
class CellSiteZone:
    zone_id: str
    role: str
    status: str

    def __post_init__(self):
        identifier(self.zone_id)
        object.__setattr__(self, 'role', _clean_text(self.role, max_len=32, label='zone.role'))
        object.__setattr__(self, 'status', _clean_text(self.status, max_len=32, label='zone.status'))


@dataclass(frozen=True)
class CellSiteSlot:
    site_id: str
    wire_ref: str
    zone_id: str
    row: int
    column: int
    tier: int
    arms: tuple[str, ...]
    status: str

    def __post_init__(self):
        object.__setattr__(self, 'site_id', _clean_text(self.site_id, max_len=96, label='site_id'))
        object.__setattr__(self, 'wire_ref', _clean_text(self.wire_ref, max_len=96, label='wire_ref'))
        identifier(self.zone_id)
        _req_int(self.row, minimum=1, label='row')
        _req_int(self.column, minimum=1, label='column')
        _req_int(self.tier, minimum=1, label='tier')
        object.__setattr__(self, 'status', _clean_text(self.status, max_len=32, label='site.status'))


@dataclass(frozen=True)
class CellSiteMapSnapshot:
    schema: Literal['rosy.cell-site-map.v1']
    input_format: str
    digest: str
    status: str
    zones: tuple[CellSiteZone, ...]
    sites: tuple[CellSiteSlot, ...]

    def __post_init__(self):
        if self.schema != 'rosy.cell-site-map.v1':
            raise ValueError('INVALID_CELL_SITE_MAP_SCHEMA')
        object.__setattr__(self, 'input_format', _clean_text(self.input_format, max_len=32, label='input_format'))
        object.__setattr__(self, 'digest', _clean_text(self.digest, max_len=64, label='digest'))
        object.__setattr__(self, 'status', _clean_text(self.status, max_len=32, label='status'))


@dataclass(frozen=True)
class SiteRecognitionEntry:
    site_ref: str
    state: Literal['EMPTY', 'OCCUPIED', 'UNREADABLE']
    object_class: str | None
    object_ref_read: str | None
    confidence: float

    def __post_init__(self):
        object.__setattr__(self, 'site_ref', _clean_text(self.site_ref, max_len=96, label='site_ref'))
        if self.state not in ('EMPTY', 'OCCUPIED', 'UNREADABLE'):
            raise ValueError('INVALID_RECOGNITION_STATE')
        if not isinstance(self.confidence, (int, float)) or type(self.confidence) is bool or not math.isfinite(self.confidence):
            raise ValueError('INVALID_RECOGNITION_CONFIDENCE')


@dataclass(frozen=True)
class SiteRecognitionSnapshot:
    schema: str
    snapshot_id: str
    observed_at: str
    source_id: str
    calibration_revision: str
    site_map_revision: int
    entries: tuple[SiteRecognitionEntry, ...]


def parse_fleet_session(raw: Any) -> FleetSession:
    if not isinstance(raw, dict):
        raise ValueError('INVALID_FLEET_SESSION')
    return FleetSession(
        principal_id=raw.get('principal_id'),
        role=raw.get('role'),
        estop=raw.get('estop'),
    )


def parse_execution_profile(raw: Any) -> ExecutionProfile:
    if not isinstance(raw, dict):
        raise ValueError('INVALID_EXECUTION_PROFILE')
    obs = [] if raw.get('observation_producers') is None else raw.get('observation_producers')
    ev = [] if raw.get('evidence_producers') is None else raw.get('evidence_producers')
    if not isinstance(obs, list) or not isinstance(ev, list):
        raise ValueError('INVALID_EXECUTION_PROFILE')
    obs_names = tuple(_clean_text(item.get('id') if isinstance(item, dict) else item, max_len=96, label='producer') for item in obs)
    ev_names = tuple(_clean_text(item.get('id') if isinstance(item, dict) else item, max_len=96, label='producer') for item in ev)
    return ExecutionProfile(
        profile=raw.get('profile'),
        arm_execution=raw.get('arm_execution') or '',
        observation_producers=obs_names,
        evidence_producers=ev_names,
    )


def parse_workcell_observations(raw: Any) -> WorkcellObservationSnapshot:
    if not isinstance(raw, dict) or not isinstance(raw.get('workcells'), list):
        raise ValueError('INVALID_WORKCELL_OBSERVATIONS')
    read_only = raw.get('read_only', True)
    _req_bool(read_only, label='read_only')
    cameras: list[WorkcellCamera] = []
    incidents: list[WorkcellArmIncident] = []
    for cell in raw['workcells']:
        if not isinstance(cell, dict) or not isinstance(cell.get('arms'), list):
            raise ValueError('INVALID_WORKCELL_OBSERVATIONS')
        cell_id = _clean_text(cell.get('workcell_id') or '', max_len=96, allow_empty=True, label='workcell_id') or 'cell'
        for cam in cell.get('cameras') or ():
            if not isinstance(cam, dict):
                raise ValueError('INVALID_WORKCELL_CAMERA')
            if cam.get('source_id'):
                cameras.append(WorkcellCamera(
                    source_id=cam['source_id'],
                    label=str(cam.get('label') or ''),
                    role=str(cam.get('role') or ''),
                ))
        for arm in cell['arms']:
            if not isinstance(arm, dict):
                raise ValueError('INVALID_WORKCELL_ARM')
            arm_id = _clean_text(arm.get('arm_id') or arm.get('source_id') or 'arm', max_len=96, label='arm_id')
            raw_incidents = arm.get('incidents') or ()
            if not isinstance(raw_incidents, list):
                raise ValueError('INVALID_WORKCELL_INCIDENTS')
            for inc in raw_incidents:
                if not isinstance(inc, dict):
                    raise ValueError('INVALID_WORKCELL_INCIDENT')
                if inc.get('state') == 'cleared':
                    continue
                decision = inc.get('decision')
                held = not isinstance(decision, dict) or decision.get('hold') is not False
                if held:
                    reason = _clean_text((decision or {}).get('reason') if isinstance(decision, dict) else 'arm incident not cleared', max_len=192, allow_empty=True, label='reason') or 'arm incident not cleared'
                    incidents.append(WorkcellArmIncident(
                        workcell_id=cell_id,
                        arm_id=arm_id,
                        incident_id=str(inc.get('incident_id') or ''),
                        state=str(inc.get('state') or 'held'),
                        reason=reason,
                        held=True,
                    ))
    return WorkcellObservationSnapshot(
        read_only=read_only,
        cameras=tuple(cameras),
        open_incident_holds=tuple(incidents),
    )


def parse_port_event_page(raw: Any) -> PortEventPageRecord:
    from harbor.port_events import parse_event

    if not isinstance(raw, dict) or set(raw) != {'events', 'cursor', 'complete'} or not isinstance(raw['events'], list):
        raise ValueError('INVALID_PORT_EVENT_PAGE')
    parsed = tuple(
        PortEventRecord(**parse_event(entry))
        for entry in raw['events']
    )
    return PortEventPageRecord(events=parsed, cursor=raw['cursor'], complete=raw['complete'])


def parse_cell_site_map(raw: Any) -> CellSiteMapSnapshot:
    from harbor.cell_site_map import normalize_cell_site_map

    normalized = normalize_cell_site_map(raw)
    zones = tuple(
        CellSiteZone(zone_id=z['id'], role=z['role'], status=z['status'])
        for z in normalized['zones']
    )
    sites = tuple(
        CellSiteSlot(
            site_id=s['site_id'],
            wire_ref=s['wire_ref'],
            zone_id=s['area'],
            row=s['row'],
            column=s['start_col'],
            tier=s['tier'],
            arms=tuple(s['arms']),
            status=s['status'],
        )
        for s in normalized['sites']
    )
    return CellSiteMapSnapshot(
        schema=normalized['schema'],
        input_format=normalized['input_format'],
        digest=normalized['digest'],
        status=normalized['status'],
        zones=zones,
        sites=sites,
    )


def parse_site_recognition_snapshot(raw: Any, *, known_site_refs=None) -> SiteRecognitionSnapshot:
    from harbor.site_recognition import parse_site_recognition

    parsed = parse_site_recognition(raw, known_site_refs=known_site_refs)
    entries = tuple(
        SiteRecognitionEntry(
            site_ref=item['site_ref'],
            state=item['state'],
            object_class=item.get('object_class'),
            object_ref_read=item.get('object_ref_read'),
            confidence=float(item['confidence']),
        )
        for item in parsed['sites']
    )
    return SiteRecognitionSnapshot(
        schema=parsed['schema'],
        snapshot_id=parsed['snapshot_id'],
        observed_at=parsed['observed_at'],
        source_id=parsed['source_id'],
        calibration_revision=parsed['calibration_revision'],
        site_map_revision=parsed['site_map_revision'],
        entries=entries,
    )
