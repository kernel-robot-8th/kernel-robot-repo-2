"""Yard page upcoming plans, move arrows, and layout tests (WP08)."""
import json
from pathlib import Path
import re

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from harbor_web import rearrangement, services
from harbor_web.models import Cargo, CargoFootprint, CellPlanDispatch, Command, Order, Plan
from harbor_web.tests.test_control_board import site_policy
from harbor_web.yard_pages import outbound_chain_replay, yard_page_context

ROOT = Path(__file__).resolve().parents[2]


class YardPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('yard-admin')
        self.operator = User.objects.create_user('yard-operator')
        self.viewer = User.objects.create_user('yard-viewer')
        for user, roles in (
            (self.admin, ('admin', 'viewer')),
            (self.operator, ('operator', 'viewer')),
            (self.viewer, ('viewer',)),
        ):
            user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in roles])
        self.condition = services.register_condition(
            self.admin, name='3x6 site', calibration_ref='synthetic', payload=site_policy(),
        )
        self.now = timezone.now()
        self.placements = []
        for code, row, column, level in (
            ('K1', 1, 1, 1), ('K2', 1, 1, 2),
            ('K3', 1, 4, 1), ('K4', 1, 4, 2),
            ('K5', 2, 1, 1), ('K6', 2, 4, 1),
        ):
            services.register_cargo(
                self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012',
                destination='컨베이어', length_cells=3,
            )
            footprint = CargoFootprint.objects.filter(cargo__code=code).latest('pk')
            self.placements.append(dict(
                cargo_id=code, area_id='yard', row=row, column=column, level=level,
                orientation=0, footprint_revision=footprint.pk,
            ))
        self.observation = services.record_observation(
            self.admin, source_id='site-cam', source_revision='1', observed_at=self.now,
            provenance='SYNTHETIC', condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                placements=self.placements,
                holdings=[],
            ),
        )
        self.client.force_login(self.operator)

    def test_empty_observation_returns_empty_yard_page_context(self):
        self.assertEqual(
            yard_page_context(None),
            dict(upcoming=[], active_arrows=[], active_upcoming=None, outbound_chain=None, current_job_key=''),
        )
        self.assertIsNone(outbound_chain_replay(None))
        # Observation with no open orders also yields empty current_job_key.
        self.assertEqual(yard_page_context(self.observation)['current_job_key'], '')

    def test_outbound_rearrange_inbound_plans_and_filters(self):
        outbound = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        outbound_plan = services.make_plan(
            self.operator, order_id=outbound.pk, expected_version=outbound.version,
            observation_id=self.observation.pk, now=self.now,
        )
        self.assertEqual(outbound_plan.result['status'], 'READY')

        # Completed outbound order must be excluded even if it has a READY plan.
        done_order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K5'), kind='OUTBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        done_plan = services.make_plan(
            self.operator, order_id=done_order.pk, expected_version=done_order.version,
            observation_id=self.observation.pk, now=self.now,
        )
        Command.objects.create(
            order=done_order, plan=done_plan, actor=self.operator, plan_version=1,
            ledger_identity='l', fleet_origin='http://fleet.example', fleet_principal='p',
            body_digest='a' * 64, binding={}, state='COMPLETED',
        )

        # Superseded READY plan followed by HOLD on the same observation must be excluded.
        superseded = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K6'), kind='OUTBOUND',
            priority_group=3, sequence=1, tie_policy='FIXED',
        )
        Plan.objects.create(
            created_by=self.operator, order=superseded, order_version=superseded.version,
            observation=self.observation, condition=self.condition, queue_digest='d1',
            result=dict(status='READY', reason='OK', moves=[]),
        )
        Plan.objects.create(
            created_by=self.operator, order=superseded, order_version=superseded.version,
            observation=self.observation, condition=self.condition, queue_digest='d2',
            result=dict(status='HOLD', reason='PRIORITY_ORDER_BLOCKED', moves=[]),
        )

        # Inbound order with a READY recommendation at 2행 1열 3층 (row=1, column=4, level=3).
        services.register_cargo(
            self.operator, code='N1', name='N1', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        inbound = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='N1'), kind='INBOUND',
            priority_group=1, sequence=2, tie_policy='FIXED',
        )
        inbound_plan = Plan.objects.create(
            created_by=self.operator, order=inbound, order_version=inbound.version,
            observation=self.observation, condition=self.condition, queue_digest='in',
            result=dict(
                plan_kind='inbound-placement',
                status='READY',
                reason='PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE',
                recommendation=dict(cargo_id='N1', area_id='yard', row=1, column=4, level=3, orientation=0),
            ),
        )

        # Rearrange order (order.cargo is None) with READY moves.
        rearrange_order = Order.objects.create(created_by=self.operator, kind='REARRANGE')
        rearrange_plan = Plan.objects.create(
            created_by=self.operator, order=rearrange_order, order_version=rearrange_order.version,
            observation=self.observation, condition=self.condition, queue_digest='rr',
            result=dict(
                plan_kind='rearrangement',
                status='READY',
                reason='PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE',
                moves=[dict(
                    cargo_id='K4', source='Y14-L2', destination='Y21-L2',
                    primary_arm='A', alternate_arms=['B'],
                    source_placement=dict(cargo_id='K4', area_id='yard', row=1, column=4, level=2, orientation=0),
                    destination_placement=dict(cargo_id='K4', area_id='yard', row=2, column=1, level=2, orientation=0),
                    phase_label='재배치',
                    footprint_revision=CargoFootprint.objects.get(cargo__code='K4').pk,
                )],
            ),
        )

        # Plan on another observation must be excluded when viewing self.observation.
        other_obs = services.record_observation(
            self.admin, source_id='site-cam', source_revision='2', observed_at=self.now,
            provenance='SYNTHETIC', condition=self.condition, payload=self.observation.payload,
        )
        other_order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K2'), kind='OUTBOUND',
            priority_group=2, sequence=2, tie_policy='FIXED',
        )
        services.make_plan(
            self.operator, order_id=other_order.pk, expected_version=other_order.version,
            observation_id=other_obs.pk, now=self.now,
        )

        context = yard_page_context(self.observation)
        items = {item['order'].pk: item for item in context['upcoming']}
        # Open orders appear in queue order (completed done_order is excluded).
        # rearrange_order defaults to priority_group=1, sequence=1, right after outbound.
        self.assertEqual(
            [item['order'].pk for item in context['upcoming']],
            [outbound.pk, rearrange_order.pk, inbound.pk, other_order.pk, superseded.pk],
        )
        self.assertNotIn(done_order.pk, items)

        out_item = items[outbound.pk]
        self.assertEqual(len(out_item['stages']), len(outbound_plan.result['moves']) + 1)
        self.assertIsNotNone(out_item['scene'])
        self.assertEqual(out_item['scene_id'], f'upcoming-scene-{outbound_plan.pk}')
        self.assertEqual(len(out_item['arrows']), len(outbound_plan.result['moves']))
        for arrow in out_item['arrows']:
            self.assertEqual(set(arrow), {'number', 'cargo', 'phase', 'source', 'destination', 'arm'})
        self.assertTrue(any(a['phase'] == '반출' and a['destination'] == '반출대' for a in out_item['arrows']))

        in_item = items[inbound.pk]
        self.assertEqual(in_item['target_label'], '→ yard-r1-c4-l3')
        self.assertIsNotNone(in_item['scene'])
        self.assertEqual(len(in_item['scene']['frames']), 2)
        self.assertEqual(in_item['scene']['frames'][1]['move']['source'], 'belt')
        self.assertEqual(in_item['scene']['frames'][1]['move']['destination'], 'yard-r1-c4-l3')
        self.assertEqual(len(in_item['arrows']), 1)
        self.assertEqual(in_item['arrows'][0]['phase'], '입고')

        # Open orders without a READY plan on self.observation are previewed in memory without saving Plans.
        self.assertIsNotNone(items[other_order.pk]['scene'])
        self.assertIsNone(items[other_order.pk]['plan'])
        self.assertIsNotNone(items[superseded.pk]['scene'])
        self.assertIsNone(items[superseded.pk]['plan'])

        rr_item = items[rearrange_order.pk]
        self.assertIsNotNone(rr_item['scene'])
        self.assertEqual(len(rr_item['stages']), 2)
        self.assertEqual(rr_item['arrows'][0]['cargo'], 'K4')
        self.assertEqual(rr_item['arrows'][0]['phase'], '재배치')

        response = self.client.get(f'/inventory/?observation={self.observation.pk}')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<h1>야드</h1><p>지금 어디에 무엇이 있나 · 화살은 지금 하는 일 하나</p>', html)
        self.assertIn('class="yard-page-layout"', html)
        self.assertIn('data-yard-upcoming-preview', html)
        self.assertIn('class="yard-upcoming-list"', html)
        self.assertIn('class="inventory-yard yard-inline-3d"', html)
        self.assertIn('data-yard-arrows', html)
        self.assertEqual(html.count('class="yard-topview"'), 1)
        self.assertIn('<details class="fold yard-source-fold">', html)
        self.assertIn('<summary>기준 · 출처 선택(지원 담당자용)</summary>', html)
        self.assertIn(f'id="upcoming-scene-{outbound_plan.pk}"', html)
        self.assertIn(f'id="upcoming-scene-{rearrange_plan.pk}"', html)
        self.assertIn(f'id="upcoming-scene-{inbound_plan.pk}"', html)
        self.assertIn('→ yard-r1-c4-l3', html)
        self.assertIn('data-yard-stages', html)
        self.assertIn(f'data-upcoming-plan="plan-{outbound_plan.pk}"', html)
        self.assertIn(f'data-upcoming-scene="upcoming-scene-{outbound_plan.pk}"', html)
        self.assertIsNone(re.search(r'\sstyle=', html))

    def test_rearrangement_preview_includes_3d_viewer_and_stage_buttons(self):
        # Create a 3x3 spatial observation where bridging cargo BR can rearrange onto the floor (G=1).
        cells = [dict(bay_id=f'Y{r}{c}', row=r, column=c) for r in range(1, 4) for c in range(1, 4)]
        condition = services.register_condition(self.admin, name='Grid R2', calibration_ref='declared', payload={
            'outbound_stand': 'out', 'temporary_bays': ['temp'], 'conveyor': 'belt',
            'allowed_moves': [['Y11', 'out']], 'max_observation_age_seconds': 3600,
            'arm_reach': [['A', c['bay_id']] for c in cells] + [['A', 'temp'], ['A', 'out']],
            'yard_layout': dict(rows=3, columns=3, levels=3, cells=cells),
            'spatial_areas': [dict(area_id=a, rows=3, columns=3, levels=3) for a in ['temp', 'out']],
            'spatial_moves': [['yard', 'yard'], ['yard', 'out'], ['yard', 'temp'], ['temp', 'yard']],
        })
        placements = []
        for code, length, column, level in (('L1', 1, 1, 1), ('L2', 1, 2, 1), ('BR', 2, 1, 2)):
            cargo = services.register_cargo(self.operator, code=code, name=code, weight_kg=1, volume_m3=1,
                                            destination='yard', length_cells=length)
            placements.append(dict(cargo_id=code, area_id='yard', row=1, column=column, level=level, orientation=0,
                                   footprint_revision=CargoFootprint.objects.get(cargo=cargo).pk))
        services.record_observation(
            self.admin, source_id='r2-cam', source_revision='1', observed_at=self.now,
            provenance='SYNTHETIC', condition=condition,
            payload=dict(schema='grid-placement-v1', observed_areas=['yard', 'temp', 'out'],
                         holdings=[], placements=placements),
        )
        response = self.client.get('/rearrangement/')
        self.assertEqual(response.status_code, 200)
        preview = response.context['preview']
        self.assertIsNotNone(preview)
        self.assertEqual(preview['proposal'].status, 'READY')
        self.assertTrue(preview['yard_scene']['frames'])
        self.assertEqual(len(preview['yard_stages']), len(preview['proposal'].moves) + 1)
        html = response.content.decode()
        self.assertIn('data-yard-preview', html)
        self.assertNotIn('data-yard-upcoming-preview', html)
        self.assertIn('data-yard-3d', html)
        self.assertIn('data-yard-stage="0"', html)
        self.assertIn('data-yard-stage="1"', html)
        self.assertIn('id="yard-scene-data"', html)
        self.assertIsNone(re.search(r'\sstyle=', html))

    def test_outbound_chain_replay_chains_multi_order_outbound_and_excludes_inbound(self):
        # Tier 1: K1 (buried under K2) then K2; Tier 2: K3; plus one open INBOUND order (N1).
        # tier_recommendations recommends K2 -> K1 in tier 1 (0 rehandles), then tier 2 chains K3.
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K2'), kind='OUTBOUND',
            priority_group=1, sequence=2, tie_policy='FIXED',
        )
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K3'), kind='OUTBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        services.register_cargo(
            self.operator, code='N1', name='N1', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        inbound = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='N1'), kind='INBOUND',
            priority_group=1, sequence=3, tie_policy='FIXED',
        )
        Plan.objects.create(
            created_by=self.operator, order=inbound, order_version=inbound.version,
            observation=self.observation, condition=self.condition, queue_digest='in-chain',
            result=dict(
                plan_kind='inbound-placement', status='READY',
                reason='PROPOSAL_REQUIRES_ADMISSION_AND_EVIDENCE',
                recommendation=dict(cargo_id='N1', area_id='yard', row=1, column=4, level=3, orientation=0),
            ),
        )

        chain = outbound_chain_replay(self.observation)
        self.assertIsNotNone(chain)
        self.assertEqual(chain['scene_id'], 'upcoming-scene-outbound-chain')
        self.assertEqual(chain['scene']['kind'], 'outbound-chain')
        # Follows tier_recommendations best.steps (K2 before K1) then chains tier 2 (K3 with K4 rehandled), never N1.
        self.assertEqual(chain['cargo_codes'], ['K2', 'K1', 'K3'])
        self.assertEqual(len(chain['arrows']), 5)
        self.assertEqual(chain['scene']['rehandle'], ['K4'])
        self.assertEqual(len(chain['scene']['frames']), len(chain['arrows']) + 1)
        self.assertNotIn('N1', {arrow['cargo'] for arrow in chain['arrows']})
        for frame in chain['scene']['frames']:
            self.assertNotIn('N1', {box['id'] for box in frame['boxes']})
        # K2 reaches stand in frame 1 and leaves before K1; K1 reaches stand in frame 2 and leaves before K3.
        self.assertTrue(any(b['id'] == 'K2' and b['area'] == 'stand' for b in chain['scene']['frames'][1]['boxes']))
        self.assertNotIn('K2', {b['id'] for b in chain['scene']['frames'][2]['boxes']})
        self.assertTrue(any(b['id'] == 'K1' and b['area'] == 'stand' for b in chain['scene']['frames'][2]['boxes']))
        self.assertNotIn('K1', {b['id'] for b in chain['scene']['frames'][3]['boxes']})
        self.assertTrue(any(b['id'] == 'K3' and b['area'] == 'stand' for b in chain['scene']['frames'][4]['boxes']))
        self.assertEqual(chain['arrows'][-1]['cargo'], 'K4')
        self.assertEqual(chain['arrows'][-1]['phase'], '복귀')

        response = self.client.get(f'/inventory/?observation={self.observation.pk}')
        html = response.content.decode()
        self.assertIn('남은 반출 연쇄 3D', html)
        self.assertIn('data-upcoming-plan="outbound-chain"', html)
        self.assertIn('data-upcoming-scene="upcoming-scene-outbound-chain"', html)
        self.assertIn('id="upcoming-scene-outbound-chain"', html)
        self.assertIsNone(re.search(r'\sstyle=', html))

    def test_outbound_chain_replay_returns_none_with_only_inbound_and_one_outbound(self):
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K3'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        services.register_cargo(
            self.operator, code='N2', name='N2', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='N2'), kind='INBOUND',
            priority_group=1, sequence=2, tie_policy='FIXED',
        )
        self.assertIsNone(outbound_chain_replay(self.observation))
        html = self.client.get(f'/inventory/?observation={self.observation.pk}').content.decode()
        self.assertNotIn('data-upcoming-plan="outbound-chain"', html)

    def test_outbound_chain_replay_includes_rehandles_across_tiers(self):
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K3'), kind='OUTBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        chain = outbound_chain_replay(self.observation)
        self.assertIsNotNone(chain)
        self.assertEqual(chain['cargo_codes'], ['K1', 'K3'])
        self.assertEqual(len(chain['arrows']), 6)
        self.assertEqual(chain['scene']['rehandle'], ['K2', 'K4'])
        self.assertEqual([a['phase'] for a in chain['arrows']], ['재취급', '반출', '복귀', '재취급', '반출', '복귀'])

    def test_active_command_populates_yard_arrows_on_page(self):
        outbound = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        plan = services.make_plan(
            self.operator, order_id=outbound.pk, expected_version=outbound.version,
            observation_id=self.observation.pk, now=self.now,
        )
        Command.objects.create(
            order=outbound, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='l', fleet_origin='http://fleet.example', fleet_principal='p',
            body_digest='b' * 64, binding={}, state='DISPATCHING',
        )
        response = self.client.get(f'/inventory/?observation={self.observation.pk}')
        html = response.content.decode()
        self.assertIn('class="yard-arrows-list"', html)
        self.assertIn('K1', html)
        self.assertIn('반출대', html)
        self.assertTrue(response.context['active_arrows'])

    def test_yard_page_css_has_no_raw_hex_or_px_literals(self):
        css = (ROOT / 'harbor_web/static/harbor_web/yard-page.css').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}', css))
        body_without_media = re.sub(r'@media[^{]+\{', '', css)
        self.assertIsNone(re.search(r'\b\d+(?:\.\d+)?px\b', body_without_media))

    def test_hold_preview_and_active_order_promotion(self):
        # Unobserved outbound cargo produces a concise HOLD reason without saving a Plan.
        services.register_cargo(
            self.operator, code='MISSING-1', name='MISSING-1', weight_kg=2, volume_m3='0.012',
            destination='컨베이어', length_cells=3,
        )
        missing_order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='MISSING-1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        running_order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K3'), kind='OUTBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        CellPlanDispatch.objects.create(
            order=running_order,
            observation=self.observation,
            dispatch_mode='HARBOR_PLAN',
            directive_mode='outbound',
            request_key='cp-yard-active-1',
            request_payload={},
            order_source='harbor',
            state='RUNNING',
            created_by=self.operator,
        )
        before_plans = Plan.objects.count()
        response = self.client.get(f'/inventory/?observation={self.observation.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Plan.objects.count(), before_plans)
        upcoming = response.context['upcoming']
        # Active RUNNING order is promoted ahead of queue order, matching the situation board.
        self.assertEqual([item['order'].pk for item in upcoming], [running_order.pk, missing_order.pk])
        self.assertTrue(upcoming[0]['active'])
        self.assertIsNotNone(upcoming[0]['scene'])
        self.assertIsNone(upcoming[1]['scene'])
        self.assertEqual(upcoming[1]['hold_reason'], '야드에 화물 없음')
        html = response.content.decode()
        self.assertIn('진행 중', html)
        self.assertIn('보류', html)
        self.assertIn('야드에 화물 없음', html)

    def test_saved_plans_respect_eight_scene_cap_and_condition_filter(self):
        other_condition = services.register_condition(
            self.admin, name='Other 3x6 site', calibration_ref='other', payload=site_policy(),
        )
        mismatched_cargo = services.register_cargo(
            self.operator, code='M1', name='M1', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        mismatched_order = services.create_order(
            self.operator, cargo=mismatched_cargo, kind='INBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        mismatched_plan = Plan.objects.create(
            created_by=self.operator, order=mismatched_order, order_version=mismatched_order.version,
            observation=self.observation, condition=other_condition, queue_digest='mismatch',
            result=dict(
                plan_kind='inbound-placement',
                status='READY',
                reason='OK',
                recommendation=dict(cargo_id='M1', area_id='yard', row=1, column=4, level=3, orientation=0),
            ),
        )
        saved_orders = [mismatched_order]
        for idx in range(2, 11):
            cargo = services.register_cargo(
                self.operator, code=f'S{idx}', name=f'S{idx}', weight_kg=2, volume_m3='0.012',
                destination='보관', length_cells=3,
            )
            order = services.create_order(
                self.operator, cargo=cargo, kind='INBOUND',
                priority_group=1, sequence=idx, tie_policy='FIXED',
            )
            Plan.objects.create(
                created_by=self.operator, order=order, order_version=order.version,
                observation=self.observation, condition=self.condition, queue_digest=f'q{idx}',
                result=dict(
                    plan_kind='inbound-placement',
                    status='READY',
                    reason='OK',
                    recommendation=dict(cargo_id=f'S{idx}', area_id='yard', row=1, column=4, level=3, orientation=0),
                ),
            )
            saved_orders.append(order)

        context = yard_page_context(self.observation)
        upcoming = context['upcoming']
        self.assertEqual(len(upcoming), 10)
        # Plan with mismatched condition is ignored (previewed instead of using mismatched_plan).
        self.assertIsNone(upcoming[0]['plan'])
        self.assertNotEqual(upcoming[0]['key'], f'plan-{mismatched_plan.pk}')
        # All 3D scene generation (including saved READY plans) is capped to the first 8 items.
        self.assertTrue(all(item['scene'] is not None and not item['preview_skipped'] for item in upcoming[:8]))
        self.assertTrue(all(item['scene'] is None and item['preview_skipped'] for item in upcoming[8:]))

    def test_chained_previews_handle_hold_saved_inbound_auto_reservation_and_outbound_freed_slot(self):
        # 1. HOLD order (unobserved outbound) must not alter chained state.
        missing = services.register_cargo(
            self.operator, code='GONE-1', name='GONE-1', weight_kg=2, volume_m3='0.012',
            destination='컨베이어', length_cells=3,
        )
        hold_order = services.create_order(
            self.operator, cargo=missing, kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        # 2. Outbound K2 (top of 1,1 stack) frees (1,1,2).
        out_k2 = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K2'), kind='OUTBOUND',
            priority_group=1, sequence=2, tie_policy='FIXED',
        )
        # 3. Saved READY inbound N1 placed at (3, 1, 1) advances spatial_snap.
        n1 = services.register_cargo(
            self.operator, code='N1', name='N1', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        in_n1 = services.create_order(
            self.operator, cargo=n1, kind='INBOUND',
            priority_group=1, sequence=3, tie_policy='FIXED',
        )
        Plan.objects.create(
            created_by=self.operator, order=in_n1, order_version=in_n1.version,
            observation=self.observation, condition=self.condition, queue_digest='n1',
            result=dict(
                plan_kind='inbound-placement',
                status='READY',
                reason='OK',
                recommendation=dict(cargo_id='N1', area_id='yard', row=3, column=1, level=1, orientation=0),
                incoming_shape=dict(cargo_id='N1', length_cells=3, footprint_revision=CargoFootprint.objects.get(cargo=n1).pk),
            ),
        )
        # 4. Previewed inbound N2 must NOT collide with saved N1 at (3, 1, 1) or auto-reserved N3 at (3, 4, 1).
        n2 = services.register_cargo(
            self.operator, code='N2', name='N2', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        in_n2 = services.create_order(
            self.operator, cargo=n2, kind='INBOUND',
            priority_group=1, sequence=4, tie_policy='FIXED',
        )
        # 5. Later automatic inbound N3 with a saved reservation at (3, 4, 1).
        n3 = services.register_cargo(
            self.operator, code='N3', name='N3', weight_kg=2, volume_m3='0.012',
            destination='보관', length_cells=3,
        )
        in_n3 = services.create_order(
            self.operator, cargo=n3, kind='INBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        in_n3.auto_replan = True
        in_n3.planning_observation = self.observation
        in_n3.save(update_fields=['auto_replan', 'planning_observation'])
        Plan.objects.create(
            created_by=self.operator, order=in_n3, order_version=in_n3.version,
            observation=self.observation, condition=self.condition, queue_digest='n3',
            result=dict(
                plan_kind='inbound-placement',
                status='READY',
                reason='OK',
                recommendation=dict(cargo_id='N3', area_id='yard', row=3, column=4, level=1, orientation=0),
                incoming_shape=dict(cargo_id='N3', length_cells=3, footprint_revision=CargoFootprint.objects.get(cargo=n3).pk),
            ),
        )

        context = yard_page_context(self.observation)
        by_pk = {item['order'].pk: item for item in context['upcoming']}
        self.assertEqual(by_pk[hold_order.pk]['hold_reason'], '야드에 화물 없음')
        self.assertIsNotNone(by_pk[out_k2.pk]['scene'])
        self.assertIsNotNone(by_pk[in_n1.pk]['scene'])
        self.assertIsNotNone(by_pk[in_n2.pk]['scene'])
        n2_box = next(b for b in by_pk[in_n2.pk]['scene']['frames'][1]['boxes'] if b['id'] == 'N2')
        n2_slot = (n2_box['area'], n2_box['row'], n2_box['column'], n2_box['level'])
        # N2 avoids saved N1 (3,1,1) and auto-reserved N3 (3,4,1), taking (1,1,2) freed by K2 outbound.
        self.assertEqual(n2_slot, ('yard', 1, 1, 2))
        self.assertNotIn('K2', {b['id'] for b in by_pk[in_n2.pk]['scene']['frames'][0]['boxes']})
        self.assertIn('N1', {b['id'] for b in by_pk[in_n2.pk]['scene']['frames'][0]['boxes']})

    def test_outbound_chain_replay_respects_limit(self):
        for seq, code in enumerate(('K1', 'K2', 'K3', 'K4'), start=1):
            services.create_order(
                self.operator, cargo=Cargo.objects.get(code=code), kind='OUTBOUND',
                priority_group=1, sequence=seq, tie_policy='FIXED',
            )
        chain = outbound_chain_replay(self.observation, limit=2)
        self.assertIsNotNone(chain)
        self.assertEqual(len(chain['cargo_codes']), 2)


class SeedYardScheduleTests(TestCase):
    def setUp(self):
        call_command('seed_site_dataset', provenance='DECLARED', verbosity=0)
        self.operator = User.objects.get(username='seed_operator')
        self.client.force_login(self.operator)

    def test_seed_yard_schedule_matches_board_order_and_never_persists_plans(self):
        self.assertEqual(Plan.objects.count(), 0)
        board_response = self.client.get('/')
        self.assertEqual(board_response.status_code, 200)
        yard_response = self.client.get('/inventory/')
        self.assertEqual(yard_response.status_code, 200)

        # Viewing /inventory/ must never write Plan rows (prevents auto_dispatch_next from proposing to ROSY).
        self.assertEqual(Plan.objects.count(), 0)

        upcoming = yard_response.context['upcoming']
        self.assertEqual(len(upcoming), 22)

        # Open order sequence matches main control board's 지금 + 다음 jobs.
        board = board_response.context['board']
        board_titles = [board['current_job']['title'], *[job['title'] for job in board['next_jobs']]]
        yard_cargo_titles = [
            f"{item['order'].cargo.code} {item['kind_label']}"
            for item in upcoming[:len(board_titles)]
        ]
        self.assertEqual(yard_cargo_titles, board_titles)

        # First 8 orders build 3D scenes in memory; all 22 items carry ETA and summary without '3D 준비 안 됨' or '—'.
        self.assertTrue(all(item['scene'] is not None for item in upcoming[:8]))
        self.assertTrue(all(item['scene'] is None and item['preview_skipped'] for item in upcoming[8:]))
        self.assertTrue(all(re.match(r'^\d\d:\d\d$', item['eta_text']) for item in upcoming))
        self.assertEqual(yard_response.context['current_job_key'], upcoming[0]['key'])
        yard_html = yard_response.content.decode()
        self.assertIn('data-upcoming-current', yard_html)
        self.assertNotIn('3D 준비 안 됨', yard_html)
        self.assertNotIn('계산 전', yard_html)
        self.assertNotIn('예상', yard_html)

        # First outbound (C-1) has a 5-step 3D scene (rehandle C-9, C-2 -> outbound C-1 -> return C-2, C-9).
        by_code = {item['order'].cargo.code: item for item in upcoming}
        first_outbound = by_code['C-1']
        self.assertEqual(len(first_outbound['arrows']), 5)
        self.assertEqual(len(first_outbound['scene']['frames']), 6)
        self.assertEqual(
            [a['phase'] for a in first_outbound['arrows']],
            ['재취급', '재취급', '반출', '복귀', '복귀'],
        )

        # Sequential inbounds (C-5, C-8, C-18) advance spatial_snap so each gets a distinct destination.
        first_inbound = by_code['C-5']
        self.assertIsNotNone(first_inbound['scene'])
        self.assertEqual(len(first_inbound['scene']['frames']), 2)
        belt_box = next(b for b in first_inbound['scene']['frames'][0]['boxes'] if b['id'] == 'C-5')
        yard_box = next(b for b in first_inbound['scene']['frames'][1]['boxes'] if b['id'] == 'C-5')
        self.assertEqual(belt_box['area'], 'belt')
        self.assertEqual((yard_box['area'], yard_box['row'], yard_box['column'], yard_box['level']), ('yard', 2, 4, 2))
        self.assertEqual(first_inbound['scene']['frames'][1]['move']['source'], 'belt')
        self.assertEqual(first_inbound['scene']['frames'][1]['move']['destination'], 'yard-r2-c4-l2')
        self.assertEqual(first_inbound['arrows'][0]['source'], '반입(컨베이어)')
        self.assertEqual(first_inbound['arrows'][0]['destination'], '2행 2열 2층')
        tier1_inbound_targets = [by_code[c]['target_label'] for c in ('C-5', 'C-8', 'C-18')]
        self.assertEqual(len(set(tier1_inbound_targets)), 3)
        self.assertEqual(tier1_inbound_targets, ['→ yard-r2-c4-l2', '→ yard-r3-c4-l2', '→ yard-r2-c4-l3'])

        # Inbound C-6 (after outbound C-3) uses the slot freed by C-3 (row=1, col=4, level=2 -> yard-r1-c4-l2).
        self.assertEqual(by_code['C-6']['target_label'], '→ yard-r1-c4-l2')
        self.assertNotIn('C-3', {b['id'] for b in by_code['C-6']['scene']['frames'][0]['boxes']})
        # Outbound chain is bounded to outbound orders within the first 8 queue items.
        chain = yard_response.context['outbound_chain']
        self.assertIsNotNone(chain)
        self.assertEqual(chain['cargo_codes'], ['C-1', 'C-4', 'C-3'])

        # 3D scene JSON carries container color, confirmation flag, priority tier, and basic cargo metadata.
        yard_boxes = {b['id']: b for b in yard_response.context['yard_scene']['frames'][0]['boxes']}
        self.assertEqual(yard_boxes['C-1']['color'], 'pink')
        self.assertTrue(yard_boxes['C-1']['color_confirmed'])
        self.assertEqual(yard_boxes['C-1']['priority_tier'], 'urgent')
        self.assertEqual(yard_boxes['C-1']['priority_group'], 1)
        self.assertEqual(yard_boxes['C-1']['shipper'], '한진특송')
        self.assertEqual(yard_boxes['C-1']['voyage'], 'V-BUSAN-01')
        self.assertTrue(yard_boxes['C-1']['cutoff'])
        self.assertEqual(yard_boxes['C-9']['color'], 'orange')
        self.assertEqual(yard_boxes['C-9']['priority_tier'], 'normal')
        self.assertEqual(yard_boxes['C-14']['color'], 'sky')
        self.assertFalse(yard_boxes['C-14']['color_confirmed'])
        self.assertEqual(yard_boxes['C-14']['priority_tier'], 'priority')
        # Inbound C-18 preview carries yellow color on the belt/yard frame.
        c18_box = next(b for b in by_code['C-18']['scene']['frames'][0]['boxes'] if b['id'] == 'C-18')
        self.assertEqual((c18_box['color'], c18_box['color_confirmed'], c18_box['priority_tier']), ('yellow', False, 'urgent'))
        html = yard_response.content.decode()
        self.assertIn('class="scene-tier-legend"', html)
        self.assertIn('긴급', html)
        self.assertIn('우선', html)
        self.assertIn('일반', html)
        self.assertEqual(Plan.objects.count(), 0)
