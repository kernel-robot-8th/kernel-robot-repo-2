"""Loader and canonical ROSY-name validator for 3D camera-mapping site layout config."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any

from harbor.cell_site_map import CANONICAL_ZONE_SPECS, SUPPORTED_STATUSES
from harbor.site_places import LEGACY_ALIAS_IDS

SCHEMA_ID = 'harbor.site-3d-layout.v1'
DEFAULT_SITE_3D_LAYOUT_PATH = Path(__file__).resolve().parents[1] / 'config' / 'site-3d-layout.json'

CANONICAL_ARMS = frozenset({'arm_a', 'arm_b', 'arm_s'})
CANONICAL_STOPS = frozenset({
    'S1', 'S2', 'S3', 'S4', 'S5', 'S_OUT', 'C1', 'C2', 'C3', 'J_in', 'J_out',
})
CANONICAL_ZONE_LABEL_ZONES = frozenset(set(CANONICAL_ZONE_SPECS) | {'belt'})
CANONICAL_CAMERAS = ('top', 'side', 'bird', 'arms')
CANONICAL_CAMERA_SOURCES = frozenset({
    'top', 'omx_overhead',
    'side', 'omx_side',
    'ceiling_north',
    'arm_s_top', 'omx_arm_s_top',
})
CAMERA_VIEW_MODES = frozenset({'raw', 'map'})
_VEHICLE_ID_RE = re.compile(r'^rosy_[0-9]+$')


def _is_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value)


def _req_float(value: Any, name: str, *, positive: bool = False) -> float:
    if not _is_finite_number(value):
        raise ValueError(f'{name} must be a finite number')
    result = float(value)
    if positive and result <= 0:
        raise ValueError(f'{name} must be positive')
    return result


def _req_vec(value: Any, length: int, name: str, *, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f'{name} must be a {length}-element numeric list')
    return [_req_float(item, f'{name}[{idx}]', positive=positive) for idx, item in enumerate(value)]


def _req_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value.strip()


def _validate_plane(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('plane must be an object')
    x_min = _req_float(raw.get('x_min'), 'plane.x_min')
    x_max = _req_float(raw.get('x_max'), 'plane.x_max')
    y_min = _req_float(raw.get('y_min'), 'plane.y_min')
    y_max = _req_float(raw.get('y_max'), 'plane.y_max')
    if x_min >= x_max or y_min >= y_max:
        raise ValueError('plane bounds must satisfy x_min < x_max and y_min < y_max')
    ppm = _req_float(raw.get('pixels_per_meter'), 'plane.pixels_per_meter', positive=True)
    image = raw.get('image')
    if not isinstance(image, list) or len(image) != 2 or not all(type(v) is int and v > 0 for v in image):
        raise ValueError('plane.image must be [width, height] positive integers')
    return {
        'x_min': x_min,
        'x_max': x_max,
        'y_min': y_min,
        'y_max': y_max,
        'pixels_per_meter': ppm,
        'image': [image[0], image[1]],
    }


def _validate_arms(raw: Any) -> dict[str, list[float]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError('arms must be a non-empty object')
    out: dict[str, list[float]] = {}
    for arm_id, xy in raw.items():
        if not isinstance(arm_id, str) or arm_id not in CANONICAL_ARMS:
            raise ValueError(f'non-canonical arm name: {arm_id!r}')
        out[arm_id] = _req_vec(xy, 2, f'arms.{arm_id}')
    return out


def _validate_stops(raw: Any) -> dict[str, list[float]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError('stops must be a non-empty object')
    out: dict[str, list[float]] = {}
    for stop_id, xy in raw.items():
        if not isinstance(stop_id, str) or stop_id in LEGACY_ALIAS_IDS or stop_id not in CANONICAL_STOPS:
            raise ValueError(f'non-canonical stop name: {stop_id!r}')
        out[stop_id] = _req_vec(xy, 2, f'stops.{stop_id}')
    return out


def _validate_vehicle(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('vehicle must be an object')
    vid = _req_text(raw.get('id'), 'vehicle.id')
    if not _VEHICLE_ID_RE.fullmatch(vid):
        raise ValueError(f'non-canonical vehicle id: {vid!r}')
    stop = _req_text(raw.get('stop'), 'vehicle.stop')
    if stop in LEGACY_ALIAS_IDS or stop not in CANONICAL_STOPS:
        raise ValueError(f'non-canonical vehicle stop: {stop!r}')
    label = _req_text(raw.get('label'), 'vehicle.label')
    xy = _req_vec(raw.get('xy'), 2, 'vehicle.xy')
    return {'id': vid, 'label': label, 'stop': stop, 'xy': xy}


def _validate_plates(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError('plates must be a non-empty object')
    out: dict[str, dict[str, Any]] = {}
    for zone_id, plate in raw.items():
        if not isinstance(zone_id, str) or zone_id not in CANONICAL_ZONE_SPECS:
            raise ValueError(f'non-canonical plate zone: {zone_id!r}')
        if not isinstance(plate, dict):
            raise ValueError(f'plates.{zone_id} must be an object')
        spec = CANONICAL_ZONE_SPECS[zone_id]
        expected_rows = list(range(1, spec['rows'] + 1))
        expected_cols = list(spec['valid_cols'])
        rows = plate.get('rows')
        cols = plate.get('cols')
        tiers = plate.get('tiers')
        if rows != expected_rows:
            raise ValueError(f'plates.{zone_id}.rows must match canonical {expected_rows}')
        if cols != expected_cols:
            raise ValueError(f'plates.{zone_id}.cols must match canonical {expected_cols}')
        if type(tiers) is not int or tiers != spec['tiers']:
            raise ValueError(f'plates.{zone_id}.tiers must match canonical {spec["tiers"]}')
        u_by_row = _req_vec(plate.get('u_by_row'), len(expected_rows), f'plates.{zone_id}.u_by_row')
        v_by_slot = _req_vec(plate.get('v_by_slot'), len(expected_cols), f'plates.{zone_id}.v_by_slot')
        size_m = _req_vec(plate.get('size_m'), 3, f'plates.{zone_id}.size_m', positive=True)
        out[zone_id] = {
            'rows': expected_rows,
            'cols': expected_cols,
            'tiers': tiers,
            'u_by_row': u_by_row,
            'v_by_slot': v_by_slot,
            'size_m': size_m,
        }
    missing = set(CANONICAL_ZONE_SPECS) - set(out)
    if missing:
        raise ValueError(f'plates missing canonical zones: {sorted(missing)}')
    return out


def _validate_conveyor(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('conveyor must be an object')
    allowed_keys = {'body_uv', 'body_y_m', 'body_size_m', 'belt', 'belt-in'}
    extra = set(raw) - allowed_keys
    if extra:
        raise ValueError(f'non-canonical conveyor keys: {sorted(extra)}')
    belt = raw.get('belt')
    belt_in = raw.get('belt-in')
    if not isinstance(belt, dict) or not isinstance(belt_in, dict):
        raise ValueError('conveyor must define canonical belt and belt-in objects')
    return {
        'body_uv': _req_vec(raw.get('body_uv'), 2, 'conveyor.body_uv'),
        'body_y_m': _req_float(raw.get('body_y_m'), 'conveyor.body_y_m'),
        'body_size_m': _req_vec(raw.get('body_size_m'), 3, 'conveyor.body_size_m', positive=True),
        'belt': {
            'uv': _req_vec(belt.get('uv'), 2, 'conveyor.belt.uv'),
            'size_m': _req_vec(belt.get('size_m'), 3, 'conveyor.belt.size_m', positive=True),
        },
        'belt-in': {
            'uv': _req_vec(belt_in.get('uv'), 2, 'conveyor.belt-in.uv'),
        },
    }


def _validate_zone_labels(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError('zone_labels must be a non-empty list')
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'zone_labels[{idx}] must be an object')
        zone = _req_text(item.get('zone'), f'zone_labels[{idx}].zone')
        if zone not in CANONICAL_ZONE_LABEL_ZONES:
            raise ValueError(f'non-canonical zone_label zone: {zone!r}')
        if zone in seen:
            raise ValueError(f'duplicate zone_label zone: {zone!r}')
        seen.add(zone)
        out.append({
            'zone': zone,
            'label': _req_text(item.get('label'), f'zone_labels[{idx}].label'),
            'uv': _req_vec(item.get('uv'), 2, f'zone_labels[{idx}].uv'),
        })
    return out


def _validate_overview(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('overview must be an object')
    return {
        'pos': _req_vec(raw.get('pos'), 3, 'overview.pos'),
        'target': _req_vec(raw.get('target'), 3, 'overview.target'),
        'fov': _req_float(raw.get('fov'), 'overview.fov', positive=True),
    }


def _validate_cameras(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError('cameras must be an object')
    if set(raw.keys()) != set(CANONICAL_CAMERAS):
        raise ValueError(f'cameras keys must match canonical {list(CANONICAL_CAMERAS)}')
    out: dict[str, dict[str, Any]] = {}
    for key in CANONICAL_CAMERAS:
        cam = raw[key]
        if not isinstance(cam, dict):
            raise ValueError(f'cameras.{key} must be an object')
        default_source_id = _req_text(cam.get('default_source_id'), f'cameras.{key}.default_source_id')
        if default_source_id not in CANONICAL_CAMERA_SOURCES:
            raise ValueError(f'non-canonical camera source_id: {default_source_id!r}')
        source_ids = cam.get('source_ids')
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(s, str) and s in CANONICAL_CAMERA_SOURCES for s in source_ids)
        ):
            raise ValueError(f'cameras.{key}.source_ids must contain only canonical ROSY camera names')
        if default_source_id not in source_ids:
            raise ValueError(f'cameras.{key}.default_source_id must be listed in source_ids')
        view_mode = _req_text(cam.get('view_mode'), f'cameras.{key}.view_mode')
        if view_mode not in CAMERA_VIEW_MODES:
            raise ValueError(f'cameras.{key}.view_mode must be raw or map')
        flip = cam.get('flip')
        if type(flip) is not bool:
            raise ValueError(f'cameras.{key}.flip must be a boolean')
        aspect = cam.get('aspect')
        if not isinstance(aspect, list) or len(aspect) != 2 or not all(type(v) is int and v > 0 for v in aspect):
            raise ValueError(f'cameras.{key}.aspect must be [w, h] positive integers')
        pose = cam.get('pose')
        if not isinstance(pose, dict):
            raise ValueError(f'cameras.{key}.pose must be an object')
        validated_pose: dict[str, Any] = {
            'pos': _req_vec(pose.get('pos'), 3, f'cameras.{key}.pose.pos'),
            'target': _req_vec(pose.get('target'), 3, f'cameras.{key}.pose.target'),
            'fov': _req_float(pose.get('fov'), f'cameras.{key}.pose.fov', positive=True),
            'up': _req_vec(pose.get('up'), 3, f'cameras.{key}.pose.up'),
            'far': _req_float(pose.get('far'), f'cameras.{key}.pose.far', positive=True),
        }
        if 'view_up' in pose and pose['view_up'] is not None:
            validated_pose['view_up'] = _req_vec(pose['view_up'], 3, f'cameras.{key}.pose.view_up')
        out[key] = {
            'name': _req_text(cam.get('name'), f'cameras.{key}.name'),
            'default_source_id': default_source_id,
            'source_ids': list(source_ids),
            'view_mode': view_mode,
            'flip': flip,
            'aspect': [aspect[0], aspect[1]],
            'pose': validated_pose,
            'tag_offset': _req_vec(cam.get('tag_offset'), 3, f'cameras.{key}.tag_offset'),
        }
    return out


def validate_site_3d_layout(document: Any) -> dict[str, Any]:
    """Validate 3D camera-mapping layout config using strictly canonical ROSY names."""
    if not isinstance(document, dict):
        raise ValueError('site 3D layout root must be a JSON object')
    if document.get('schema') != SCHEMA_ID:
        raise ValueError(f'unsupported site 3D layout schema: {document.get("schema")!r}')
    status = document.get('status')
    if not isinstance(status, str) or status not in SUPPORTED_STATUSES:
        raise ValueError(f'unsupported site 3D layout status: {status!r}')
    return {
        'schema': SCHEMA_ID,
        'status': status,
        'plane': _validate_plane(document.get('plane')),
        'tier_height_m': _req_float(document.get('tier_height_m'), 'tier_height_m', positive=True),
        'arms': _validate_arms(document.get('arms')),
        'stops': _validate_stops(document.get('stops')),
        'vehicle': _validate_vehicle(document.get('vehicle')),
        'plates': _validate_plates(document.get('plates')),
        'conveyor': _validate_conveyor(document.get('conveyor')),
        'zone_labels': _validate_zone_labels(document.get('zone_labels')),
        'overview': _validate_overview(document.get('overview')),
        'cameras': _validate_cameras(document.get('cameras')),
    }


def load_site_3d_layout(path: Path | str | None = None) -> dict[str, Any]:
    """Read and validate site-3d-layout.json from disk."""
    target = Path(path) if path is not None else DEFAULT_SITE_3D_LAYOUT_PATH
    raw = json.loads(target.read_text(encoding='utf-8'))
    return validate_site_3d_layout(raw)
