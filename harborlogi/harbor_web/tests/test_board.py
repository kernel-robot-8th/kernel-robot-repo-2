"""Situation board (v4.8) tests: section order, counts, 5-item cap, mini yard, clean copy, CSS tokens."""
from datetime import timedelta
from pathlib import Path
import re
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from harbor_web import services
from harbor_web.commands import _save
from harbor_web.models import Cargo, CargoFootprint, Command, FieldEpisode, Order, Plan
from harbor_web.tests.test_control_board import site_policy

ROOT = Path(__file__).resolve().parents[2]


class SituationBoardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('board-admin')
        self.operator = User.objects.create_user('board-operator')
        self.viewer = User.objects.create_user('board-viewer')
        self.field = User.objects.create_user('board-field')
        for user, role_names in (
            (self.admin, ('admin', 'viewer')),
            (self.operator, ('operator', 'viewer')),
            (self.viewer, ('viewer',)),
            (self.field, ('field',)),
        ):
            user.groups.add(*[Group.objects.get_or_create(name=name)[0] for name in role_names])
        self.now = timezone.now()
        self.condition = services.register_condition(
            self.admin, name='3x6 board', calibration_ref='synthetic', payload=site_policy()
        )
        self.client.force_login(self.operator)

    def _cargo(self, code, length=3):
        return services.register_cargo(
            self.operator, code=code, name=code, weight_kg=2, volume_m3='0.012',
            destination='반출대', length_cells=length,
        )

    def _observe(self, placements=(), *, observed_at=None, revision='r1'):
        return services.record_observation(
            self.admin,
            source_id='board-cam',
            source_revision=revision,
            observed_at=observed_at or timezone.now(),
            provenance='SYNTHETIC',
            condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                placements=list(placements),
                holdings=[],
            ),
        )

    def _placement(self, code, row, column, level):
        fp = CargoFootprint.objects.filter(cargo__code=code).latest('pk')
        return dict(
            cargo_id=code, area_id='yard', row=row, column=column,
            level=level, orientation=0, footprint_revision=fp.pk,
        )

    def test_section_order_hidden_alerts_and_clean_visible_copy(self):
        for code in ('K1', 'K2'):
            self._cargo(code)
        obs = self._observe([self._placement('K1', 1, 1, 1), self._placement('K2', 1, 1, 2)])
        order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=obs.pk)

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        board = response.context['board']
        self.assertEqual(board['drive_label'], '자동 진행 꺼짐')
        self.assertEqual(board['drive_tone'], 'neutral')
        self.assertEqual(board['alert_total'], 0)
        self.assertEqual(board['alerts'], [])

        html = response.content.decode()
        self.assertNotIn('class="panel board-alerts"', html)
        self.assertNotIn('class="fold board-support"', html)
        self.assertNotIn('상태는 ROSY 실행 결과로 바뀜', html)
        self.assertNotIn('<span aria-hidden="true">→</span>', html)
        self.assertNotIn('알고리즘이 정한 순서', html)
        self.assertNotIn('2행×3열×3층', html)
        self.assertNotIn('2행 × 3열 · 3층', html)
        self.assertIn('<h1 id="h-board">상황판</h1>', html)
        self.assertNotIn('야드 현황과 전체 지시, 예상 작업량과 실제 근거를 구분해 판단하세요', html)

        main_html = html.split('<main ', 1)[1].split('>', 1)[1].split('</main>', 1)[0]
        positions = [
            main_html.index('class="panel board-run"'),
            main_html.index('class="board-counts"'),
            main_html.index('class="board-section board-even"'),
            main_html.index('class="panel board-jobs"'),
        ]
        self.assertEqual(positions, sorted(positions))

        outside_details = main_html.split('<details class="fold board-support">', 1)[0]
        visible_text = re.sub(r'<[^>]+>', ' ', outside_details)
        self.assertNotRegex(visible_text, r'관측|원본|인계|대조|회차|#\d+|READY|HOLD|BLOCKED')
        self.assertIn('추천 순서', outside_details)
        self.assertIn('야드 크게 보기', outside_details)
        self.assertIn(f'href="/inventory/?observation={obs.pk}"', outside_details)

        # Viewer role can read `/` cleanly too
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_alerts_section_appears_only_when_alerts_exist_and_stays_clean(self):
        # Missing observation -> 1 alert
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn('class="panel board-alerts"', html)
        self.assertIn('자동 진행 꺼짐 · 처리할 것 1건', html)
        self.assertIn('처리할 것 1건 보기', html)
        self.assertIn('href="/alerts/"', html)

        main_html = html.split('<main ', 1)[1].split('</main>', 1)[0]
        positions = [
            main_html.index('class="panel board-run"'),
            main_html.index('class="board-counts"'),
            main_html.index('class="panel board-alerts"'),
            main_html.index('class="board-section board-even"'),
            main_html.index('class="panel board-jobs"'),
        ]
        self.assertEqual(positions, sorted(positions))

        # Add an OPEN FieldEpisode and a BLOCKED command
        cargo = self._cargo('A1')
        obs = self._observe([self._placement('A1', 1, 1, 1)], revision='a1')
        order = services.create_order(
            self.operator, cargo=cargo, kind='OUTBOUND', priority_group=1, sequence=1, tie_policy='FIXED'
        )
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=obs.pk)
        Command.objects.create(
            order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='a' * 64, binding={}, state='BLOCKED',
        )
        in_order = services.create_order(
            self.operator, cargo=self._cargo('IN1'), kind='INBOUND',
            priority_group=2, sequence=1, tie_policy='FIXED',
        )
        FieldEpisode.objects.create(
            order=in_order, vehicle_id='veh-1', assigned_to=self.field, created_by=self.operator, state='OPEN'
        )
        resp = self.client.get('/')
        self.assertEqual(resp.context['board']['alert_total'], 2)
        self.assertEqual(resp.context['board']['drive_label'], '자동 진행 꺼짐 · 처리할 것 2건')
        self.assertEqual(resp.context['board']['drive_tone'], 'attention')
        outside_details = resp.content.decode().split('<main ', 1)[1].split('>', 1)[1].split('<details class="fold board-support">', 1)[0]
        visible_text = re.sub(r'<[^>]+>', ' ', outside_details)
        self.assertNotRegex(visible_text, r'관측|원본|인계|대조|회차|#\d+|READY|HOLD|BLOCKED')

    def test_drive_label_follows_executor_status(self):
        for enabled, label, tone in ((True, '자동 진행 켜짐', 'good'),
                                     (False, '자동 진행 꺼짐', 'neutral')):
            with self.subTest(enabled=enabled), patch('harbor_web.executor.status',
                    return_value=dict(enabled=enabled, label=label, tone=tone)):
                response = self.client.get('/')
                drive = '자동 운전 중' if enabled else label
                self.assertEqual(response.context['board']['drive_label'], f'{drive} · 처리할 것 1건')
                self.assertEqual(response.context['status_strip']['executor_label'], label)
                if not enabled:
                    self.assertNotContains(response, '자동 운전 중')

    def test_next_jobs_capped_at_five_and_completed_waiting_next(self):
        obs = self._observe([self._placement(self._cargo('K1').code, 1, 1, 1)], revision='cap')
        orders = []
        for i in range(1, 8):
            c = Cargo.objects.get(code='K1') if i == 1 else self._cargo(f'K{i}')
            orders.append(
                services.create_order(
                    self.operator, cargo=c, kind='OUTBOUND', priority_group=1, sequence=i, tie_policy='FIXED'
                )
            )
        plan1 = services.make_plan(self.operator, order_id=orders[0].pk, expected_version=1, observation_id=obs.pk)
        cmd1 = Command.objects.create(
            order=orders[0], plan=plan1, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='a' * 64, binding={}, state='RECONCILED',
        )

        # While order 1 is running, next_jobs shows orders 2..6 (5 items, not 6)
        resp = self.client.get('/')
        board = resp.context['board']
        self.assertEqual(board['current_job']['title'], 'K1 반출')
        self.assertEqual(board['current_job']['state'], '진행 중')
        self.assertEqual(board['current_job']['tone'], 'info')
        self.assertEqual(len(board['next_jobs']), 5)
        self.assertEqual([j['title'] for j in board['next_jobs']], [f'K{i} 반출' for i in range(2, 7)])
        self.assertEqual(resp.content.decode().count('class="board-next-item"'), 5)

        # Complete order 1 today -> current_job is '완료 · 다음 작업 대기 중', next_jobs starts at K2 (capped at 5)
        _save(cmd1, self.operator, 'COMPLETED')
        resp = self.client.get('/')
        board = resp.context['board']
        self.assertEqual(board['current_job']['title'], 'K1 반출')
        self.assertEqual(board['current_job']['state'], '완료 · 다음 작업 대기 중')
        self.assertEqual(board['current_job']['tone'], 'good')
        self.assertEqual(board['day_progress'], {'completed': 1, 'total': 7, 'remaining': 6})
        self.assertEqual(len(board['next_jobs']), 5)
        self.assertEqual([j['title'] for j in board['next_jobs']], [f'K{i} 반출' for i in range(2, 7)])

    def test_work_sequence_keeps_current_and_next_details_in_one_panel(self):
        board = {
            'current_job': dict(title='현재 화물', state='진행 중', tone='info',
                href='/work/1/', source='야드', destination='반출대', cargo='K1', arm='A'),
            'next_jobs': [dict(title='다음 화물', kind_label='입고', state='대기', tone='neutral',
                href='/requests/2/', route_text='반입대 → 야드', arm='B')],
        }
        html = render_to_string('overview.html', {'board': board})
        sequence = html.split('aria-labelledby="h-board-jobs"', 1)[1].split('</section>', 1)[0]
        self.assertIn('<h2 id="h-board-jobs">작업 순서</h2>', sequence)
        self.assertIn('aria-labelledby="h-board-jobs" tabindex="0"', sequence)
        current, upcoming = sequence.split('class="board-next-item"', 1)
        for text in ('지금', '현재 화물', '진행 중', '야드 → 반출대', '팔 A', 'href="/work/1/"'):
            self.assertIn(text, current)
        for text in ('다음 1', '입고', '다음 화물', '대기', '반입대 → 야드', '팔 B', 'href="/requests/2/"'):
            self.assertIn(text, upcoming)
        empty = render_to_string('overview.html', {'board': {'current_job': None, 'next_jobs': []}})
        self.assertIn('하는 일 없음', empty)
        self.assertNotIn('class="board-next-item"', empty)
        self.assertNotIn('다음 일 없음', empty)

    def test_counts_deduplicate_by_order_and_exclude_empty_rearrange(self):
        obs = self._observe([self._placement(self._cargo('K1').code, 1, 1, 1)], revision='cnt')
        out_done = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        plan_out = services.make_plan(self.operator, order_id=out_done.pk, expected_version=1, observation_id=obs.pk)
        c_first = Command.objects.create(
            order=out_done, plan=plan_out, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='a' * 64, binding={}, state='RECONCILED',
        )
        _save(c_first, self.operator, 'COMPLETED')
        # Duplicate completion of the same order must count once
        plan_out_2 = services.make_plan(self.operator, order_id=out_done.pk, expected_version=1, observation_id=obs.pk)
        c_second = Command.objects.create(
            order=out_done, plan=plan_out_2, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='b' * 64, binding={}, state='RECONCILED',
        )
        _save(c_second, self.operator, 'COMPLETED')

        # Yesterday's completion + today retry must not count today
        old_cargo = self._cargo('OLD')
        old_order = services.create_order(
            self.operator, cargo=old_cargo, kind='INBOUND', priority_group=1, sequence=1, tie_policy='FIXED'
        )
        old_plan = services.make_plan(self.operator, order_id=old_order.pk, expected_version=1, observation_id=obs.pk)
        old_cmd1 = Command.objects.create(
            order=old_order, plan=old_plan, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='c' * 64, binding={}, state='RECONCILED',
        )
        with patch('django.utils.timezone.now', return_value=timezone.now() - timedelta(days=1)):
            _save(old_cmd1, self.operator, 'COMPLETED')
        old_plan2 = services.make_plan(self.operator, order_id=old_order.pk, expected_version=1, observation_id=obs.pk)
        old_cmd2 = Command.objects.create(
            order=old_order, plan=old_plan2, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='d' * 64, binding={}, state='RECONCILED',
        )
        _save(old_cmd2, self.operator, 'COMPLETED')

        # Pending inbound + pending outbound
        services.create_order(
            self.operator, cargo=self._cargo('IN1'), kind='INBOUND', priority_group=2, sequence=1, tie_policy='FIXED'
        )
        services.create_order(
            self.operator, cargo=self._cargo('OUT2'), kind='OUTBOUND', priority_group=2, sequence=1, tie_policy='FIXED'
        )

        # Skipped / empty rearrange orders must be excluded
        r_hold = Order.objects.create(created_by=self.operator, kind='REARRANGE')
        Plan.objects.create(
            order=r_hold, order_version=1, observation=obs, condition=self.condition, queue_digest='q',
            result={'status': 'HOLD', 'reason': 'REARRANGE_NOT_WORTH', 'moves': []}, created_by=self.operator,
        )
        r_empty = Order.objects.create(created_by=self.operator, kind='REARRANGE')
        Plan.objects.create(
            order=r_empty, order_version=1, observation=obs, condition=self.condition, queue_digest='q',
            result={'status': 'READY', 'moves': []}, created_by=self.operator,
        )
        # Real pending rearrange must be counted
        r_real = Order.objects.create(created_by=self.operator, kind='REARRANGE')
        Plan.objects.create(
            order=r_real, order_version=1, observation=obs, condition=self.condition, queue_digest='q',
            result={'status': 'READY', 'moves': [{'cargo_id': 'X', 'source': 'a', 'destination': 'b'}]},
            created_by=self.operator,
        )

        board = self.client.get('/').context['board']
        by_kind = {item['kind']: item for item in board['counts']}
        self.assertEqual(by_kind['OUTBOUND'], {'kind': 'OUTBOUND', 'label': '반출', 'completed': 1, 'pending': 1})
        self.assertEqual(by_kind['INBOUND'], {'kind': 'INBOUND', 'label': '입고', 'completed': 0, 'pending': 1})
        self.assertEqual(by_kind['REARRANGE'], {'kind': 'REARRANGE', 'label': '재배치', 'completed': 0, 'pending': 1})

    def test_mini_yard_highlight_and_ghost_cargo_for_outbound_and_inbound(self):
        for code in ('K1', 'K2'):
            self._cargo(code)
        obs = self._observe([self._placement('K1', 1, 1, 1), self._placement('K2', 1, 1, 2)], revision='mini')
        order = services.create_order(
            self.operator, cargo=Cargo.objects.get(code='K1'), kind='OUTBOUND',
            priority_group=1, sequence=1, tie_policy='FIXED',
        )
        plan = services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=obs.pk)
        self.assertEqual(plan.result['status'], 'READY')
        first_move = plan.result['moves'][0]
        self.assertEqual(first_move['cargo_id'], 'K2')

        resp = self.client.get(f'/?observation={obs.pk}')
        board = resp.context['board']
        ops_yard = resp.context['operations']['yard']

        # operations['yard'] must not be mutated
        for lane in ops_yard['yard']['lanes']:
            for slot in lane['slots']:
                self.assertFalse(slot.get('mini_move'))
                self.assertIsNone(slot.get('ghost_cargo'))

        mini = board['mini_yard']
        self.assertNotIn('dimensions', mini)
        slots_by_key = {
            s['key']: s
            for area in [mini['yard'], *mini['left'], *mini['right'], *mini['below']]
            for lane in area['lanes']
            for s in lane['slots']
        }
        self.assertTrue(slots_by_key['yard-R1S1'].get('mini_move'))
        self.assertIsNone(slots_by_key['yard-R1S1'].get('ghost_cargo'))
        dest_key = f"{first_move['destination_placement']['area_id']}-R{first_move['destination_placement']['row']}S1"
        self.assertTrue(slots_by_key[dest_key].get('mini_move'))
        self.assertEqual(slots_by_key[dest_key].get('ghost_cargo'), 'K2')

        html = resp.content.decode()
        self.assertIn('is-move-slot', html)
        self.assertIn('<span class="slot-ghost">K2</span>', html)
        # Destination card with ghost cargo omits overlapping slot-empty text
        dest_card = html.split(f'data-yard-row="{dest_key}"', 1)[1].split('</div>', 1)[0]
        self.assertNotIn('class="slot-empty"', dest_card)
        self.assertIn('<span class="slot-ghost">K2</span>', dest_card)

        # Inbound placement recommendation also marks destination and ghost cargo
        Command.objects.create(
            order=order, plan=plan, actor=self.operator, plan_version=1,
            ledger_identity='t', fleet_origin='t', fleet_principal='t',
            body_digest='e' * 64, binding={}, state='COMPLETED',
        )
        in_order = services.create_order(
            self.operator, cargo=self._cargo('N1'), kind='INBOUND', priority_group=1, sequence=1, tie_policy='FIXED'
        )
        in_plan = services.make_plan(self.operator, order_id=in_order.pk, expected_version=1, observation_id=obs.pk)
        self.assertEqual(in_plan.result['status'], 'READY')
        resp_in = self.client.get(f'/?observation={obs.pk}')
        in_board = resp_in.context['board']
        self.assertEqual(in_board['current_job']['source'], '반입(컨베이어)')
        self.assertIn('<span class="slot-ghost">N1</span>', resp_in.content.decode())

    def test_board_even_container_css_tokens_and_slot_card_backwards_compat(self):
        self.assertIsNotNone(finders.find('harbor_web/board.css'))
        css = (ROOT / 'harbor_web/static/harbor_web/board.css').read_text(encoding='utf-8')
        for selector in ('.board-job-list', '.yard-topview.board-even', '.board-counts', '.board-run', '.slot-card.is-move-slot', '.slot-ghost'):
            self.assertIn(selector, css)
        self.assertIn('@media (min-width: 1100px)', css)
        self.assertNotRegex(css, r'#[0-9a-fA-F]{3,8}')
        css_without_media = re.sub(r'@media\s*\([^)]*\)', '', css)
        self.assertNotRegex(css_without_media, r'\b\d+(?:\.\d+)?px\b')

        html = self.client.get('/').content.decode()
        self.assertIn('harbor_web/board.css', html)
        self.assertIn('class="board-section board-even"', html)
        self.assertEqual(html.count('class="yard-topview"'), 1)

        # slot_card.html without mini_move / ghost_cargo stays untouched
        plain_card = render_to_string(
            'slot_card.html',
            {'slot': {'key': 'yard-R1S1', 'label': '1열', 'levels': 3, 'stack': []}},
        )
        self.assertNotIn('is-move-slot', plain_card)
        self.assertNotIn('slot-ghost', plain_card)
        self.assertIn('class="slot-empty"', plain_card)
