"""Pure parser and normalizer for canonical ROSY cell-site maps (rosy.cell-site-map.v1)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from harbor.model import YARD_ARMS

SCHEMA_ID = 'rosy.cell-site-map.v1'
SUPPORTED_STATUSES = frozenset({'planned_unverified', 'verified'})
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_ROWS = 12
MAX_COLUMNS = 12
MAX_TIERS = 6
MAX_AREAS = 16
MAX_POSITIONS = 4096

ROSY_ARM_TO_HARBOR = {'arm_a': 'A', 'arm_b': 'B'}
assert set(ROSY_ARM_TO_HARBOR.values()) <= set(YARD_ARMS)

ROSY_ROLE_TO_HARBOR = {'보관': 'storage', '임시': 'temporary', '반출': 'outbound'}
HARBOR_ROLE_TO_ROSY = {harbor: rosy for rosy, harbor in ROSY_ROLE_TO_HARBOR.items()}
LEGACY_AREA_TO_ROLE = {'yard': 'storage', 'stand': 'outbound'}

ZONE_ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
WIRE_REF_RE = re.compile(r'^(?P<area>[a-z][a-z0-9_]*)-r(?P<row>[1-9]\d*)-c(?P<col>[1-9]\d*)-l(?P<tier>[1-9]\d*)$')


def _clean_text(value: Any, *, max_len: int = 80, code: str = 'INVALID_MAP_DOCUMENT') -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > max_len:
        raise ValueError(f'{code}: expected clean non-empty text within {max_len} chars')
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f'{code}: text contains control characters')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError(f'{code}: invalid Unicode text') from exc
    return value


def _req_int(value: Any, *, minimum: int = 1, maximum: int | None = None, code: str = 'INVALID_MAP_DOCUMENT', name: str = 'value') -> int:
    if type(value) is not int:
        raise ValueError(f'{code}: {name} must be an integer')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f'{code}: {name} out of allowed bounds')
    return value


def _map_arms(raw_arms: Any, *, context: str) -> tuple[list[str], list[str]]:
    if not isinstance(raw_arms, list) or not raw_arms:
        raise ValueError(f'UNSUPPORTED_ARM_REACH: {context} requires non-empty arms list')
    rosy_arms: list[str] = []
    harbor_arms: list[str] = []
    for raw in raw_arms:
        if not isinstance(raw, str) or raw not in ROSY_ARM_TO_HARBOR:
            raise ValueError(f'UNSUPPORTED_ARM_REACH: {context}/{raw}')
        if raw not in rosy_arms:
            rosy_arms.append(raw)
        mapped = ROSY_ARM_TO_HARBOR[raw]
        if mapped not in harbor_arms:
            harbor_arms.append(mapped)
    return sorted(harbor_arms), sorted(rosy_arms)


def _check_status(raw_status: Any, *, context: str) -> str:
    if not isinstance(raw_status, str) or raw_status not in SUPPORTED_STATUSES:
        raise ValueError(f'UNSUPPORTED_MAP_STATUS: {context} has unsupported status {raw_status!r}')
    return raw_status


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError(f'DUPLICATE_JSON_KEY: duplicate key {key!r}')
        obj[key] = val
    return obj


def _reject_constant(token: str) -> Any:
    raise ValueError(f'NON_FINITE_NUMBER: invalid JSON constant {token!r}')


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f'NON_FINITE_NUMBER: float {token!r} is not finite')
    return value


def decode_cell_site_json(raw: bytes | bytearray) -> dict[str, Any]:
    """Strictly decode UTF-8 JSON (<=1 MiB, no duplicate keys, no non-finite numbers, object root)."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ValueError('INVALID_MAP_DOCUMENT: empty document')
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('INVALID_MAP_DOCUMENT: document exceeds 1 MiB limit')
    try:
        text = bytes(raw).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('INVALID_MAP_DOCUMENT: document must be valid UTF-8') from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'INVALID_MAP_DOCUMENT: invalid JSON ({exc})') from exc
    if not isinstance(document, dict):
        raise ValueError('INVALID_MAP_DOCUMENT: root must be a JSON object')
    return document


def _validate_not_places(raw: Any, zone_ids: set[str]) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('INVALID_MAP_DOCUMENT: not_places must be a list')
    seen: set[str] = set()
    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('INVALID_MAP_DOCUMENT: invalid not_places entry')
        item_id = _clean_text(entry.get('id'), max_len=64)
        if not ZONE_ID_RE.fullmatch(item_id):
            raise ValueError(f'INVALID_MAP_DOCUMENT: invalid not_places id {item_id!r}')
        if item_id in seen:
            raise ValueError(f'INVALID_MAP_DOCUMENT: duplicate not_places id {item_id!r}')
        if item_id in zone_ids:
            raise ValueError(f'INVALID_MAP_DOCUMENT: not_places id overlaps zone {item_id!r}')
        seen.add(item_id)
        label = _clean_text(entry.get('label', item_id), max_len=80)
        items.append({'id': item_id, 'label': label})
    return items


def _parse_site_entry(entry: dict[str, Any], seen_site_ids: set[str], seen_wire_refs: set[str]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError('INVALID_MAP_DOCUMENT: site entry must be an object')
    site_id = _clean_text(entry.get('site_id'), max_len=96)
    sim_site = _clean_text(entry.get('sim_site'), max_len=96)
    area = _clean_text(entry.get('area'), max_len=64)
    row = _req_int(entry.get('row'), minimum=1, maximum=MAX_ROWS, name='site.row')
    slot = _req_int(entry.get('slot'), minimum=1, maximum=MAX_COLUMNS, name='site.slot')
    tier = _req_int(entry.get('tier'), minimum=1, maximum=MAX_TIERS, name='site.tier')
    wire_ref = _clean_text(entry.get('wire_ref'), max_len=96, code='WIRE_REF_MISMATCH')
    status = _check_status(entry.get('status'), context=f'site {site_id}')
    harbor_arms, rosy_arms = _map_arms(entry.get('arms'), context=f'site {site_id}')

    match = WIRE_REF_RE.fullmatch(wire_ref)
    if not match:
        raise ValueError(f'WIRE_REF_MISMATCH: invalid wire_ref {wire_ref!r}')
    w_area = match.group('area')
    w_row = int(match.group('row'))
    w_col = int(match.group('col'))
    w_tier = int(match.group('tier'))
    if w_area != area or w_row != row or w_tier != tier:
        raise ValueError(f'WIRE_REF_MISMATCH: {wire_ref!r} disagrees with area/row/tier fields')

    if site_id in seen_site_ids:
        raise ValueError(f'DUPLICATE_SITE: duplicate site_id {site_id!r}')
    if wire_ref in seen_wire_refs:
        raise ValueError(f'DUPLICATE_SITE: duplicate wire_ref {wire_ref!r}')
    seen_site_ids.add(site_id)
    seen_wire_refs.add(wire_ref)
    return {
        'site_id': site_id,
        'sim_site': sim_site,
        'area': area,
        'row': row,
        'slot': slot,
        'start_col': w_col,
        'tier': tier,
        'wire_ref': wire_ref,
        'arms': harbor_arms,
        'rosy_arms': rosy_arms,
        'status': status,
    }


def _validate_zone_meta(zone: dict[str, Any], *, require_slots: bool) -> tuple[dict[str, Any], int]:
    if not isinstance(zone, dict):
        raise ValueError('INVALID_MAP_DOCUMENT: zone must be an object')
    zone_id = _clean_text(zone.get('id'), max_len=64, code='UNSUPPORTED_ZONE_AREA')
    if not ZONE_ID_RE.fullmatch(zone_id):
        raise ValueError(f'UNSUPPORTED_ZONE_AREA: invalid zone id {zone_id!r}')
    label = _clean_text(zone.get('label'), max_len=80)
    rosy_role = _clean_text(zone.get('role'), max_len=32, code='UNSUPPORTED_ZONE_ROLE')
    if rosy_role not in ROSY_ROLE_TO_HARBOR:
        raise ValueError(f'UNSUPPORTED_ZONE_ROLE: {zone_id}/{rosy_role}')
    harbor_role = ROSY_ROLE_TO_HARBOR[rosy_role]
    if zone_id == 'yard' and harbor_role != 'storage':
        raise ValueError('INVALID_MAP_DOCUMENT: reserved zone yard must have role 보관')

    grid = zone.get('grid')
    if not isinstance(grid, dict) or set(grid.keys()) != {'rows', 'columns', 'tiers'}:
        raise ValueError(f'INVALID_MAP_DOCUMENT: {zone_id}.grid must contain exact keys rows/columns/tiers')
    rows = _req_int(grid['rows'], minimum=1, maximum=MAX_ROWS, name=f'{zone_id}.grid.rows')
    columns = _req_int(grid['columns'], minimum=1, maximum=MAX_COLUMNS, name=f'{zone_id}.grid.columns')
    tiers = _req_int(grid['tiers'], minimum=1, maximum=MAX_TIERS, name=f'{zone_id}.grid.tiers')
    slot_cells = _req_int(zone.get('slot_cells'), minimum=1, maximum=columns, name=f'{zone_id}.slot_cells')
    if columns % slot_cells != 0:
        raise ValueError(f'ZONE_NOT_RECTANGULAR: {zone_id} columns {columns} not divisible by slot_cells {slot_cells}')

    status = _check_status(zone.get('status'), context=f'zone {zone_id}')
    harbor_arms, rosy_arms = _map_arms(zone.get('arms'), context=f'zone {zone_id}')

    if require_slots and (not isinstance(zone.get('slots'), list) or not zone['slots']):
        raise ValueError(f'INVALID_MAP_DOCUMENT: zone {zone_id} requires non-empty slots list')

    return {
        'id': zone_id,
        'label': label,
        'role': harbor_role,
        'rosy_role': rosy_role,
        'rows': rows,
        'columns': columns,
        'tiers': tiers,
        'arms': harbor_arms,
        'rosy_arms': rosy_arms,
        'status': status,
        'site_count': rows * (columns // slot_cells) * tiers,
    }, slot_cells


def _normalize_canonical_zones(raw_zones: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if not isinstance(raw_zones, list) or not raw_zones:
        raise ValueError('INVALID_MAP_DOCUMENT: zones must be a non-empty list')
    zones: list[dict[str, Any]] = []
    sites: list[dict[str, Any]] = []
    seen_zones: set[str] = set()
    seen_site_ids: set[str] = set()
    seen_wire_refs: set[str] = set()
    common_slot_cells: int | None = None

    for raw_zone in raw_zones:
        z_meta, slot_cells = _validate_zone_meta(raw_zone, require_slots=True)
        z_id = z_meta['id']
        if z_id in seen_zones:
            raise ValueError(f'DUPLICATE_ZONE: duplicate zone id {z_id!r}')
        seen_zones.add(z_id)
        if common_slot_cells is None:
            common_slot_cells = slot_cells
        elif slot_cells != common_slot_cells:
            raise ValueError(f'MIXED_SLOT_CELLS: expected {common_slot_cells}, got {slot_cells} on {z_id}')

        rows, columns, tiers = z_meta['rows'], z_meta['columns'], z_meta['tiers']
        slots_per_row = columns // slot_cells
        raw_slots = raw_zone['slots']
        if len(raw_slots) != rows * slots_per_row:
            raise ValueError(f'ZONE_NOT_RECTANGULAR: {z_id} expected {rows * slots_per_row} slots, got {len(raw_slots)}')

        seen_coords: set[tuple[int, int, int]] = set()
        observed_rosy_arms: set[str] = set()
        for slot in raw_slots:
            if not isinstance(slot, dict):
                raise ValueError(f'INVALID_MAP_DOCUMENT: {z_id} slot must be an object')
            s_row = _req_int(slot.get('row'), minimum=1, maximum=rows, name=f'{z_id}.slot.row')
            s_num = _req_int(slot.get('slot'), minimum=1, maximum=slots_per_row, name=f'{z_id}.slot.slot')
            cols = slot.get('columns')
            if not isinstance(cols, list) or len(cols) != 2:
                raise ValueError(f'INVALID_MAP_DOCUMENT: {z_id} slot.columns must be [start, end]')
            start_col = _req_int(cols[0], minimum=1, maximum=columns, name=f'{z_id}.slot.columns[0]')
            end_col = _req_int(cols[1], minimum=start_col, maximum=columns, name=f'{z_id}.slot.columns[1]')
            expected_start = 1 + (s_num - 1) * slot_cells
            expected_end = expected_start + slot_cells - 1
            if start_col != expected_start or end_col != expected_end:
                raise ValueError(f'ZONE_NOT_RECTANGULAR: {z_id} slot ({s_row},{s_num}) columns {cols!r} inconsistent with width {slot_cells}')

            _check_status(slot.get('status'), context=f'{z_id} slot {slot.get("id")!r}')
            s_harbor_arms, s_rosy_arms = _map_arms(slot.get('arms'), context=f'{z_id} slot {slot.get("id")!r}')
            if not set(s_rosy_arms) <= set(z_meta['rosy_arms']):
                raise ValueError(f'UNSUPPORTED_ARM_REACH: slot arms {s_rosy_arms} exceed zone arms on {z_id}')
            observed_rosy_arms.update(s_rosy_arms)

            raw_tiers = slot.get('tiers')
            if not isinstance(raw_tiers, list) or len(raw_tiers) != tiers:
                raise ValueError(f'ZONE_NOT_RECTANGULAR: {z_id} slot ({s_row},{s_num}) expected {tiers} tiers')
            for tier_entry in raw_tiers:
                if not isinstance(tier_entry, dict):
                    raise ValueError(f'INVALID_MAP_DOCUMENT: {z_id} tier entry must be an object')
                t_val = _req_int(tier_entry.get('tier'), minimum=1, maximum=tiers, name=f'{z_id}.tier')
                coord = (s_row, start_col, t_val)
                if coord in seen_coords:
                    raise ValueError(f'ZONE_NOT_RECTANGULAR: duplicate coordinate {coord} in {z_id}')
                seen_coords.add(coord)
                site_id = _clean_text(tier_entry.get('id'), max_len=96)
                sim_site = _clean_text(tier_entry.get('item'), max_len=96)
                wire_ref = f'{z_id}-r{s_row}-c{start_col}-l{t_val}'
                if site_id in seen_site_ids or wire_ref in seen_wire_refs:
                    raise ValueError(f'DUPLICATE_SITE: duplicate id or address {site_id!r}/{wire_ref!r}')
                seen_site_ids.add(site_id)
                seen_wire_refs.add(wire_ref)
                sites.append({
                    'site_id': site_id,
                    'sim_site': sim_site,
                    'area': z_id,
                    'row': s_row,
                    'slot': s_num,
                    'start_col': start_col,
                    'tier': t_val,
                    'wire_ref': wire_ref,
                    'arms': s_harbor_arms,
                    'rosy_arms': s_rosy_arms,
                    'status': z_meta['status'],
                })

        expected_coords = {
            (r, 1 + idx * slot_cells, t)
            for r in range(1, rows + 1)
            for idx in range(slots_per_row)
            for t in range(1, tiers + 1)
        }
        if seen_coords != expected_coords:
            raise ValueError(f'ZONE_NOT_RECTANGULAR: {z_id} missing or extra cell coordinates')
        if observed_rosy_arms != set(z_meta['rosy_arms']):
            raise ValueError(f'ZONES_SITES_MISMATCH: {z_id} zone arms differ from slot union')
        zones.append(z_meta)

    assert common_slot_cells is not None
    return zones, sites, common_slot_cells


def _normalize_fleet_with_zones(raw_zones: Any, raw_sites: Any, raw_count: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if not isinstance(raw_zones, list) or not raw_zones:
        raise ValueError('INVALID_MAP_DOCUMENT: zones must be a non-empty list')
    if not isinstance(raw_sites, list) or not raw_sites:
        raise ValueError('INVALID_MAP_DOCUMENT: sites must be a non-empty list')
    if raw_count is not None:
        count = _req_int(raw_count, minimum=1, code='COUNT_MISMATCH', name='count')
        if count != len(raw_sites):
            raise ValueError(f'COUNT_MISMATCH: count={count} does not match len(sites)={len(raw_sites)}')

    zones: list[dict[str, Any]] = []
    zone_by_id: dict[str, dict[str, Any]] = {}
    common_slot_cells: int | None = None
    for raw_zone in raw_zones:
        z_meta, slot_cells = _validate_zone_meta(raw_zone, require_slots=False)
        z_id = z_meta['id']
        if z_id in zone_by_id:
            raise ValueError(f'DUPLICATE_ZONE: duplicate zone id {z_id!r}')
        if common_slot_cells is None:
            common_slot_cells = slot_cells
        elif slot_cells != common_slot_cells:
            raise ValueError(f'MIXED_SLOT_CELLS: expected {common_slot_cells}, got {slot_cells} on {z_id}')
        zone_by_id[z_id] = z_meta
        zones.append(z_meta)
    assert common_slot_cells is not None

    sites: list[dict[str, Any]] = []
    seen_site_ids: set[str] = set()
    seen_wire_refs: set[str] = set()
    sites_by_zone: dict[str, list[dict[str, Any]]] = {z['id']: [] for z in zones}

    for entry in raw_sites:
        site = _parse_site_entry(entry, seen_site_ids, seen_wire_refs)
        z_meta = zone_by_id.get(site['area'])
        if z_meta is None:
            raise ValueError(f'ZONES_SITES_MISMATCH: site {site["site_id"]!r} references unknown zone {site["area"]!r}')
        if (site['start_col'] - 1) % common_slot_cells != 0:
            raise ValueError(f'ZONES_SITES_MISMATCH: {site["wire_ref"]!r} start column misaligned with slot_cells={common_slot_cells}')
        if site['slot'] != 1 + (site['start_col'] - 1) // common_slot_cells:
            raise ValueError(f'WIRE_REF_MISMATCH: {site["wire_ref"]!r} disagrees with site.slot={site["slot"]}')
        if not set(site['rosy_arms']) <= set(z_meta['rosy_arms']):
            raise ValueError(f'ZONES_SITES_MISMATCH: site arms exceed zone arms on {site["site_id"]!r}')
        sites.append(site)
        sites_by_zone[site['area']].append(site)

    for z_meta in zones:
        z_id = z_meta['id']
        z_sites = sites_by_zone[z_id]
        expected_coords = {
            (r, 1 + idx * common_slot_cells, t)
            for r in range(1, z_meta['rows'] + 1)
            for idx in range(z_meta['columns'] // common_slot_cells)
            for t in range(1, z_meta['tiers'] + 1)
        }
        actual_coords = {(s['row'], s['start_col'], s['tier']) for s in z_sites}
        if len(z_sites) != len(actual_coords) or actual_coords != expected_coords:
            raise ValueError(f'ZONES_SITES_MISMATCH: zone {z_id} site coverage differs from declared grid')
        if {arm for s in z_sites for arm in s['rosy_arms']} != set(z_meta['rosy_arms']):
            raise ValueError(f'ZONES_SITES_MISMATCH: zone {z_id} arms differ from site arms union')

    return zones, sites, common_slot_cells


def _normalize_legacy_fleet(raw_sites: Any, raw_count: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, list[str]]:
    if not isinstance(raw_sites, list) or not raw_sites:
        raise ValueError('INVALID_MAP_DOCUMENT: sites must be a non-empty list')
    if raw_count is not None:
        count = _req_int(raw_count, minimum=1, code='COUNT_MISMATCH', name='count')
        if count != len(raw_sites):
            raise ValueError(f'COUNT_MISMATCH: count={count} does not match len(sites)={len(raw_sites)}')

    sites: list[dict[str, Any]] = []
    seen_site_ids: set[str] = set()
    seen_wire_refs: set[str] = set()
    by_area: dict[str, list[dict[str, Any]]] = {}
    row_cols: dict[tuple[str, int], set[int]] = {}

    for entry in raw_sites:
        site = _parse_site_entry(entry, seen_site_ids, seen_wire_refs)
        sites.append(site)
        by_area.setdefault(site['area'], []).append(site)
        row_cols.setdefault((site['area'], site['row']), set()).add(site['start_col'])

    gaps: set[int] = set()
    for start_cols in row_cols.values():
        ordered = sorted(start_cols)
        if len(ordered) >= 2:
            for left, right in zip(ordered, ordered[1:]):
                gaps.add(right - left)
    if not gaps:
        raise ValueError('SLOT_WIDTH_UNRESOLVED: cannot determine slot_cells without zones or multi-slot rows')
    if len(gaps) != 1:
        raise ValueError(f'MIXED_SLOT_CELLS: inconsistent start column spacing {sorted(gaps)}')
    slot_cells = next(iter(gaps))
    if slot_cells < 1 or slot_cells > MAX_COLUMNS:
        raise ValueError(f'INVALID_MAP_DOCUMENT: invalid derived slot_cells={slot_cells}')

    zones: list[dict[str, Any]] = []
    for area, area_sites in by_area.items():
        if not ZONE_ID_RE.fullmatch(area):
            raise ValueError(f'UNSUPPORTED_ZONE_AREA: invalid area id {area!r}')
        if area in LEGACY_AREA_TO_ROLE:
            harbor_role = LEGACY_AREA_TO_ROLE[area]
        elif area.startswith('temp_'):
            harbor_role = 'temporary'
        else:
            raise ValueError(f'UNSUPPORTED_ZONE_AREA: cannot determine role for legacy area {area!r}')

        rows = max(s['row'] for s in area_sites)
        tiers = max(s['tier'] for s in area_sites)
        max_start = max(s['start_col'] for s in area_sites)
        columns = max_start + slot_cells - 1
        if rows > MAX_ROWS or columns > MAX_COLUMNS or tiers > MAX_TIERS:
            raise ValueError(f'INVALID_MAP_DOCUMENT: {area} dimensions {(rows, columns, tiers)} exceed limits')
        if columns % slot_cells != 0:
            raise ValueError(f'ZONE_NOT_RECTANGULAR: {area} columns {columns} not divisible by slot_cells={slot_cells}')

        actual_coords: set[tuple[int, int, int]] = set()
        for site in area_sites:
            if (site['start_col'] - 1) % slot_cells != 0:
                raise ValueError(f'WIRE_REF_MISMATCH: {site["wire_ref"]!r} misaligned with slot_cells={slot_cells}')
            if site['slot'] != 1 + (site['start_col'] - 1) // slot_cells:
                raise ValueError(f'WIRE_REF_MISMATCH: {site["wire_ref"]!r} disagrees with site.slot={site["slot"]}')
            coord = (site['row'], site['start_col'], site['tier'])
            if coord in actual_coords:
                raise ValueError(f'ZONE_NOT_RECTANGULAR: duplicate cell {coord} in {area}')
            actual_coords.add(coord)

        expected_coords = {
            (r, 1 + idx * slot_cells, t)
            for r in range(1, rows + 1)
            for idx in range(columns // slot_cells)
            for t in range(1, tiers + 1)
        }
        if actual_coords != expected_coords:
            raise ValueError(f'ZONE_NOT_RECTANGULAR: legacy sites in {area} do not form a complete grid')

        harbor_arms = sorted({arm for s in area_sites for arm in s['arms']})
        rosy_arms = sorted({arm for s in area_sites for arm in s['rosy_arms']})
        zones.append({
            'id': area,
            'label': None,
            'role': harbor_role,
            'rosy_role': HARBOR_ROLE_TO_ROSY[harbor_role],
            'rows': rows,
            'columns': columns,
            'tiers': tiers,
            'arms': harbor_arms,
            'rosy_arms': rosy_arms,
            'status': area_sites[0]['status'],
            'site_count': len(area_sites),
        })

    warnings = [
        'Fleet 응답에 zones 요약이 없어 자리 주소(wire_ref)에서 격자 형상을 복원했고 Harbor 기본 구역 표시 이름을 사용했습니다 (정본 라벨과 다를 수 있음).',
    ]
    return zones, sites, slot_cells, warnings


def normalize_cell_site_map(document: dict[str, Any]) -> dict[str, Any]:
    """Normalize canonical file JSON, zones+sites Fleet response, or legacy sites-only Fleet response."""
    if not isinstance(document, dict):
        raise ValueError('INVALID_MAP_DOCUMENT: document must be an object')
    if document.get('schema') != SCHEMA_ID:
        raise ValueError(f'INVALID_MAP_DOCUMENT: expected schema {SCHEMA_ID!r}')
    revision = _req_int(document.get('revision'), minimum=1, name='revision')
    status = _check_status(document.get('status'), context='document')
    tier_height = document.get('tier_height_mm')
    if type(tier_height) not in (int, float) or isinstance(tier_height, bool) or not math.isfinite(tier_height) or tier_height <= 0:
        raise ValueError('INVALID_MAP_DOCUMENT: tier_height_mm must be a positive finite number')

    has_zones = 'zones' in document
    has_sites = 'sites' in document
    warnings: list[str] = []

    if has_zones and not has_sites:
        input_format = 'canonical_file'
        zones, sites, slot_cells = _normalize_canonical_zones(document['zones'])
        fallback_labels = False
    elif has_zones and has_sites:
        input_format = 'fleet_zones'
        zones, sites, slot_cells = _normalize_fleet_with_zones(document['zones'], document['sites'], document.get('count'))
        fallback_labels = False
    elif not has_zones and has_sites:
        input_format = 'fleet_legacy'
        zones, sites, slot_cells, warnings = _normalize_legacy_fleet(document['sites'], document.get('count'))
        fallback_labels = True
    else:
        raise ValueError('INVALID_MAP_DOCUMENT: document requires zones or sites')

    zone_ids = {z['id'] for z in zones}
    if len(zones) > MAX_AREAS:
        raise ValueError(f'INVALID_MAP_DOCUMENT: exceeds maximum {MAX_AREAS} areas')
    total_positions = sum(z['rows'] * z['columns'] * z['tiers'] for z in zones)
    if total_positions > MAX_POSITIONS:
        raise ValueError(f'INVALID_MAP_DOCUMENT: exceeds maximum {MAX_POSITIONS} cell positions')
    yard = next((z for z in zones if z['id'] == 'yard'), None)
    if yard is None or yard['role'] != 'storage':
        raise ValueError('INVALID_MAP_DOCUMENT: primary storage zone yard is required')
    if not any(z['role'] == 'outbound' for z in zones):
        raise ValueError('OUTBOUND_REQUIRED: at least one outbound zone is required')
    if 'conveyor' in zone_ids:
        raise ValueError('INVALID_MAP_DOCUMENT: conveyor identifier cannot alias a zone')
    yard_cells = {f'Y{r}{c}' for r in range(1, yard['rows'] + 1) for c in range(1, yard['columns'] + 1)}
    if zone_ids & yard_cells:
        raise ValueError('INVALID_MAP_DOCUMENT: auxiliary zone id collides with yard cell id')

    not_places = _validate_not_places(document.get('not_places'), zone_ids)

    digest_body = {
        'schema': SCHEMA_ID,
        'revision': revision,
        'status': status,
        'tier_height_mm': float(tier_height),
        'slot_cells': slot_cells,
        'zones': [
            {k: z[k] for k in ('id', 'label', 'role', 'rows', 'columns', 'tiers', 'arms', 'status')}
            for z in zones
        ],
        'wire_refs': sorted(s['wire_ref'] for s in sites),
        'not_places': [np_item['id'] for np_item in not_places],
    }
    digest = hashlib.sha256(
        json.dumps(digest_body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    ).hexdigest()

    return {
        'schema': SCHEMA_ID,
        'revision': revision,
        'status': status,
        'tier_height_mm': float(tier_height),
        'slot_cells': slot_cells,
        'input_format': input_format,
        'fallback_labels': fallback_labels,
        'zones': zones,
        'sites': sites,
        'not_places': not_places,
        'conveyor': 'conveyor',
        'digest': digest,
        'warnings': warnings,
    }


def default_name(normalized: dict[str, Any]) -> str:
    return f"ROSY 셀 맵 rev{normalized['revision']} ({normalized['status']})"[:120]


def default_calibration_ref(normalized: dict[str, Any]) -> str:
    return f"{normalized['schema']}#rev{normalized['revision']} ({normalized['status']})"[:192]
