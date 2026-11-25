"""Read-only logical cuboids from the existing configured stage presentation."""
from copy import deepcopy

from harbor.cargo_colors import CARGO_COLORS, CARGO_COLOR_LABELS
from harbor_web.yard_occupancy import AREA_LABELS


def _box(cell, area):
    return dict(id=cell['code'], area=area['id'], row=cell['row'], column=cell['column'], level=cell['level'],
        width=cell.get('colspan', 1), depth=cell.get('rowspan', 1),
        x=area['x'] + cell['column'] - 1, y=area['y'] + cell['row'] - 1, z=cell['level'] - 1,
        role=cell['role'], label=cell['label'], positions=list(cell.get('covered_positions') or [cell['position_id']]),
        footprint_revision=cell.get('footprint_revision'), orientation=cell.get('orientation', 0),
        size=cell.get('footprint_label', '1×1'))


def area_presentation(payload):
    """Display-only names and spacing; pinned installation coordinates stay intact."""
    from harbor_web import services
    presentation = services.area_presentation(payload)
    if payload.get('slot_cells'):
        for area_id, suffix in (('temp', ''), ('temp_a', ' A'), ('temp_b', ' B')):
            item = presentation.get(area_id)
            old = f'임시 장치{suffix}'
            if item and item['label'] in (old, old + '(왼쪽)', old + '(오른쪽)'):
                item['label'] = AREA_LABELS[area_id]
        stand, plate = presentation.get('stand'), presentation.get('temp_a')
        if stand and plate and (stand['x'], stand['y'], plate['x'], plate['y']) == (7.2, 1, 0, -4.2):
            # Only the old cell map's centered stand moves to A's side; custom maps keep their spacing.
            areas = {a.area_id: a for a in services.map_areas(payload)}
            candidate, size = dict(stand, y=plate['y']), areas['stand']
            overlaps = any(candidate['x'] < p['x'] + areas[key].columns and p['x'] < candidate['x'] + size.columns
                and candidate['y'] < p['y'] + areas[key].rows and p['y'] < candidate['y'] + size.rows
                for key, p in presentation.items() if key != 'stand')
            if not overlaps:
                presentation['stand'] = candidate
    return presentation


def present_stage(stage, payload):
    """Carry explicit display metadata with the pinned condition, keeping logical cell addresses intact."""
    if 'area_presentation' in payload or payload.get('slot_cells'):
        stage['area_presentation'] = area_presentation(payload)
        for source in [stage, *stage.get('auxiliary_areas', []), *stage.get('auxiliary_bays', [])]:
            area_id = source.get('area_id', source.get('bay_id', 'yard'))
            item = stage['area_presentation'][area_id]
            source.update(area_label=item['label'], x=item['x'], y=item['y'])
    stage['slot_cells'] = payload.get('slot_cells')
    if stage.get('spatial') and stage.get('layout') and payload.get('slot_cells'):
        # A site with fixed container slots reads slots, not cells (KAN-115); other sites keep the cell grid
        # because their cargo sizes and turns vary.
        from harbor_web.yard_rows import slot_map_from_stage
        stage['slot_map'] = slot_map_from_stage(stage, payload)
    return stage


def scene_for(stages):
    if not stages or any(not stage.get('layout') for stage in stages):
        return None
    first = stages[0]
    areas = [dict(id=first.get('area_id', 'yard'), label='장치 야드', x=0, y=0, **first['layout'])]
    # Auxiliary offsets are presentation spacing, never measured world coordinates.
    right, y = first['layout']['columns'] + 1.2, 0
    for source in first.get('auxiliary_areas', []):
        area_id = source['area_id']
        areas.append(dict(id=area_id, label=AREA_LABELS.get(area_id, area_id),
            x=right, y=y, **source['layout']))
        y += source['layout']['rows'] + 1.2
    for bay in first.get('auxiliary_bays', []):
        areas.append(dict(id=bay['bay_id'], label=bay.get('kind', bay['bay_id']), rows=1, columns=1,
            levels=bay['capacity'], x=right, y=y))
        y += 2.2
    for area in areas:
        area.update(first.get('area_presentation', {}).get(area['id'], {}))
    by_id = {area['id']: area for area in areas}
    # Preserve configured position IDs, including cells hidden by a merged footprint.
    for area in areas:
        area['addresses'] = {}
    for stage in stages:
        for source in [stage, *stage.get('auxiliary_areas', [])]:
            area = by_id[source.get('area_id', 'yard')]
            for layer in source['layers']:
                for row in layer['rows']:
                    for cell in row['cells']:
                        positions = cell.get('covered_positions') or [cell['position_id']]
                        coordinates = [(r, c) for r in range(cell['row'], cell['row'] + cell.get('rowspan', 1))
                                       for c in range(cell['column'], cell['column'] + cell.get('colspan', 1))]
                        for (r, c), position in zip(coordinates, positions):
                            area['addresses'][f"{r},{c},{cell['level']}"] = position
        for bay in stage.get('auxiliary_bays', []):
            area = by_id[bay['bay_id']]
            for level in range(1, area['levels'] + 1):
                area['addresses'][f'1,1,{level}'] = f"{bay['bay_id']}-L{level}"

    frames = []
    for stage in stages:
        boxes = []
        for source in [stage, *stage.get('auxiliary_areas', [])]:
            area = by_id[source.get('area_id', 'yard')]
            boxes.extend(_box(cell, area) for layer in source['layers'] for row in layer['rows']
                for cell in row['cells'] if cell['code'])
        for bay in stage.get('auxiliary_bays', []):
            area = by_id[bay['bay_id']]
            boxes.extend(_box(dict(cell, row=1, column=1, position_id=f"{bay['bay_id']}-L{cell['level']}"), area)
                for cell in bay['cells'] if cell['code'])
        frames.append(dict(index=stage['index'], label=stage['label'], boxes=boxes, move=deepcopy(stage.get('move'))))
    return dict(areas=[{k: a[k] for k in ('id', 'label', 'rows', 'columns', 'levels', 'x', 'y', 'addresses')} for a in areas],
        frames=frames, slot_cells=first.get('slot_cells'), cargo_colors=list(CARGO_COLORS))


def _tier_info(priority_group, shipper_tier):
    effective = priority_group if priority_group is not None else shipper_tier
    if effective == 1:
        return 'urgent', '긴급'
    if effective == 2:
        return 'priority', '우선'
    if effective is not None and effective >= 3:
        return 'normal', '일반'
    return 'neutral', ''


def cargo_scene_metadata(codes):
    """Read-only lookup of container color, priority tier, and basic info for 3D scenes."""
    unique = {code for code in codes if code}
    if not unique:
        return {}
    from django.utils import timezone
    from harbor_web.models import Cargo, Order
    from harbor_web.order_queue import completed_order_ids

    cargos = {c.code: c for c in Cargo.objects.filter(code__in=unique).select_related('commercial_profile')}
    if not cargos:
        return {}
    done_ids = completed_order_ids()
    open_orders = {}
    for order in (
        Order.objects.filter(cargo__code__in=unique)
        .exclude(kind='REARRANGE')
        .exclude(pk__in=done_ids)
        .select_related('cargo')
        .order_by('priority_group', 'sequence', 'pk')
    ):
        open_orders.setdefault(order.cargo.code, order)
    fallback_orders = {}
    missing = unique - set(open_orders)
    if missing:
        for order in (
            Order.objects.filter(cargo__code__in=missing)
            .exclude(kind='REARRANGE')
            .select_related('cargo')
            .order_by('-pk')
        ):
            fallback_orders.setdefault(order.cargo.code, order)

    meta = {}
    for code, cargo in cargos.items():
        open_order = open_orders.get(code)
        info_order = open_order or fallback_orders.get(code)
        priority_group = open_order.priority_group if open_order is not None else None
        priority_tier, priority_label = _tier_info(priority_group, cargo.shipper_tier)
        cutoff = ''
        if info_order is not None and info_order.cutoff_at is not None:
            local_cutoff = timezone.localtime(info_order.cutoff_at) if timezone.is_aware(info_order.cutoff_at) else info_order.cutoff_at
            cutoff = f'{local_cutoff:%m-%d %H:%M}'
        meta[code] = dict(
            color=cargo.current_commercial.color,
            color_label=CARGO_COLOR_LABELS.get(cargo.current_commercial.color, '') if cargo.current_commercial.color else '',
            color_confirmed=bool(cargo.current_commercial.color_confirmed),
            priority_group=priority_group,
            shipper_tier=cargo.shipper_tier,
            priority_tier=priority_tier,
            priority_label=priority_label,
            cargo_name=cargo.name,
            shipper=cargo.shipper_name,
            destination=cargo.destination,
            voyage=info_order.voyage_code if info_order is not None else '',
            cutoff=cutoff,
        )
    return meta


def attach_cargo_metadata(scene, metadata=None):
    """Attach read-only color, priority tier, and basic cargo metadata onto every box in a scene."""
    if not scene or not scene.get('frames'):
        return scene
    scene.setdefault('cargo_colors', list(CARGO_COLORS))
    if metadata is None:
        codes = {box['id'] for frame in scene['frames'] for box in frame.get('boxes', []) if box.get('id')}
        metadata = cargo_scene_metadata(codes)
    for frame in scene['frames']:
        for box in frame.get('boxes', []):
            info = metadata.get(box.get('id'))
            if info:
                box.update(info)
            else:
                box.setdefault('color', '')
                box.setdefault('color_label', '')
                box.setdefault('color_confirmed', False)
                box.setdefault('priority_group', None)
                box.setdefault('shipper_tier', None)
                box.setdefault('priority_tier', 'neutral')
                box.setdefault('priority_label', '')
                box.setdefault('cargo_name', '')
                box.setdefault('shipper', '')
                box.setdefault('destination', '')
                box.setdefault('voyage', '')
                box.setdefault('cutoff', '')
    return scene
