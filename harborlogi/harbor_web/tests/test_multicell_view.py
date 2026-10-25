import json
from pathlib import Path

from django.test import SimpleTestCase
from django.test import TestCase
from django.contrib.auth.models import User, Group
from django.utils import timezone

from harbor.layout import YardLayout
from harbor.spatial import Area, CargoShape, Placement, SpatialSnapshot
from harbor_web.yard_view import spatial_yard_view
from harbor_web.tests import test_multicell_services as fixtures
from harbor_web import services
from harbor_web.models import CargoFootprint, Audit


class SpatialViewTests(SimpleTestCase):
    def snapshot(self):
        layout = YardLayout.from_dict(json.loads((Path(__file__).resolve().parents[2] / 'config/yard-layout.json').read_text(encoding='utf-8')))
        return SpatialSnapshot(1, timezone.now(), layout,
            (Area('yard', 3, 3, 3), Area('temp', 1, 3, 3), Area('stand', 1, 3, 1)),
            (CargoShape('C1', 2, 11), CargoShape('C2', 1, 12), CargoShape('C5', 3, 15)),
            (Placement('C1', 'yard', 1, 1, 1), Placement('C2', 'yard', 1, 1, 2), Placement('C5', 'yard', 3, 1, 1)))

    def test_one_cargo_is_one_merged_cell_with_full_address_coverage(self):
        view = spatial_yard_view(self.snapshot(), target='C1')
        cells = [c for row in view['layers'][0]['rows'] for c in row['cells']]
        self.assertEqual([c['code'] for c in cells if c['code']], ['C1', 'C5'])
        self.assertEqual(sum(c['colspan'] * c['rowspan'] for c in cells), 9)
        target = next(c for c in cells if c['code'] == 'C1')
        self.assertEqual((target['colspan'], target['rowspan']), (2, 1))
        self.assertEqual(target['covered_positions'], ['Y11-L1', 'Y12-L1'])
        self.assertEqual(target['footprint_revision'], 11)
        self.assertEqual(view['cargo_count'], 3)
        self.assertEqual(view['occupied_units'], 6)

    def test_orientation_is_reflected_without_splitting_identity(self):
        snapshot = self.snapshot()
        snapshot = SpatialSnapshot(snapshot.revision, snapshot.observed_at, snapshot.layout, snapshot.areas,
            (CargoShape('R', 3, 2),), (Placement('R', 'yard', 1, 1, 1, 90),))
        rows = spatial_yard_view(snapshot)['layers'][0]['rows']
        cargo = [c for row in rows for c in row['cells'] if c['code']]
        self.assertEqual(len(cargo), 1)
        self.assertEqual((cargo[0]['colspan'], cargo[0]['rowspan']), (1, 3))
        self.assertEqual(cargo[0]['covered_positions'], ['Y11-L1', 'Y21-L1', 'Y31-L1'])

    def test_auxiliary_footprint_and_observation_are_preserved(self):
        snapshot = self.snapshot()
        changed = snapshot.with_placements(tuple(
            Placement(p.cargo_id, 'stand', 1, 1, 1) if p.cargo_id == 'C1' else
            Placement(p.cargo_id, 'yard', 1, 1, 1) if p.cargo_id == 'C2' else p for p in snapshot.placements))
        view = spatial_yard_view(changed, target='C1')
        stand = next(a for a in view['auxiliary_areas'] if a['area_id'] == 'stand')
        target = stand['layers'][0]['rows'][0]['cells'][0]
        self.assertEqual(target['code'], 'C1')
        self.assertEqual(target['colspan'], 2)
        self.assertEqual((view['cargo_count'], view['occupied_units']), (2, 4))
        self.assertEqual((stand['cargo_count'], stand['occupied_units']), (1, 2))
        self.assertEqual((view['total_cargo_count'], view['total_occupied_units']), (3, 6))
        self.assertEqual(snapshot.placement('C1').area_id, 'yard')


class MulticellPagesTests(TestCase):
    setUp = fixtures.MulticellServicesTests.setUp
    cargo = fixtures.MulticellServicesTests.cargo
    condition = fixtures.MulticellServicesTests.condition
    payload = fixtures.MulticellServicesTests.payload
    observe = fixtures.MulticellServicesTests.observe
    order = fixtures.MulticellServicesTests.order

    def test_spatial_inventory_and_workbench_render_atomic_footprint(self):
        cargo = self.cargo()
        observation = self.observe(self.condition(), self.payload(cargo))
        order = self.order(cargo)
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)
        self.client.force_login(self.operator)
        before = (CargoFootprint.objects.count(), Audit.objects.count())
        for path in ('/inventory/', f'/requests/{order.pk}/', f'/plans/{plan.pk}/'):
            response = self.client.get(path)
            self.assertContains(response, '2×1')
            self.assertContains(response, 'colspan="2"')
            self.assertContains(response, 'data-grid-area="yard"')
            self.assertNotContains(response, '현재 계획의 인계 요청 준비')
        self.assertEqual(before, (CargoFootprint.objects.count(), Audit.objects.count()))

    def test_footprint_revision_ui_preserves_pinned_observation_and_permissions(self):
        cargo = self.cargo()
        observation = self.observe(self.condition(), self.payload(cargo))
        self.client.force_login(self.operator)
        path = f'/cargo/{cargo.pk}/footprint/'
        self.assertContains(self.client.get(path), '2×1')
        self.assertEqual(self.client.post(path, dict(length_cells=3)).status_code, 302)
        self.assertEqual(CargoFootprint.objects.filter(cargo=cargo).count(), 2)
        self.assertEqual(services.spatial_snapshot(observation).shape(cargo.code).length_cells, 2)
        self.assertContains(self.client.get('/cargo/'), '3×1')
        viewer = User.objects.create_user('footprint-viewer')
        viewer.groups.add(Group.objects.get_or_create(name='viewer')[0])
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post(path, dict(length_cells=1)).status_code, 403)

    def test_held_inbound_spatial_plan_still_shows_its_pinned_observation(self):
        cargo = self.cargo()
        observed = self.observe(self.condition(), self.payload(cargo))
        order = services.create_order(self.operator, cargo=cargo, kind='INBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED')
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observed.pk)
        self.assertEqual(plan.result['reason'], 'INBOUND_PLANNER_NOT_AVAILABLE')
        self.client.force_login(self.operator)
        response = self.client.get(f'/plans/{plan.pk}/')
        self.assertContains(response, 'data-cargo-id="C1"')
        self.assertContains(response, 'colspan="2"')
        self.assertNotContains(response, '현재 계획의 인계 요청 준비')
