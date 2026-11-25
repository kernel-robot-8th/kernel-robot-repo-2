"""Project APPLIED container.stored port events into Observation(provenance='PORT_EVENT') for /inventory/."""
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from harbor.port_events import IntakeLedger
from harbor.rosy_http import RosyClient
from harbor_web import intake_loop, port_event_projection, services
from harbor_web.models import Audit, CargoFootprint, Observation
from harbor_web.tests import test_glossary_sweep, test_intake_loop


class PortEventProjectionTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / 'ledger.sqlite3')
        env = mock.patch.dict(os.environ, {
            'HARBOR_LEDGER_PATH': self.path,
            'HARBOR_TRIP_DESTINATION': 'intake-bench',
        })
        env.start()
        self.addCleanup(env.stop)

        self.user = User.objects.create_user('proj-operator')
        for role in ('admin', 'operator', 'viewer'):
            self.user.groups.add(Group.objects.get_or_create(name=role)[0])

        root = Path(__file__).resolve().parents[2]
        policy = json.loads((root / 'config/spatial-policy-3x6.json').read_text(encoding='utf-8'))
        policy['yard_layout'] = json.loads((root / 'config/yard-layout-3x6.json').read_text(encoding='utf-8'))
        self.condition = services.register_condition(
            self.user, name='3x6 현장 기준', calibration_ref='synthetic', payload=policy
        )
        for code in ('C1', 'C2', 'C3'):
            services.register_cargo(
                self.user, code=code, name=code, weight_kg=2, volume_m3='0.012', destination='출하장', length_cells=3
            )
        self.footprints = {
            c: CargoFootprint.objects.filter(cargo__code=c).latest('pk') for c in ('C1', 'C2', 'C3')
        }
        self.fleet = test_intake_loop.Fleet()
        client = RosyClient(transport=self.fleet)
        client.expected_principal, client.expected_role = test_intake_loop.PRINCIPAL, 'service'
        self.registry = SimpleNamespace(config={'path': self.path, 'origin': 'https://fleet-r.invalid'}, client=client)
        self.now = timezone.now()
        self.client.force_login(self.user)

    def _expect_stored(self, cargo_code, slot_ref, event_id, *, identity='observed'):
        ledger = IntakeLedger(self.path)
        ledger.expect(cargo_code, now=self.now)
        ledger.arrive(cargo_code, now=self.now)
        self.fleet.events.append(dict(
            event_id=event_id,
            kind='container.stored',
            observation_ref=f'obs-{event_id[:8]}',
            object_ref=cargo_code,
            intake_point=None,
            slot_ref=slot_ref,
            vehicle_id=None,
            reason=None,
            identity=identity,
            observed_at='2026-11-22T09:00:00Z',
        ))

    def test_occurrence_receipt_dispatch_observation_replay_and_repeat(self):
        from django.conf import settings
        from harbor_web.cell_plan_dispatch import auto_dispatch_next
        from datetime import timedelta
        from harbor_web.models import IntakeOccurrence, IntakeProjectionCursor, Order, Plan
        from harbor_web.runtime_models import DeploymentState
        from harbor_web.tests.test_cell_plan_dispatch import _FakeCellPlanClient
        DeploymentState.objects.get_or_create(pk=1, defaults={'mode': settings.MODE})
        yard = dict(schema='grid-placement-v1', observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                    holdings=[], placements=[])
        legacy = IntakeLedger(self.path)
        # Harbor's manifest clock is ahead of Fleet's RFID clock.
        legacy.expect('C1', now=self.now + timedelta(minutes=2))
        def event(kind, char, occurrence, *, identity='observed', **fields):
            self.fleet.events.append(dict(event_id=char * 64, kind='container.' + kind,
                observation_ref='observation-' + char, object_ref='C1', identity=identity,
                observed_at=timezone.now().isoformat(), intake_occurrence_id=occurrence, **fields))
            intake_loop.tick(self.registry, now=timezone.now())
        event('received_at_intake', 'a', 'first', intake_point='belt')
        self.assertEqual(legacy.read('C1')['state'], 'RECEIVED_AT_INTAKE')
        self.fleet.events.append(dict(event_id='0'*64, kind='container.stored', observation_ref='old-unbound',
            object_ref='C1', identity='observed', slot_ref='yard-r1-c4-l1', observed_at=timezone.now().isoformat()))
        intake_loop.tick(self.registry, now=timezone.now())
        self.assertEqual(legacy.read('C1')['state'], 'RECEIVED_AT_INTAKE')
        receipt = IntakeOccurrence.objects.get(pk='first')
        self.assertEqual(receipt.status, 'WAITING_FOR_YARD')
        self.assertEqual(Order.objects.count(), 1)
        partial = dict(yard, observed_areas=['yard'])
        def import_partial(revision, captured):
            return Observation.objects.create(created_by=self.user, source_id='yard', source_revision=revision,
                observed_at=captured, provenance='DECLARED', condition=self.condition, payload=partial)
        import_partial('incomplete', timezone.now())
        intake_loop.tick(self.registry, now=timezone.now())
        self.assertFalse(Plan.objects.filter(order=receipt.order).exists())
        full = services.record_observation(self.user, source_id='yard', source_revision='one', observed_at=timezone.now(),
            provenance='DECLARED', condition=self.condition, payload=yard)
        import_partial('delayed-old', self.now)
        intake_loop.tick(self.registry, now=timezone.now())
        plan = Plan.objects.filter(order=receipt.order).latest('pk')
        self.assertEqual(plan.observation, full)
        fleet = _FakeCellPlanClient()
        with mock.patch('harbor_web.readiness.first_unmet_cell_plan', return_value='READINESS_WORKCELL_INCIDENT_HOLD'):
            self.assertIsNone(auto_dispatch_next(client=fleet, production=True))
        page = self.client.get('/inventory/')
        rec = plan.result['recommendation']
        target = '{area_id}-r{row}-c{column}-l{level}'.format(**rec)
        self.assertContains(page, target)
        self.assertContains(page, 'READINESS_WORKCELL_INCIDENT_HOLD')
        self.assertContains(page, '적재 결과 대기')
        self.assertContains(page, '관측 대기')
        self.assertFalse(fleet.calls)
        dispatch = auto_dispatch_next(client=fleet, production=False)
        self.assertEqual(dispatch.plan_id, plan.pk)
        self.assertEqual(dispatch.request_payload['intake_occurrence_id'], 'first')
        self.assertEqual(dispatch.request_payload['placement_attempt_id'], dispatch.request_key)
        self.assertIs(dispatch.request_payload['propose'], True)
        for commercial_field in ('shipper_name', 'shipper_tier', 'destination', 'weight_kg', 'volume_m3', 'tag_value'):
            self.assertNotIn(f'"{commercial_field}"', json.dumps(dispatch.request_payload))
        self.assertContains(self.client.get('/inventory/'), '제안 접수 · 승인 대기')
        requested = dispatch.request_payload['steps'][0]['dst_site']
        slot = 'yard-r1-c4-l1' if requested != 'yard-r1-c4-l1' else 'yard-r1-c1-l1'
        import_partial('partial-after-submit', timezone.now())
        captured = timezone.now()
        event('stored', 'b', 'first', slot_ref=slot, placement_attempt_id=dispatch.request_key)
        receipt.refresh_from_db()
        self.assertEqual((receipt.status, legacy.read('C1')['state']), ('YARD_CONFLICT', 'RECEIVED_AT_INTAKE'))
        self.assertFalse(Observation.objects.filter(provenance='PORT_EVENT').exists())
        services.record_observation(self.user, source_id='yard', source_revision='recovered', observed_at=captured,
            provenance='DECLARED', condition=self.condition, payload=yard)
        intake_loop.tick(self.registry, now=timezone.now())
        receipt.refresh_from_db()
        self.assertEqual(legacy.read('C1')['state'], 'COMPLETED')
        self.assertEqual((receipt.status, receipt.observed_slot), ('SLOT_MISMATCH', slot))
        html = self.client.get('/inventory/')
        self.assertContains(html, requested)
        self.assertContains(html, slot)
        self.assertContains(html, '요청·관측 위치 불일치')
        intake_loop.tick(self.registry, now=timezone.now())
        dispatch.state = 'COMPLETED'
        dispatch.save(update_fields=['state', 'updated_at'])
        services.record_observation(self.user, source_id='yard', source_revision='after-outbound', observed_at=timezone.now(),
            provenance='DECLARED', condition=self.condition, payload=yard)
        event('received_at_intake', 'c', 'second', intake_point='belt')
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual((legacy.read('C1')['state'], legacy.read('C1')['slot_ref']), ('RECEIVED_AT_INTAKE', None))
        second = IntakeOccurrence.objects.get(pk='second')
        second_dispatch = auto_dispatch_next(client=_FakeCellPlanClient(), production=False)
        self.assertEqual(second_dispatch.order_id, second.order_id)
        event('stored', 'd', 'second', slot_ref=slot, placement_attempt_id=dispatch.request_key)
        self.assertFalse(IntakeOccurrence.objects.get(pk='second').stored_event_id)
        event('stored', 'e', 'first', slot_ref=slot, placement_attempt_id=dispatch.request_key)
        self.assertEqual(Observation.objects.filter(provenance='PORT_EVENT').count(), 1)
        event('stored', 'f', 'second', slot_ref=second_dispatch.request_payload['steps'][0]['dst_site'],
            placement_attempt_id=second_dispatch.request_key, identity='attested')
        second.refresh_from_db()
        self.assertEqual(second.status, 'STORED')
        self.assertEqual(second.result_identity, 'attested')
        self.assertEqual((legacy.read('C1')['state'], legacy.read('C1')['slot_ref']), ('COMPLETED', second.observed_slot))
        self.assertContains(self.client.get('/inventory/'), 'RFID 작업 추적 · 놓기 위치 검증')
        self.assertEqual(Observation.objects.filter(provenance='PORT_EVENT').count(), 2)
        cursor = IntakeProjectionCursor.objects.get()
        self.assertEqual(legacy.occurrence_batch(cursor.sequence, cursor.pending), [])
        cursor.delete()
        intake_loop.tick(self.registry, now=timezone.now())
        self.assertEqual((legacy.read('C1')['slot_ref'], IntakeOccurrence.objects.count()), (second.observed_slot, 2))

    def test_receipt_binds_unique_scheduled_order_and_retries_unknown_cargo(self):
        from harbor_web.models import Cargo, CargoTag, IntakeOccurrence, Order
        first = services.create_order(self.user, kind='INBOUND', cargo=Cargo.objects.get(code='C1'),
            priority_group=1, sequence=1, tie_policy='FIXED')
        for cargo, occurrence, digit in [('C1', 'scheduled', '1'), ('C4', 'unregistered', '2')]:
            self.fleet.events.append(dict(event_id=digit*64, kind='container.received_at_intake',
                observation_ref='obs-' + digit, object_ref=cargo, identity='observed', intake_point='belt',
                observed_at=timezone.now().isoformat(), intake_occurrence_id=occurrence))
        intake_loop.tick(self.registry, now=timezone.now())
        self.assertEqual(IntakeOccurrence.objects.get(pk='scheduled').order_id, first.pk)
        self.assertEqual(IntakeOccurrence.objects.get(pk='unregistered').status, 'CARGO_UNKNOWN')
        self.assertFalse(Cargo.objects.filter(code='C4').exists())
        registered = services.register_cargo(self.user, code='C4', name='C4', weight_kg=7, volume_m3=1,
            shipper_name='시험 화주', shipper_tier=2, tag_value='C4-test-tag', destination='Busan', length_cells=3)
        intake_loop.tick(self.registry, now=timezone.now())
        original = IntakeOccurrence.objects.get(pk='unregistered').order
        repeat = dict(kind='container.received_at_intake', object_ref='C4', identity='observed',
                      intake_point='belt', event_id='3'*64, observation_ref='obs-3',
                      intake_occurrence_id='repeat-C4', observed_at=timezone.now().isoformat())
        self.fleet.events.append(repeat)
        intake_loop.tick(self.registry, now=timezone.now())
        intake_loop.tick(self.registry, now=timezone.now())
        latest = IntakeOccurrence.objects.get(pk='repeat-C4').order
        self.assertNotEqual(original.pk, latest.pk)
        self.assertEqual((original.cargo_id, latest.cargo_id), (registered.pk, registered.pk))
        self.assertEqual((original.priority_group, latest.priority_group), (2, 2))
        self.assertEqual((latest.cargo.shipper_name, latest.cargo.destination, latest.cargo.weight_kg),
                         ('시험 화주', 'Busan', 7))
        self.assertEqual(CargoTag.objects.get(tag_value='C4-test-tag').cargo_id, registered.pk)
        self.assertEqual(Order.objects.count(), 3)

    def test_day1_empty_map_stored_event_projects_and_renders_on_inventory(self):
        ev_id = 'a' * 64
        self._expect_stored('C1', 'yard-r1-c1-l1', ev_id)
        intake_loop.tick(self.registry, now=self.now)

        obs = Observation.objects.get(source_id=port_event_projection.PORT_EVENT_SOURCE_ID, source_revision=ev_id)
        self.assertIsNone(obs.created_by_id)
        self.assertEqual(obs.provenance, 'PORT_EVENT')
        self.assertEqual(obs.payload['placements'], [dict(
            cargo_id='C1', area_id='yard', row=1, column=1, level=1,
            orientation=0, footprint_revision=self.footprints['C1'].pk,
        )])
        self.assertTrue(Audit.objects.filter(
            action='observation.port_event', object_type='Observation', object_id=str(obs.pk)
        ).exists())

        res = self.client.get('/inventory/')
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('C1', html)
        self.assertIn('ROSY 입고 기록', html)
        visible, summaries = test_glossary_sweep._visible_and_summary_text(html)
        self.assertNotRegex(visible, test_glossary_sweep.FORBIDDEN)
        self.assertNotRegex(summaries, test_glossary_sweep.FORBIDDEN)

        # Idempotent replay on subsequent ticks creates zero duplicates
        intake_loop.tick(self.registry, now=self.now)
        self.assertEqual(Observation.objects.count(), 1)

    def test_existing_spatial_observation_appends_and_clears_holding(self):
        services.record_observation(
            self.user, source_id='manual', source_revision='m-1', observed_at=self.now, provenance='DECLARED',
            condition=self.condition,
            payload=dict(
                schema='grid-placement-v1',
                observed_areas=['yard', 'temp_a', 'temp_b', 'stand'],
                holdings=[dict(cargo_id='C2', holder_id='arm_b')],
                placements=[dict(
                    cargo_id='C1', area_id='yard', row=1, column=1, level=1,
                    orientation=0, footprint_revision=self.footprints['C1'].pk,
                )],
            ),
        )
        ev_id = 'b' * 64
        self._expect_stored('C2', 'yard-r1-c1-l2', ev_id, identity='attested')
        intake_loop.tick(self.registry, now=self.now)

        latest = Observation.objects.order_by('-pk').first()
        self.assertEqual(latest.provenance, 'PORT_EVENT')
        self.assertEqual(latest.payload['holdings'], [])
        self.assertEqual({p['cargo_id'] for p in latest.payload['placements']}, {'C1', 'C2'})

    def test_rejection_isolation_records_once_without_blocking_valid_events(self):
        bad_unregistered = '1' * 64
        bad_misaligned = '2' * 64
        bad_unsupported = '3' * 64
        good_ev = '4' * 64
        self._expect_stored('C-UNREGISTERED', 'yard-r1-c1-l1', bad_unregistered)
        self._expect_stored('C2', 'yard-r1-c2-l1', bad_misaligned)
        self._expect_stored('C3', 'yard-r2-c1-l2', bad_unsupported)
        self._expect_stored('C1', 'yard-r1-c1-l1', good_ev)

        intake_loop.tick(self.registry, now=self.now)

        self.assertEqual(Observation.objects.filter(provenance='PORT_EVENT').count(), 1)
        obs = Observation.objects.get(source_revision=good_ev)
        self.assertEqual(obs.payload['placements'][0]['cargo_id'], 'C1')
        rejected = list(Audit.objects.filter(action='observation.port_event_rejected').order_by('pk'))
        self.assertEqual([r.object_id for r in rejected], [bad_unregistered, bad_misaligned, bad_unsupported])
        self.assertEqual(rejected[0].detail['reason'], 'CARGO_NOT_REGISTERED')
        self.assertEqual(rejected[1].detail['reason'], 'SLOT_NOT_ALIGNED')
        self.assertEqual(rejected[2].detail['reason'], 'SPATIAL_VALIDATION_FAILED')

        # Second projection pass deduplicates rejected audits
        port_event_projection.project_stored_events(IntakeLedger(self.path))
        self.assertEqual(Audit.objects.filter(action='observation.port_event_rejected').count(), 3)

    def test_transient_projection_error_does_not_break_poll_and_retries(self):
        ev_id = '5' * 64
        self._expect_stored('C1', 'yard-r1-c1-l1', ev_id)
        with mock.patch.object(port_event_projection, 'spatial_snapshot', side_effect=RuntimeError('transient db')):
            with self.assertLogs('harbor_web.port_event_projection', level='ERROR'):
                result = intake_loop.tick(self.registry, now=self.now)
        self.assertEqual(result['outcomes'][ev_id], 'APPLIED')
        self.assertFalse(Observation.objects.filter(provenance='PORT_EVENT').exists())
        self.assertFalse(Audit.objects.filter(action='observation.port_event_rejected').exists())

        intake_loop.tick(self.registry, now=self.now)
        self.assertTrue(Observation.objects.filter(source_revision=ev_id, provenance='PORT_EVENT').exists())

    def test_observation_author_check_constraint_enforced(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Observation.objects.create(
                created_by=None,
                source_id='manual',
                source_revision='rev-no-author',
                observed_at=self.now,
                provenance='DECLARED',
                payload={'schema': 'grid-placement-v1', 'observed_areas': ['yard'], 'placements': [], 'holdings': []},
                condition=self.condition,
            )
