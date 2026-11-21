"""설치 바인딩으로 자리 주소와 물리 목표 이름을 대조한다."""
import json
from pathlib import Path
import tempfile
import unittest

from harbor.place_map import all_slots, check_slot
from harbor.process import anchor_address


def binding(slot, target):
    return dict(cargo_id='C-1', source='intake', destination=slot,
                workcell_id='cell', instance_id='arm', object_ref='C-1',
                source_ref='intake-target', target_ref=target)


class PlaceMapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'bindings.json'
        self.slot = anchor_address('yard', 1, 2, 1)
        self.write([binding(self.slot, 'installed-target')])

    def write(self, items):
        self.path.write_text(json.dumps(items), encoding='utf-8')

    def test_binding_file_round_trip(self):
        self.assertEqual(all_slots(self.path), (self.slot,))
        self.assertEqual(check_slot(self.slot, self.path), 'installed-target')

    def test_spatial_binding_uses_anchor(self):
        item = binding(self.slot, self.slot)
        del item['destination']
        item.update(footprint_revision=1, area_id='yard', row=1, column=2,
                    level=1, orientation=0, yard_arm='A')
        self.write([item])
        self.assertEqual(check_slot(self.slot, self.path), self.slot)

    def test_missing_slot_is_an_error(self):
        with self.assertRaisesRegex(ValueError, '대응'):
            check_slot('yard-r1-c3-l1', self.path)

    def test_invalid_address_is_an_error(self):
        for slot in ('yard', 'yard-r0-c2-l1', 'yard-r1-c2-l-1',
                     'yard-r01-c2-l1', 'yard-r1-c2-l1\n', None):
            with self.subTest(slot=slot), self.assertRaisesRegex(ValueError, '문법'):
                check_slot(slot, self.path)

    def test_conflicting_targets_are_an_error(self):
        self.write([binding(self.slot, 'one'), binding(self.slot, 'two')])
        with self.assertRaises(ValueError):
            check_slot(self.slot, self.path)

    def test_same_target_for_multiple_cargo_bindings_is_allowed(self):
        item = binding(self.slot, 'installed-target')
        self.write([item, dict(item, cargo_id='C-2', object_ref='C-2')])
        self.assertEqual(check_slot(self.slot, self.path), 'installed-target')

    def test_invalid_or_unreadable_file_is_an_error(self):
        for items in ({}, [None], [dict(target_ref=[])], [binding(self.slot, '')]):
            self.write(items)
            with self.subTest(items=items), self.assertRaises(ValueError):
                check_slot(self.slot, self.path)
        self.path.write_text('{', encoding='utf-8')
        with self.assertRaises(ValueError):
            check_slot(self.slot, self.path)
        self.path.unlink()
        with self.assertRaises(ValueError):
            check_slot(self.slot, self.path)

    def test_simulation_template_does_not_invent_installed_slots(self):
        self.write([dict(simulation_cell_template=True, workcell_id='cell', instances={'A': 'arm'})])
        self.assertEqual(all_slots(self.path), ())
        with self.assertRaises(ValueError):
            check_slot(self.slot, self.path)
