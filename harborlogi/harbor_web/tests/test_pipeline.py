import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from harbor_web.pipeline import connection_summary, fleet_links, pipeline_for
from harbor_web.tests import test_commands as command_fixtures
from harbor_web import commands
from harbor_web.models import Audit, CommandFact


class PipelineTests(TestCase):
    def context(self, state='RECONCILED', status=None, receipt=None, reason='', ledger_state='WAITING'):
        order = SimpleNamespace(pk=1)
        plan = SimpleNamespace(pk=2, result={'status': 'READY'})
        command = SimpleNamespace(pk=3, state=state, mission_id='job/1?x=y',
                                  fleet_origin='https://fleet.example')
        detail = dict(job={'status': status} if status else None, receipt=receipt,
                      ledger={'state': ledger_state}, reason=reason, read_at='saved-time')
        return pipeline_for(order, plan, command, detail)

    def test_links_validate_origin_and_encode_known_job(self):
        links = fleet_links('https://fleet.example', 'job/1?x=y', 'robot&1', arm_console_enabled=True)
        self.assertEqual(links['arm'], 'https://fleet.example/console?view=work&arm_transfer=job%2F1%3Fx%3Dy')
        self.assertEqual(links['robot'], 'https://fleet.example/console?robot=robot%261')
        self.assertIsNone(fleet_links('https://fleet.example', 'job')['arm'])
        for origin in ('javascript:alert(1)', 'https://user:secret@fleet.example', 'http://fleet.example',
                       'https://fleet.example/path', 'https://fleet.example/?token=secret', 'https://fleet.example\n'):
            with self.subTest(origin=origin):
                self.assertIsNone(fleet_links(origin)['console'])

    def test_unknown_never_offers_resubmit_or_approval(self):
        for state in ('UNKNOWN', 'DISPATCHING', 'CANCEL_PENDING'):
            value = self.context(state, 'PROPOSED')
            self.assertEqual(value['primary']['action'], 'reconcile')

    def test_receipt_does_not_complete_business_and_time_is_saved_time(self):
        value = self.context(status='GOAL_CONFIRMED', receipt={'receipt_id': 'receipt'})
        stages = {s['key']: s for s in value['stages']}
        self.assertEqual(stages['physical']['status'], 'done')
        self.assertNotEqual(stages['business']['status'], 'done')
        self.assertEqual(value['read_at'], 'saved-time')

    def test_action_success_still_awaits_physical_proof(self):
        value = self.context(status='ACTION_SUCCEEDED')
        stages = {s['key']: s for s in value['stages']}
        self.assertEqual(stages['execution']['status'], 'done')
        self.assertNotEqual(stages['physical']['status'], 'done')

    def test_held_business_does_not_encourage_proposal_approval(self):
        with patch.dict(os.environ, {'HARBOR_FLEET_ARM_CONSOLE_ENABLED': '1'}):
            self.assertEqual(self.context(status='PROPOSED', ledger_state='HOLD')['primary']['action'], 'reconcile')

    def test_admission_link_requires_explicit_ui_install_declaration(self):
        with patch.dict(os.environ, {'HARBOR_FLEET_ARM_CONSOLE_ENABLED': '0'}):
            self.assertEqual(self.context(status='PROPOSED')['primary']['action'], 'reconcile')
        with patch.dict(os.environ, {'HARBOR_FLEET_ARM_CONSOLE_ENABLED': '1'}):
            self.assertEqual(self.context(status='PROPOSED')['primary']['action'], 'fleet')

    def test_connection_summary_does_not_publish_credentials_or_local_paths(self):
        with patch.dict(os.environ, {'HARBOR_FLEET_ORIGIN': 'https://fleet.example',
                'HARBOR_FLEET_TOKEN': 'secret-token', 'HARBOR_FLEET_PRINCIPAL': 'private-principal',
                'HARBOR_LEDGER_PATH': 'private-ledger-path', 'HARBOR_BINDINGS_PATH': 'private-binding-path'}):
            value = connection_summary()
        self.assertTrue(value['configured'])
        for private in ('secret-token', 'private-principal', 'private-ledger-path', 'private-binding-path'):
            self.assertNotIn(private, repr(value))
        self.assertFalse(value['verified'])


class PipelinePageTests(TestCase):
    setUp = command_fixtures.CommandTests.setUp
    cargo = command_fixtures.CommandTests.cargo
    condition = command_fixtures.CommandTests.condition
    observation = command_fixtures.CommandTests.observation
    order = command_fixtures.CommandTests.order
    plan = command_fixtures.CommandTests.plan
    job = command_fixtures.CommandTests.job

    def test_get_work_pages_never_probes_or_writes(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        self.client.force_login(self.operator)
        counts = (Audit.objects.count(), CommandFact.objects.count())
        with patch('harbor_web.commands.acquire_registry', side_effect=AssertionError('GET acquired writer')), \
                patch('harbor.rosy_http.RosyClient._http', side_effect=AssertionError('GET contacted Fleet')):
            detail = self.client.get(f'/work/{command.pk}/')
            listing = self.client.get('/work/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertContains(detail, 'name="action" value="submit"', count=1)
        self.assertNotContains(detail, 'name="action" value="reconcile"')
        self.assertNotContains(detail, 'name="action" value="cancel"')
        self.assertEqual((Audit.objects.count(), CommandFact.objects.count()), counts)
        command.refresh_from_db()
        self.assertEqual(command.version, 1)

    def test_saved_fact_timestamp_is_not_render_time(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        commands._prepare_ledger(command, registry)
        registry.ledger.hold(commands._key(command))
        with patch.object(registry.client, 'arm_request', return_value={'job': self.job(command)}):
            command = commands.reconcile_command(self.operator, command_id=command.pk, expected_version=1)
        fact = CommandFact.objects.get(command=command)
        with patch('django.utils.timezone.now', side_effect=AssertionError('render-time timestamp')):
            self.assertEqual(commands.command_detail(command)['read_at'], fact.read_at)
