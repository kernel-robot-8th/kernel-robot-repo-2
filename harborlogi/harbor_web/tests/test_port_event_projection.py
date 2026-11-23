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
