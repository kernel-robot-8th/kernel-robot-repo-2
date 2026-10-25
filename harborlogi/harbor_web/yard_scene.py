"""Read-only logical cuboids from the existing configured stage presentation."""
from copy import deepcopy


def _box(cell, area):
    return dict(id=cell['code'], area=area['id'], row=cell['row'], column=cell['column'], level=cell['level'],
        width=cell.get('colspan', 1), depth=cell.get('rowspan', 1),
        x=area['x'] + cell['column'] - 1, y=area['y'] + cell['row'] - 1, z=cell['level'] - 1,
        role=cell['role'], label=cell['label'], positions=list(cell.get('covered_positions') or [cell['position_id']]),
        footprint_revision=cell.get('footprint_revision'), orientation=cell.get('orientation', 0),
        size=cell.get('footprint_label', '1×1'))


def scene_for(stages):
    if not stages or any(not stage.get('layout') for stage in stages):
        return None
    first = stages[0]
    areas = [dict(id=first.get('area_id', 'yard'), label='장치 야드', x=0, y=0, **first['layout'])]
    # Auxiliary offsets are presentation spacing, never measured world coordinates.
    right, y = first['layout']['columns'] + 1.2, 0
    for source in first.get('auxiliary_areas', []):
        area_id = source['area_id']
        areas.append(dict(id=area_id, label={'temp': '임시 장치', 'stand': '반출대'}.get(area_id, area_id),
            x=right, y=y, **source['layout']))
        y += source['layout']['rows'] + 1.2
    for bay in first.get('auxiliary_bays', []):
        areas.append(dict(id=bay['bay_id'], label=bay.get('kind', bay['bay_id']), rows=1, columns=1,
            levels=bay['capacity'], x=right, y=y))
        y += 2.2
    by_id = {area['id']: area for area in areas}
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
    return dict(areas=[{k: a[k] for k in ('id', 'label', 'rows', 'columns', 'levels', 'x', 'y')} for a in areas],
        frames=frames)
