"""Loader and canonical validator for topview cell-site pixel overlay config."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
import re
from typing import Any

from harbor.cell_site_map import (
    CANONICAL_ZONE_SPECS,
    SUPPORTED_STATUSES,
    canonical_cell_slot_specs,
    canonical_cell_wire_refs,
)

logger = logging.getLogger(__name__)

SCHEMA_ID = 'harbor.topview-site-pixels.v1'
DEFAULT_SITE_PIXELS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'topview-site-pixels.json'
DEFAULT_SIDE_PIXELS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'sideview-site-pixels.json'
DEFAULT_ARM_S_PIXELS_PATH = Path(__file__).resolve().parents[1] / 'config' / 'arm-s-site-pixels.json'
# Canonical non-slot places the supply-arm-S camera shows (not_places id, belt-in end); anything else is rejected.
_ARM_S_PICK_SITES = frozenset({'not_places:vehicle_tray', 'belt-in'})
# Side camera: one box per yard stack tier (e.g. 'side:yard-r2-t3'). Temp plates are excluded on side view.
_TIER_SITE_RE = re.compile(r'^side:(?P<zone>[a-z][a-z0-9_]*?)(?:-r(?P<row>[1-9]\d*))?-t(?P<tier>[1-9]\d*)$')
_SIDE_EXCLUDED_ZONES = frozenset({'temp_a', 'temp_b'})
_SLOT_SITE_RE = re.compile(r'^(?P<zone>[a-z][a-z0-9_]*):r(?P<row>[1-9]\d*)-s(?P<slot>[1-9]\d*)$')


def _is_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value)


def _expected_slot_wire_refs(site: str) -> tuple[str, ...]:
    match = _SLOT_SITE_RE.fullmatch(site)
    if match is None:
        return ()
    zone = match.group('zone')
    row = int(match.group('row'))
    slot_idx = int(match.group('slot'))
    spec = CANONICAL_ZONE_SPECS.get(zone)
    if spec is None or not (1 <= row <= spec['rows']) or not (1 <= slot_idx <= len(spec['valid_cols'])):
        return ()
    col = spec['valid_cols'][slot_idx - 1]
    return tuple(f'{zone}-r{row}-c{col}-l{tier}' for tier in range(1, spec['tiers'] + 1))


def _expected_tier_wire_refs(site: str) -> tuple[str, ...]:
    """All wire refs one side-camera tier box covers: only yard rows are shown on sideview (temp plates excluded)."""
    match = _TIER_SITE_RE.fullmatch(site)
    if match is None:
        return ()
    zone = match.group('zone')
    if zone != 'yard':
        return ()
    spec = CANONICAL_ZONE_SPECS.get(zone)
    if spec is None:
        return ()
    tier = int(match.group('tier'))
    row = match.group('row')
    if not (1 <= tier <= spec['tiers']) or row is None or not (1 <= int(row) <= spec['rows']):
        return ()
    r = int(row)
    return tuple(f'{zone}-r{r}-c{col}-l{tier}' for col in spec['valid_cols'])


def _default_short_label(site: str, label: str) -> str:
    if site == 'belt':
        return '컨베이어'
    if site == 'stand:r1-s1':
        return '반출대'
    match = _SLOT_SITE_RE.fullmatch(site)
    if match is None:
        return label
    zone, row, slot_idx = match.group('zone'), match.group('row'), match.group('slot')
    if zone == 'yard':
        return f'Y r{row}·s{slot_idx}'
    if zone == 'temp_a':
        return f'A{row}'
    if zone == 'temp_b':
        return f'B{row}'
    return label


def _validate_roi(raw_roi: Any, width: int, height: int) -> list[float] | None:
    if raw_roi is None:
        return None
    if not isinstance(raw_roi, list) or len(raw_roi) != 4 or not all(_is_finite_number(v) for v in raw_roi):
        raise ValueError('roi must be null or [x1, y1, x2, y2]')
    x1, y1, x2, y2 = (float(v) for v in raw_roi)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f'roi {raw_roi!r} out of image bounds ({width}x{height})')
    return [x1, y1, x2, y2]


def _validate_polygon(raw_polygon: Any, width: int, height: int) -> list[list[float]] | None:
    if raw_polygon is None:
        return None
    if not isinstance(raw_polygon, list) or len(raw_polygon) < 3:
        raise ValueError('polygon must be null or a list of at least 3 [x, y] vertices')
    vertices: list[list[float]] = []
    for vertex in raw_polygon:
        if not isinstance(vertex, list) or len(vertex) != 2 or not all(_is_finite_number(v) for v in vertex):
            raise ValueError(f'invalid polygon vertex {vertex!r}')
        x, y = float(vertex[0]), float(vertex[1])
        if not (0 <= x <= width and 0 <= y <= height):
            raise ValueError(f'polygon vertex {vertex!r} out of image bounds ({width}x{height})')
        vertices.append([x, y])
    area2 = 0.0
    for idx, (x1, y1) in enumerate(vertices):
        x2, y2 = vertices[(idx + 1) % len(vertices)]
        area2 += x1 * y2 - x2 * y1
    if abs(area2) <= 0.0:
        raise ValueError('polygon must have positive area')
    return vertices


def _validate_point(raw_point: Any, width: int, height: int) -> list[float] | None:
    if raw_point is None:
        return None
    if not isinstance(raw_point, list) or len(raw_point) != 2 or not all(_is_finite_number(v) for v in raw_point):
        raise ValueError('point must be null or [x, y]')
    x, y = float(raw_point[0]), float(raw_point[1])
    if not (0 <= x <= width and 0 <= y <= height):
        raise ValueError(f'point {raw_point!r} out of image bounds ({width}x{height})')
    return [x, y]


def validate_topview_site_pixels(document: Any) -> dict[str, Any] | None:
    """Validate a topview site-pixels document against canonical ROSY cell slot/wire_ref names.

    Invalid individual points are logged and discarded so one bad entry never hides valid overlays.
    """
    if not isinstance(document, dict):
        logger.warning('topview site-pixels rejected: root must be a JSON object')
        return None
    if document.get('schema') != SCHEMA_ID:
        logger.warning('topview site-pixels rejected: unsupported schema %r', document.get('schema'))
        return None
    source_id = document.get('source_id')
    if not isinstance(source_id, str) or not source_id.strip():
        logger.warning('topview site-pixels rejected: missing source_id')
        return None
    source_id = source_id.strip()

    image = document.get('image')
    if (
        not isinstance(image, list)
        or len(image) != 2
        or not all(type(v) is int and v > 0 for v in image)
    ):
        logger.warning('topview site-pixels rejected: invalid image dimensions %r', image)
        return None
    width, height = image[0], image[1]

    status = document.get('status')
    if not isinstance(status, str) or status not in SUPPORTED_STATUSES:
        logger.warning('topview site-pixels rejected: unsupported status %r', status)
        return None

    raw_points = document.get('points')
    if not isinstance(raw_points, list):
        logger.warning('topview site-pixels rejected: points must be a list')
        return None

    canonical_slots = canonical_cell_slot_specs()
    canonical_refs = canonical_cell_wire_refs()
    seen_sites: set[str] = set()
    validated_points: list[dict[str, Any]] = []

    for index, entry in enumerate(raw_points):
        if not isinstance(entry, dict):
            logger.warning('topview site-pixels: skipping non-object point at index %d', index)
            continue
        site = entry.get('site')
        label = entry.get('label')
        if not isinstance(site, str) or not site.strip():
            logger.warning('topview site-pixels: skipping point %d with missing site', index)
            continue
        if not isinstance(label, str) or not label.strip():
            logger.warning('topview site-pixels: skipping site %r with missing label', site)
            continue
        site = site.strip()
        label = label.strip()
        if site in seen_sites:
            logger.warning('topview site-pixels: skipping duplicate site %r', site)
            continue

        raw_short = entry.get('short_label')
        short_label = raw_short.strip() if isinstance(raw_short, str) and raw_short.strip() else _default_short_label(site, label)

        if site == 'belt' or site in _ARM_S_PICK_SITES:
            kind = entry.get('kind') or 'pick'
            if kind != 'pick':
                logger.warning('topview site-pixels: skipping %s point with unsupported kind %r', site, kind)
                continue
            try:
                point_xy = _validate_point(entry.get('point'), width, height)
            except ValueError as exc:
                logger.warning('topview site-pixels: skipping %s (%s)', site, exc)
                continue
            seen_sites.add(site)
            validated_points.append({
                'site': site,
                'label': label,
                'short_label': short_label,
                'zone': 'belt' if site == 'belt' else site.split(':', 1)[0],
                'kind': 'pick',
                'point': point_xy,
            })
            continue

        tier_match = _TIER_SITE_RE.fullmatch(site)
        is_tier = tier_match is not None
        if is_tier and tier_match.group('zone') in _SIDE_EXCLUDED_ZONES:
            logger.warning('topview site-pixels: excluding sideview temp site %r', site)
            continue
        if not is_tier and site not in canonical_slots:
            logger.warning('topview site-pixels: skipping non-canonical site %r', site)
            continue

        raw_refs = entry.get('wire_refs')
        expected_refs = _expected_tier_wire_refs(site) if is_tier else _expected_slot_wire_refs(site)
        if (
            not isinstance(raw_refs, list)
            or not raw_refs
            or not all(isinstance(r, str) for r in raw_refs)
            or len(set(raw_refs)) != len(raw_refs)
            or not set(raw_refs) <= canonical_refs
            or set(raw_refs) != set(expected_refs)
        ):
            logger.warning(
                'topview site-pixels: skipping site %r due to wire_refs mismatch (got %r, expected %r)',
                site, raw_refs, list(expected_refs),
            )
            continue

        try:
            roi = _validate_roi(entry.get('roi'), width, height)
            polygon = _validate_polygon(entry.get('polygon'), width, height)
        except ValueError as exc:
            logger.warning('topview site-pixels: skipping site %r (%s)', site, exc)
            continue

        seen_sites.add(site)
        zone = tier_match.group('zone') if is_tier else site.split(':', 1)[0]
        validated_entry: dict[str, Any] = {
            'site': site,
            'label': label,
            'short_label': short_label,
            'zone': zone,
            'kind': 'tier' if is_tier else 'slot',
            'wire_refs': list(raw_refs),
            'roi': roi,
        }
        if polygon is not None:
            validated_entry['polygon'] = polygon
        validated_points.append(validated_entry)

    rosy_commit = document.get('rosy_commit')
    return {
        'schema': SCHEMA_ID,
        'source_id': source_id,
        'image': [width, height],
        'status': status,
        'rosy_commit': rosy_commit.strip() if isinstance(rosy_commit, str) and rosy_commit.strip() else '',
        'reference_frame': document['reference_frame'].strip() if isinstance(document.get('reference_frame'), str) else '',
        'frame_source': document['frame_source'].strip() if isinstance(document.get('frame_source'), str) else '',
        'points': validated_points,
    }


def load_topview_site_pixels(path: Path | str | None = None) -> dict[str, Any] | None:
    """Read and validate topview site-pixels JSON from disk. Returns None on read/schema error."""
    target = Path(path) if path is not None else DEFAULT_SITE_PIXELS_PATH
    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        logger.warning('topview site-pixels load failed (%s): %s', target, exc)
        return None
    return validate_topview_site_pixels(raw)


def load_arm_s_site_pixels(path: Path | str | None = None) -> dict[str, Any] | None:
    """Supply-arm-S camera places (vehicle tray, belt-in): same schema; pixels stay null until measured."""
    return load_topview_site_pixels(path if path is not None else DEFAULT_ARM_S_PIXELS_PATH)


def load_sideview_site_pixels(path: Path | str | None = None) -> dict[str, Any] | None:
    """Side-camera tier boxes: same schema and validator, separate file."""
    return load_topview_site_pixels(path if path is not None else DEFAULT_SIDE_PIXELS_PATH)
