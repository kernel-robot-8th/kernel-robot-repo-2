from copy import deepcopy

from django.test import SimpleTestCase, TestCase

from harbor.spatial import CargoShape, Placement, SpatialSnapshot
from harbor_web import services
from harbor_web.tests import test_multicell_view, test_yard_grid
from harbor_web.workspace import workspace_context
from harbor_web.yard_scene import scene_for
from harbor_web.yard_view import spatial_yard_view


class YardSceneTests(SimpleTestCase):
    def stage(self, snapshot=None, index=0, move=None):
        snapshot = snapshot or test_multicell_view.SpatialViewTests().snapshot()
        return dict(spatial_yard_view(snapshot, target='C1'), index=index, label=f'Stage {index}', move=move)

    def test_multicell_boxes_keep_atomic_identity_and_exact_positions(self):
        stage = self.stage()
        original = deepcopy(stage)
        scene = scene_for([stage])
        boxes = {b['id']: b for b in scene['frames'][0]['boxes']}
        self.assertEqual(set(boxes), {'C1', 'C2', 'C5'})
        self.assertEqual((boxes['C1']['width'], boxes['C1']['depth']), (2, 1))
        self.assertEqual((boxes['C5']['width'], boxes['C5']['depth']), (3, 1))
        self.assertEqual((boxes['C2']['x'], boxes['C2']['y'], boxes['C2']['z']), (0, 0, 1))
        self.assertEqual(boxes['C1']['positions'], ['Y11-L1', 'Y12-L1'])
        addresses = scene['areas'][0]['addresses']
        self.assertEqual(addresses['1,2,1'], 'Y12-L1')
        self.assertEqual(addresses['1,1,2'], 'Y11-L2')
        self.assertEqual(addresses['3,3,1'], 'Y33-L1')
        self.assertEqual(boxes['C1']['footprint_revision'], 11)
        self.assertEqual(boxes['C1']['role'], 'target')
        boxes['C1']['positions'].append('presentation-only')
        self.assertEqual(stage, original)

    def test_rotation_uses_rowspan_and_unknown_area_id_is_preserved(self):
        snapshot = test_multicell_view.SpatialViewTests().snapshot()
        snapshot = SpatialSnapshot(snapshot.revision, snapshot.observed_at, snapshot.layout, snapshot.areas,
            (CargoShape('R', 3, 2),), (Placement('R', 'yard', 1, 1, 1, 90),))
        stage = self.stage(snapshot)
        stage['auxiliary_areas'][0]['area_id'] = 'unfamiliar'
        scene = scene_for([stage])
        box = scene['frames'][0]['boxes'][0]
        self.assertEqual((box['width'], box['depth']), (1, 3))
        self.assertEqual((box['orientation'], box['size']), (90, '3×1'))
        self.assertEqual(box['positions'], ['Y11-L1', 'Y21-L1', 'Y31-L1'])
        self.assertEqual(scene['areas'][1]['id'], 'unfamiliar')
        self.assertEqual(scene['areas'][1]['label'], 'unfamiliar')
        self.assertEqual((scene['areas'][1]['x'], scene['areas'][1]['y']), (4.2, 0))
        self.assertEqual(scene['areas'][2]['y'], 2.2)

    def test_every_frame_uses_its_own_placement_and_move(self):
        snapshot = test_multicell_view.SpatialViewTests().snapshot()
        moved = snapshot.with_placements(tuple(Placement(p.cargo_id, 'stand', 1, 1, 1) if p.cargo_id == 'C1' else
            Placement(p.cargo_id, 'yard', 1, 1, 1) if p.cargo_id == 'C2' else p
            for p in snapshot.placements))
        move = dict(cargo_id='C1', source='Y11', destination='stand')
        stages = [self.stage(snapshot), self.stage(moved, 1, move)]
        scene = scene_for(stages)
        self.assertEqual([f['index'] for f in scene['frames']], [0, 1])
        self.assertEqual([f['label'] for f in scene['frames']], ['Stage 0', 'Stage 1'])
        self.assertIsNone(scene['frames'][0]['move'])
        self.assertEqual(scene['frames'][1]['move'], move)
        for frame in scene['frames']:
            self.assertEqual({b['id'] for b in frame['boxes']}, {'C1', 'C2', 'C5'})
        box = next(b for b in scene['frames'][1]['boxes'] if b['id'] == 'C1')
        self.assertEqual((box['area'], box['x'], box['y'], box['width']), ('stand', 4.2, 2.2, 2))
        scene['frames'][1]['move']['cargo_id'] = 'changed'
        self.assertEqual(stages[1]['move'], move)

    def test_unconfigured_geometry_has_no_scene(self):
        self.assertIsNone(scene_for([]))
        self.assertIsNone(scene_for([dict(layout=None, bays=[dict(bay_id='Y99', cargo=['C1'])])]))


class YardScenePagesTests(TestCase):
    setUp = test_yard_grid.ConfiguredYardTests.setUp
    cargo = test_yard_grid.ConfiguredYardTests.cargo
    order = test_yard_grid.ConfiguredYardTests.order
    condition = test_yard_grid.ConfiguredYardTests.condition
    condition_payload = test_yard_grid.ConfiguredYardTests.condition_payload
    grid = test_yard_grid.ConfiguredYardTests.grid

    def test_context_preserves_all_planned_frames_and_observation(self):
        order, observation = self.grid()
        original = deepcopy(observation.payload)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        context = workspace_context(order, plan)
        scene = context['yard_scene']
        self.assertEqual(len(scene['frames']), len(context['yard_stages']))
        self.assertEqual([(a['id'], a['levels']) for a in scene['areas']], [('yard', 3), ('temp', 3), ('stand', 1)])
        for frame in scene['frames']:
            self.assertEqual({b['id'] for b in frame['boxes']}, {'C1', 'C2'})
        temp_box = next(b for b in scene['frames'][1]['boxes'] if b['id'] == 'C2')
        self.assertEqual((temp_box['area'], temp_box['row'], temp_box['column'], temp_box['level']), ('temp', 1, 1, 1))
        self.assertEqual(temp_box['positions'], ['temp-L1'])
        self.client.force_login(self.operator)
        response = self.client.get(f'/inventory/?observation={observation.pk}')
        inventory_boxes = response.context['yard_scene']['frames'][0]['boxes']
        geometry = lambda boxes: [(b['id'], b['x'], b['y'], b['z'], b['positions']) for b in boxes]
        self.assertEqual(geometry(inventory_boxes), geometry(scene['frames'][0]['boxes']))
        observation.refresh_from_db()
        self.assertEqual(observation.payload, original)
