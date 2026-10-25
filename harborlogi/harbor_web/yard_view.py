"""Present configured logical addresses without inferring robot geometry."""
from copy import deepcopy

from harbor.layout import YardLayout


def yard_view(bays, policy, *, target='', blockers=()):
    result = deepcopy(bays)
    for bay in result:
        bay['kind'] = ('반출대' if bay['bay_id'] == policy['outbound_stand'] else '컨베이어' if bay['bay_id'] == policy['conveyor']
                       else '임시 장치' if bay['bay_id'] in policy['temporary_bays'] else '장치 칸')
        bay['free'] = bay['capacity'] - len(bay['cargo'])
        bay['cells'] = [dict(code=code, level=i + 1,
            role='target' if code == target else 'blocker' if code in blockers else 'stored',
            label='반출 대상' if code == target else '재취급 대상' if code in blockers else '보관 화물')
            for i, code in enumerate(bay['cargo'])]
    if 'yard_layout' not in policy:
        return dict(bays=result, layout=None, layers=[], auxiliary_bays=[])
    layout = YardLayout.from_dict(policy['yard_layout'])
    by_id = {bay['bay_id']: bay for bay in result}
    layers = []
    for level in range(1, layout.levels + 1):
        rows = []
        for row in range(1, layout.rows + 1):
            cells = []
            for column in range(1, layout.columns + 1):
                address = layout.at(row, column)
                bay = by_id[address.bay_id]
                cargo = bay['cells'][level - 1] if level <= len(bay['cells']) else dict(code=None, role='vacant', label='빈 위치')
                cells.append(dict(cargo, bay_id=address.bay_id, row=row, column=column, level=level,
                                  position_id=f'{address.bay_id}-L{level}', kind=bay['kind'], stack_height=len(bay['cargo'])))
            rows.append(dict(number=row, cells=cells))
        layers.append(dict(level=level, rows=rows))
    mapped = {c.bay_id for c in layout.cells}
    return dict(bays=result, layout=dict(rows=layout.rows, columns=layout.columns, levels=layout.levels, positions=len(layout.cells) * layout.levels),
                layers=layers, auxiliary_bays=[b for b in result if b['bay_id'] not in mapped])
