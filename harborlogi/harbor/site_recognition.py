"""Read-only validation and ledger comparison for ROSY cell-site recognition snapshots (`rosy.cell-site-recognition.v1`)."""
from datetime import datetime
import math
import re

from harbor.cargo_colors import CARGO_COLORS
from harbor.cell_site_map import WIRE_REF_RE

SCHEMA_ID = 'rosy.cell-site-recognition.v1'
MAX_SITES = 512
MAX_TEXT_LENGTH = 192
_DOCUMENT_FIELDS = {'schema', 'snapshot_id', 'observed_at', 'source_id',
                    'calibration_revision', 'site_map_revision', 'sites'}
_SITE_FIELDS = {'site_ref', 'state', 'object_class', 'object_ref_read', 'confidence'}
_UTC_ISO_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                         r'(?:\.[0-9]{1,6})?(?:Z|\+00:00)')


def _text(value, *, field, limit=MAX_TEXT_LENGTH, code='INVALID_RECOGNITION_TEXT'):
    if (not isinstance(value, str) or not value or len(value) > limit
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f'{code}: {field} requires clean text within {limit} characters')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError(f'{code}: {field} contains invalid Unicode') from error
    return value


def _wire_ref(value):
    value = _text(value, field='site_ref', limit=96, code='INVALID_SITE_REF')
    match = WIRE_REF_RE.fullmatch(value)
    if match is None:
        raise ValueError('INVALID_SITE_REF: expected <area>-r<row>-c<column>-l<tier>')
    return match


def parse_site_recognition(payload, *, known_site_refs=None) -> dict:
    """Validate a partial snapshot, returning a detached dict with UTC ISO text intact."""
    if not isinstance(payload, dict) or set(payload) != _DOCUMENT_FIELDS:
        raise ValueError('INVALID_RECOGNITION_DOCUMENT: unknown or missing snapshot fields')
    if payload['schema'] != SCHEMA_ID:
        raise ValueError(f'UNSUPPORTED_RECOGNITION_SCHEMA: expected {SCHEMA_ID}')
    result = {'schema': SCHEMA_ID}
    for field in ('snapshot_id', 'source_id', 'calibration_revision'):
        result[field] = _text(payload[field], field=field)
    observed_at = _text(payload['observed_at'], field='observed_at', limit=32, code='INVALID_OBSERVED_AT')
    if not _UTC_ISO_RE.fullmatch(observed_at):
        raise ValueError('INVALID_OBSERVED_AT: expected UTC ISO timestamp with Z or +00:00')
    try:
        datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('INVALID_OBSERVED_AT: invalid calendar date or time') from error
    result['observed_at'] = observed_at
    revision = payload['site_map_revision']
    if type(revision) is not int or revision < 1:
        raise ValueError('INVALID_SITE_MAP_REVISION: expected a positive integer')
    result['site_map_revision'] = revision
    sites = payload['sites']
    if not isinstance(sites, list) or len(sites) > MAX_SITES:
        raise ValueError(f'INVALID_SITES: expected a list of at most {MAX_SITES} sites')
    known = None if known_site_refs is None else set(known_site_refs)
    result['sites'] = []
    seen = set()
    for site in sites:
        if not isinstance(site, dict) or set(site) != _SITE_FIELDS:
            raise ValueError('INVALID_RECOGNITION_SITE: unknown or missing site fields')
        ref = site['site_ref']
        _wire_ref(ref)
        if ref in seen:
            raise ValueError('DUPLICATE_SITE_REF: site_ref occurs more than once')
        if known is not None and ref not in known:
            raise ValueError('UNKNOWN_SITE_REF: site_ref is outside the installed site map')
        seen.add(ref)
        state = site['state']
        if not isinstance(state, str) or state not in {'EMPTY', 'OCCUPIED', 'UNREADABLE'}:
            raise ValueError('INVALID_RECOGNITION_STATE: expected EMPTY, OCCUPIED or UNREADABLE')
        for field in ('object_class', 'object_ref_read'):
            if site[field] is not None:
                if state == 'EMPTY':
                    raise ValueError('INVALID_EMPTY_SITE: an EMPTY site carries no object_class or object_ref_read')
                _text(site[field], field=field)
        confidence = site['confidence']
        if (type(confidence) not in (int, float) or not 0 <= confidence <= 1
                or not math.isfinite(confidence)):
            raise ValueError('INVALID_CONFIDENCE: expected a finite number in 0..1')
        result['sites'].append(dict(site))
    return result


def placement_to_site_ref(area, row, column, level):
    """Map any cell of a 1x3 container to its c1/c4 wire anchor."""
    _text(area, field='area', limit=64, code='INVALID_PLACEMENT')
    if (any(type(v) is not int or v < 1 for v in (row, column, level)) or column > 6):
        raise ValueError('INVALID_PLACEMENT: positive row/level and column in 1..6 required')
    ref = f'{area}-r{row}-c{1 if column <= 3 else 4}-l{level}'
    _wire_ref(ref)
    return ref


def site_ref_to_placement(site_ref):
    """Return the Harbor anchor coordinates; middle columns are not anchors."""
    match = _wire_ref(site_ref)
    column = int(match['col'])
    if column not in (1, 4):
        raise ValueError('INVALID_SITE_REF: a 1x3 container anchor column must be 1 or 4')
    return {'area': match['area'], 'row': int(match['row']),
            'column': column, 'level': int(match['tier'])}


def harbor_cell_to_site_ref(cell, *, level=1):
    """Convert a yard cell Y{row}{column} to the wire anchor at the requested level."""
    if not isinstance(cell, str) or (match := re.fullmatch(r'Y([1-9][0-9]*)([1-6])', cell)) is None:
        raise ValueError('INVALID_HARBOR_CELL: expected Y{row}{column}, column 1..6')
    return placement_to_site_ref('yard', int(match[1]), int(match[2]), level)


def site_ref_to_harbor_cells(site_ref):
    """Return the three Y cells covered by a yard site; level is in the wire ref."""
    placement = site_ref_to_placement(site_ref)
    if placement['area'] != 'yard':
        raise ValueError('INVALID_HARBOR_CELL: only yard sites have Y cell names')
    return tuple(f"Y{placement['row']}{column}"
                 for column in range(placement['column'], placement['column'] + 3))


def compare_with_yard(snapshot, placements, *, cargo_colors=None):
    """Return display/person-review results only; unreported sites imply nothing."""
    snapshot = parse_site_recognition(snapshot)
    cargo_by_ref = {}
    color_by_ref = {}
    for placement in placements:
        if (not isinstance(placement, dict)
                or not {'area', 'row', 'column', 'level', 'cargo_id'} <= set(placement)
                or type(placement['column']) is not int or placement['column'] not in (1, 4)):
            raise ValueError('INVALID_PLACEMENT: expected cargo at a column 1 or 4 anchor')
        ref = placement_to_site_ref(placement['area'], placement['row'], placement['column'], placement['level'])
        if ref in cargo_by_ref:
            raise ValueError('DUPLICATE_PLACEMENT: more than one cargo at the same anchor')
        cargo_by_ref[ref] = _text(placement['cargo_id'], field='cargo_id', code='INVALID_PLACEMENT')
        raw_color = placement.get('color')
        if isinstance(raw_color, str) and raw_color in CARGO_COLORS:
            color_by_ref[ref] = raw_color
    results = []
    for site in snapshot['sites']:
        site_ref_to_placement(site['site_ref'])
        cargo = cargo_by_ref.get(site['site_ref'])
        if site['state'] == 'UNREADABLE':
            result = 'UNREADABLE'
        elif site['state'] == 'EMPTY':
            result = 'ROSY_EMPTY_HARBOR_OCCUPIED' if cargo is not None else 'MATCH'
        elif cargo is None:
            result = 'ROSY_OCCUPIED_HARBOR_EMPTY'
        elif site['object_ref_read'] is not None and site['object_ref_read'] != cargo:
            result = 'CLASS_MISMATCH'
        else:
            result = 'MATCH'
        entry = {**site, 'harbor_cargo_id': cargo, 'result': result}
        expected_color = color_by_ref.get(site['site_ref'])
        if expected_color is None and cargo is not None and cargo_colors:
            candidate = cargo_colors.get(cargo)
            if isinstance(candidate, str) and candidate in CARGO_COLORS:
                expected_color = candidate
        observed_class = site['object_class']
        if (site['state'] == 'OCCUPIED'
                and expected_color is not None
                and observed_class in CARGO_COLORS
                and observed_class != expected_color):
            entry['color_mismatch'] = True
        results.append(entry)
    return results
