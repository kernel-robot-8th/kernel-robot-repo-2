"""Read-only context builder for the Yard page's Camera <-> 3D Position Mapping panel."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from harbor.cargo_colors import CARGO_COLORS
from harbor.cell_site_map import canonical_cell_wire_refs
from harbor.site_3d_layout import CANONICAL_CAMERAS, load_site_3d_layout
from harbor.topview_site_pixels import (
    load_arm_s_site_pixels,
    load_sideview_site_pixels,
    load_topview_site_pixels,
)


def _containers_from_scene(scene: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Extract canonical wire_ref -> container metadata from the existing yard 3D scene."""
    if not scene or not scene.get('frames'):
        return {}
    canonical_refs = canonical_cell_wire_refs()
    containers: dict[str, dict[str, Any]] = {}
    for box in scene['frames'][0].get('boxes') or ():
        area = box.get('area')
        row = box.get('row')
        col = box.get('column')
        level = box.get('level')
        code = box.get('id')
        if not (isinstance(area, str) and isinstance(code, str) and code):
            continue
        if not all(type(v) is int and v >= 1 for v in (row, col, level)):
            continue
        site_ref = f'{area}-r{row}-c{col}-l{level}'
        if site_ref not in canonical_refs:
            continue
        containers[site_ref] = {
            'code': code,
            'site_ref': site_ref,
            'color': box.get('color') or '',
            'color_label': box.get('color_label') or '',
            'color_confirmed': bool(box.get('color_confirmed')),
            'priority_tier': box.get('priority_tier') or 'neutral',
            'priority_label': box.get('priority_label') or '',
            'cargo_name': box.get('cargo_name') or '',
            'shipper': box.get('shipper') or '',
            'destination': box.get('destination') or '',
            'voyage': box.get('voyage') or '',
            'cutoff': box.get('cutoff') or '',
        }
    return containers


def _overlay_doc(from_topview: dict[str, Any] | None, loader, source_id: str) -> dict[str, Any] | None:
    doc = deepcopy(from_topview) if isinstance(from_topview, dict) else loader()
    if doc is not None and source_id:
        doc['source_id'] = source_id
    return doc


def camera_map_context(*, scene: dict[str, Any] | None, topview: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble read-only payload for the yard camera-mapping panel without writing to the DB."""
    layout = load_site_3d_layout()
    tv = topview if isinstance(topview, dict) else {}
    sources_by_id = {
        item['source_id']: item['url']
        for item in (tv.get('sources') or ())
        if isinstance(item, dict) and item.get('source_id') and item.get('url')
    }

    cameras: list[dict[str, Any]] = []
    camera_by_key: dict[str, dict[str, Any]] = {}
    for key in CANONICAL_CAMERAS:
        cfg = layout['cameras'][key]
        matched_id = next((sid for sid in cfg['source_ids'] if sid in sources_by_id), None)
        source_id = matched_id or cfg['default_source_id']
        base_url = sources_by_id.get(matched_id, '') if matched_id else ''
        frame_url = f"{base_url}?view={cfg['view_mode']}" if base_url else ''
        entry = {
            'key': key,
            'name': cfg['name'],
            'source_id': source_id,
            'available': bool(frame_url),
            'url': frame_url,
            'view_mode': cfg['view_mode'],
            'flip': cfg['flip'],
            'aspect': list(cfg['aspect']),
            'pose': cfg['pose'],
            'tag_offset': list(cfg['tag_offset']),
        }
        cameras.append(entry)
        camera_by_key[key] = entry

    overlays = {
        'top': _overlay_doc(tv.get('site_pixels'), load_topview_site_pixels, camera_by_key['top']['source_id']),
        'side': _overlay_doc(tv.get('side_pixels'), load_sideview_site_pixels, camera_by_key['side']['source_id']),
        'arms': _overlay_doc(tv.get('arm_s_pixels'), load_arm_s_site_pixels, camera_by_key['arms']['source_id']),
    }

    default_camera = next((c['key'] for c in cameras if c['available']), 'top')
    if camera_by_key['top']['available']:
        default_camera = 'top'

    return {
        'layout': layout,
        'containers': _containers_from_scene(scene),
        'cargo_colors': list(CARGO_COLORS),
        'cameras': cameras,
        'overlays': overlays,
        'default_camera': default_camera,
        'status': tv.get('status') or '',
        'unverified_layout': layout['status'] == 'planned_unverified',
    }
