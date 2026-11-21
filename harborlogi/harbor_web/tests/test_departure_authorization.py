"""ROSY 출발 허락과 사람 CLEAR 대비 경로의 회귀 시험."""
from datetime import timedelta
from hashlib import sha256
import os
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from harbor.departure import DepartureLedger, NoReleasePermit
from harbor.transport import TransportLedger
from harbor_web import field_episodes, intake_loop, services
from harbor_web.field_pages import episode_status
from harbor_web.models import Audit, DepartureIntent
from harbor_web.tests import test_intake_loop


class DepartureAuthorizationTests(test_intake_loop.IntakeLoopTests):
    def setUp(self):
        super().setUp()
        cargo = services.register_cargo(self.operator, code='release-cargo', name='Cargo', weight_kg='1',
                                        volume_m3='1', destination='yard')
        order = services.create_order(self.operator, cargo=cargo, kind='INBOUND', priority_group=1,
                                      sequence=1, tie_policy='FIXED')
        self.episode = services.assign_episode(self.operator, order_id=order.pk, vehicle_id='release-vehicle',
                                               assigned_to=self.field)
        self.now = timezone.now()
        self.event_id = sha256(b'release-1').hexdigest()

    def release(self, *, observed_at=None, vehicle_id='release-vehicle'):
        self.fleet.events = [dict(event_id=self.event_id, kind='vehicle.release_permitted', observation_ref='release-obs',
                                  vehicle_id=vehicle_id, observed_at=(observed_at or self.now).isoformat())]

    def test_poll_prepares_without_confirmation_and_spends_once(self):
        self.release()
        intake_loop.tick(self.registry, now=self.now)
        intent = DepartureIntent.objects.get(episode=self.episode)
        self.assertIsNone(intent.confirmation_id)
        self.assertEqual(intent.release_event_id, self.event_id)
        self.assertEqual(TransportLedger(self.path).read(intake_loop.trip_key(intent))['state'], 'PLANNED')
        self.assertEqual(Audit.objects.get(action='departure.prepare').detail,
                         {'authorized_by': 'rosy_release', 'release_event_id': self.event_id})
        self.assertEqual(episode_status(self.episode)['status_label'], 'ROSY 허락으로 출발 준비됨')
        intake_loop.tick(self.registry, now=self.now)
        self.assertEqual(DepartureIntent.objects.count(), 1)
        self.assertEqual(Audit.objects.filter(action='departure.prepare').count(), 1)
        with self.assertRaises(ValueError):
            DepartureLedger(self.path).authorize('release-vehicle', since=self.episode.created_at)
        self.assertEqual(field_episodes.prepare_departure_from_release(self.episode, now=self.now).pk, intent.pk)

    def test_previous_visit_or_other_vehicle_permission_is_not_used(self):
        for observed_at, vehicle in [(self.episode.created_at - timedelta(seconds=1), 'release-vehicle'),
                                     (self.now, 'other-vehicle')]:
            with self.subTest(vehicle=vehicle):
                self.event_id = sha256(vehicle.encode()).hexdigest()
                self.release(observed_at=observed_at, vehicle_id=vehicle)
                intake_loop.tick(self.registry, now=self.now)
                self.assertFalse(DepartureIntent.objects.exists())

    def test_no_ledger_path_leaves_permission_unspent(self):
        self.release()
        with mock.patch.dict(os.environ, {'HARBOR_LEDGER_PATH': ''}):
            intake_loop.tick(self.registry, now=self.now)
            self.assertIsNone(field_episodes.prepare_departure_from_release(self.episode, now=self.now))
        self.assertFalse(DepartureIntent.objects.exists())
        self.assertEqual(DepartureLedger(self.path).authorize('release-vehicle', since=self.episode.created_at), self.event_id)

    def test_reassigned_episode_cannot_spend_permission(self):
        services.reassign_episode(self.operator, episode_id=self.episode.pk, expected_version=1, assigned_to=self.field)
        self.release(observed_at=self.episode.created_at)
        intake_loop.tick(self.registry, now=self.now)
        self.assertFalse(DepartureIntent.objects.exists())
        self.assertIsNone(field_episodes.prepare_departure_from_release(self.episode, now=self.now))

    def test_clear_fallback_and_audit_when_rosy_has_no_permission(self):
        intake_loop.tick(self.registry, now=self.now)
        confirmation = services.confirm_episode(self.field, episode_id=self.episode.pk, expected_version=1,
                                                outcome='CLEAR', observed_at=self.now, now=self.now)
        intent = services.prepare_departure(self.operator, episode_id=self.episode.pk,
                                            confirmation_id=confirmation.pk, now=self.now)
        self.assertEqual(intent.release_event_id, '')
        self.assertEqual(Audit.objects.get(action='departure.prepare').detail,
                         {'authorized_by': 'field_confirmation', 'confirmation_id': confirmation.pk})

    def test_stale_or_non_clear_confirmation_cannot_authorize(self):
        for outcome in ['CLEAR', 'MISMATCH', 'UNABLE']:
            with self.subTest(outcome=outcome), transaction.atomic():
                confirmation = services.confirm_episode(self.field, episode_id=self.episode.pk, expected_version=1,
                                                        outcome=outcome, observed_at=self.now, now=self.now)
                with self.assertRaises(ValidationError):
                    services.prepare_departure(self.operator, episode_id=self.episode.pk, confirmation_id=confirmation.pk,
                                                now=self.now + timedelta(minutes=6) if outcome == 'CLEAR' else self.now)
                transaction.set_rollback(True)
        self.assertFalse(DepartureIntent.objects.exists())

    def test_exactly_one_authorization_required_in_database(self):
        confirmation = services.confirm_episode(self.field, episode_id=self.episode.pk, expected_version=1,
                                                outcome='CLEAR', observed_at=self.now, now=self.now)
        for fields in [{}, {'confirmation': confirmation, 'release_event_id': self.event_id}]:
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                DepartureIntent.objects.create(created_by=self.operator, actor=self.operator, episode=self.episode,
                                                vehicle_id=self.episode.vehicle_id, cargo=self.episode.order.cargo, **fields)

    def test_trip_failure_keeps_intent_and_next_tick_retries(self):
        self.release()
        def fail_trip(*args, **kwargs):
            # savepoint 안 DB 오류가 intent 저장까지 롤백하면 안 된다.
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM missing_trip_table')
        with mock.patch.object(field_episodes, 'prepare_trip', side_effect=fail_trip):
            with self.assertLogs(level='ERROR'):
                intake_loop.tick(self.registry, now=self.now)
        intent = DepartureIntent.objects.get(episode=self.episode)
        with self.assertRaises(ValueError):
            DepartureLedger(self.path).authorize(self.episode.vehicle_id, since=self.episode.created_at)
        with self.assertRaises(ValueError):
            TransportLedger(self.path).read(intake_loop.trip_key(intent))
        intake_loop.tick(self.registry, now=self.now)
        self.assertEqual(DepartureIntent.objects.get(episode=self.episode).pk, intent.pk)
        self.assertEqual(TransportLedger(self.path).read(intake_loop.trip_key(intent))['state'], 'AWAITING_APPROVAL')
        self.assertEqual(Audit.objects.filter(action='departure.prepare').count(), 1)

    def test_permission_only_belongs_to_latest_vehicle_episode(self):
        latest = services.assign_episode(self.operator, order_id=self.episode.order_id,
                                         vehicle_id=self.episode.vehicle_id, assigned_to=self.field)
        self.now = timezone.now()
        self.release()
        intake_loop.tick(self.registry, now=self.now)
        self.assertFalse(DepartureIntent.objects.filter(episode=self.episode).exists())
        self.assertTrue(DepartureIntent.objects.filter(episode=latest).exists())
        self.assertIsNone(field_episodes.prepare_departure_from_release(self.episode, now=self.now))
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.state, 'OPEN')
        # 최신 회차가 준비된 뒤 새 허락이 와도 옛 회차에 붙이면 안 된다.
        self.event_id = sha256(b'release-2').hexdigest()
        self.release()
        intake_loop.tick(self.registry, now=self.now)
        self.assertFalse(DepartureIntent.objects.filter(episode=self.episode).exists())
        self.assertEqual(DepartureLedger(self.path).authorize(latest.vehicle_id, since=latest.created_at), self.event_id)

    def test_one_episode_error_does_not_block_other_vehicle(self):
        other = services.assign_episode(self.operator, order_id=self.episode.order_id,
                                        vehicle_id='other-vehicle', assigned_to=self.field)
        self.now = timezone.now()
        self.release(vehicle_id='other-vehicle')
        real = field_episodes.prepare_departure_from_release
        def prepare(episode, **kwargs):
            if episode.pk == self.episode.pk:
                raise ValueError('bad episode')
            return real(episode, **kwargs)
        with mock.patch.object(field_episodes, 'prepare_departure_from_release', side_effect=prepare):
            with self.assertLogs('harbor_web.intake_loop', level='ERROR'):
                intake_loop.tick(self.registry, now=self.now)
        self.assertTrue(DepartureIntent.objects.filter(episode=other).exists())

    def test_other_value_error_is_not_missing_permission(self):
        with mock.patch.object(DepartureLedger, 'authorize', side_effect=ValueError('invalid vehicle')):
            with self.assertRaisesRegex(ValueError, 'invalid vehicle'):
                field_episodes.prepare_departure_from_release(self.episode, now=self.now)

    def test_automatic_intent_has_no_human_actor(self):
        self.release()
        intake_loop.tick(self.registry, now=self.now)
        intent = DepartureIntent.objects.get(episode=self.episode)
        self.assertIsNone(intent.actor_id)
        self.assertIsNone(intent.created_by_id)
        self.assertIsNone(Audit.objects.get(action='departure.prepare').actor_id)

    def test_human_prepare_returns_rosy_intent_and_hides_button(self):
        confirmation = services.confirm_episode(self.field, episode_id=self.episode.pk, expected_version=1,
                                                outcome='CLEAR', observed_at=self.now, now=self.now)
        self.release()
        intake_loop.tick(self.registry, now=self.now)
        intent = DepartureIntent.objects.get(episode=self.episode)
        self.assertEqual(services.prepare_departure(self.operator, episode_id=self.episode.pk,
                         confirmation_id=confirmation.pk, now=self.now).pk, intent.pk)
        self.client.force_login(self.operator)
        response = self.client.post(f'/field/{self.episode.pk}/',
                                    {'action': 'departure', 'confirmation_id': confirmation.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ROSY 허락으로 이미 출발 준비됨')
        self.assertNotContains(response, '같은 차량의 출발 전제 기록</button>')
        self.assertEqual(DepartureIntent.objects.count(), 1)

    def test_missing_permission_raises_specific_value_error_subclass(self):
        with self.assertRaises(NoReleasePermit) as raised:
            DepartureLedger(self.path).authorize(self.episode.vehicle_id, since=self.episode.created_at)
        self.assertIsInstance(raised.exception, ValueError)
        self.assertIsNone(field_episodes.prepare_departure_from_release(self.episode, now=self.now))
