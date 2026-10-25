import unittest
from copy import deepcopy
from unittest.mock import patch

from harbor.layout import YardLayout
from harbor.model import Bay


def layout_payload():
    return dict(rows=3, columns=3, levels=3, cells=[
        dict(bay_id=f'Y{row}{column}', row=row, column=column)
        for row in range(1, 4) for column in range(1, 4)])


class YardLayoutTests(unittest.TestCase):
    def test_invalid_dimensions_or_cell_count_reject_before_constructing_cells(self):
        class UnscannableCells(list):
            def __iter__(self):
                raise AssertionError('Rejected cell counts must not be scanned')

        for fields in (dict(rows=True), dict(rows=13), dict(columns=0), dict(levels=7),
                       dict(cells=UnscannableCells([None] * 145))):
            payload = {**layout_payload(), **fields}
            with self.subTest(fields=fields), patch('harbor.layout.YardCell') as cell:
                with self.assertRaises(ValueError):
                    YardLayout.from_dict(payload)
                cell.assert_not_called()

    def test_configured_ids_and_coordinates_are_preserved_independent_of_input_order(self):
        payload = layout_payload()
        payload['cells'].reverse()
        layout = YardLayout.from_dict(payload)
        self.assertEqual((layout.rows, layout.columns, layout.levels), (3, 3, 3))
        self.assertEqual(layout.at(1, 1).bay_id, 'Y11')
        self.assertEqual(layout.at(3, 3).bay_id, 'Y33')
        self.assertEqual(len(layout.cells) * layout.levels, 27)

    def test_missing_duplicate_out_of_bounds_and_wrong_types_are_rejected(self):
        bad = []
        p = layout_payload(); p['cells'].pop(); bad.append(p)
        p = layout_payload(); p['cells'][0]['row'] = 0; bad.append(p)
        p = layout_payload(); p['cells'][0]['column'] = 4; bad.append(p)
        p = layout_payload(); p['cells'][0]['row'] = True; bad.append(p)
        p = layout_payload(); p['cells'][0]['bay_id'] = 'Y12'; bad.append(p)
        p = layout_payload(); p['cells'][0].update(row=1, column=2); bad.append(p)
        p = layout_payload(); p['levels'] = 7; bad.append(p)
        p = layout_payload(); p['cells'][0]['physical_x'] = 1; bad.append(p)
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                YardLayout.from_dict(payload)

    def test_observation_must_cover_configured_columns_with_three_levels(self):
        layout = YardLayout.from_dict(layout_payload())
        bays = tuple(Bay(cell.bay_id, (), 3) for cell in layout.cells)
        layout.validate_bays(bays, auxiliary_ids=('stand', 'temp'))
        layout.validate_bays(bays + (Bay('stand', (), 1),), auxiliary_ids=('stand', 'temp'))
        for invalid in (bays[:-1], bays[:-1] + (Bay('Y33', (), 1),), bays + (Bay('unknown', (), 3),)):
            with self.subTest(bays=invalid), self.assertRaises(ValueError):
                layout.validate_bays(invalid, auxiliary_ids=('stand', 'temp'))

    def test_input_cannot_change_layout_after_construction(self):
        payload = layout_payload()
        original = deepcopy(payload)
        layout = YardLayout.from_dict(payload)
        payload['cells'][0]['bay_id'] = 'Changed'
        self.assertEqual(layout.at(1, 1).bay_id, original['cells'][0]['bay_id'])
