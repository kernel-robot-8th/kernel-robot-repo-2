"""Cell plans on a production ROSY: their own readiness gate, a system-refreshed check, a Fleet-only executor and R2."""
import json
import os
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import TestCase
from django.utils import timezone

from harbor.ownership import WriterLease
from harbor_web import cell_plan_dispatch, executor, fleet_connection as bridge, readiness, rearrangement, services
from harbor_web.models import Audit, CellPlanDispatch, Command, Plan
from harbor_web.tests import test_cell_plan_dispatch as base

PRINCIPAL = 'harbor-service'
# Production ROSY: arms closed and arm-transfers absent, yet cell plans, incidents and producers answer.
CELL_PLAN_READY = dict(arm='UNMET', arm_clear='MET', evidence='UNMET', recheck='UNMET', proposal='UNMET',
                       arm_live='UNMET', auto_producer='MET', cell_plans='MET', port_events='MET')


def _error(status, code=None):
    body = json.dumps({'detail': {'code': code}}).encode() if code else b'{"detail":"Not Found"}'
    return HTTPError('https://fleet.example', status, 'err', {}, BytesIO(body))


class ProductionRosy:
    """RosyClient._http stand-in for a production-profile ROSY that runs cell plans only."""

    def __init__(self):
        self.cells = base._FakeCellPlanClient()
        self.calls = []

    def __call__(self, client, method, path, body):
        self.calls.append((method, path))
        if path == '/api/fleet/session':
            return {'principal_id': PRINCIPAL, 'role': 'service'}
        if path.startswith('/api/fleet/arm-transfers/'):
            raise _error(503, 'ARM_TRANSFER_JOB_UNAVAILABLE')
        if path.startswith('/api/fleet/port-events'):
            raise _error(400, 'PORT_EVENT_CURSOR_INVALID')
        if path == '/api/fleet/workcell-observations':
            return {'workcells': []}
        if path == '/api/fleet/execution-profile':
            return dict(profile='production', arm_execution='closed', observation_producers=[dict(authenticated=True)],
                        evidence_producers=[dict(authenticated=True)])
        if method == 'GET' and path.startswith('/api/fleet/pipeline/cell-plans?request_key='):
            raise _error(404, 'UNKNOWN_CELL_PLAN')
        if method == 'POST' and path == '/api/fleet/pipeline/cell-plans':
            payload = {k: v for k, v in body.items() if k != 'request_key'}
            return self.cells.propose_cell_plan(payload, request_key=body['request_key'])
        raise _error(404)


class CellPlanProductionPathTests(TestCase):
    setUp_base = base.CellPlanDispatchTests.setUp

    def setUp(self):
        self.setUp_base()
        # A ROSY port-event observation: production-eligible while the port-events route answers (readiness rule).
        self.observation = services.record_observation(
            self.operator, source_id='cam-top', source_revision='rev-2', observed_at=timezone.now(),
            provenance='PORT_EVENT', condition=self.condition, payload=self.observation.payload)
        self.plan = services.make_plan(self.operator, order_id=self.order.pk, expected_version=1,
                                       observation_id=self.observation.pk)
        self.assertEqual(self.plan.result['status'], 'READY')
        self.addCleanup(executor.clear_backoff)

    def check(self, session='MATCHED', **conditions):
        detail = dict(status='FAILED', session=session, arm='NOT_CHECKED', reason='ARM_NOT_INSTALLED',
                      conditions=dict(CELL_PLAN_READY, **conditions), fingerprint=bridge.fingerprint(bridge.connection_settings()))
        Audit.objects.create(actor=self.operator, action='fleet.check', object_type='FleetConnection', object_id='1', detail=detail)

    def site(self, folder):
        return patch.dict(os.environ, HARBOR_FLEET_ORIGIN='https://fleet.example', HARBOR_FLEET_PRINCIPAL=PRINCIPAL,
                          HARBOR_FLEET_TOKEN='t', HARBOR_LEDGER_PATH=str(Path(folder) / 'ledger.db'))

    def release_fleet_only_lease(self):
        if executor._fleet_only_lease is not None:
            executor._fleet_only_lease.__exit__()
            executor._fleet_only_lease = None

    # Problem 1: the cell-plan gate leaves out the legacy ARM rows.
    def test_cell_plan_gate_opens_without_legacy_arm_conditions(self):
        self.assertEqual(readiness.first_unmet_cell_plan(self.observation), 'READINESS_CHECK_REQUIRED')
        self.check()
        self.assertEqual(readiness.first_unmet_cell_plan(self.observation), '')
        self.assertEqual(readiness.first_unmet(self.plan), 'READINESS_BINDING_REQUIRED')  # legacy Command gate unchanged
        after_expiry = bridge.last_check().created_at + readiness.MAX_AGE + timedelta(seconds=1)
        with patch('harbor_web.readiness.timezone.now', return_value=after_expiry):
            self.assertEqual(readiness.first_unmet_cell_plan(self.observation), 'READINESS_CHECK_STALE')

    def test_each_cell_plan_gate_blocks_with_its_own_reason(self):
        for session, conditions, expected in [
                ('MISMATCH', {}, 'READINESS_SESSION_UNMET'),
                ('MATCHED', dict(arm_clear='UNMET'), 'READINESS_WORKCELL_INCIDENT_HOLD'),
                ('MATCHED', dict(auto_producer='UNMET'), 'READINESS_EVIDENCE_PRODUCER_REQUIRED'),
                ('MATCHED', dict(cell_plans='UNMET'), 'READINESS_CELL_PLAN_ROUTE_UNMET'),
                ('MATCHED', dict(cell_plans='UNKNOWN'), 'READINESS_CELL_PLAN_ROUTE_UNMET')]:
            self.check(session=session, **conditions)
            self.assertEqual(readiness.first_unmet_cell_plan(self.observation), expected)
        self.check(port_events='UNMET')
        self.assertEqual(readiness.first_unmet_cell_plan(self.observation), 'READINESS_OBSERVATION_UNAUTHENTICATED')
        self.check()
        self.observation.provenance = 'SYNTHETIC'
        self.assertEqual(readiness.first_unmet_cell_plan(self.observation), 'READINESS_OBSERVATION_UNAUTHENTICATED')

    def test_operator_button_and_directive_dispatch_in_production(self):
        self.check()
        fake = base._FakeCellPlanClient()
        with patch.object(cell_plan_dispatch, '_default_client', return_value=fake):
            row = cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk)
            self.assertEqual((row.state, row.order_source), ('PROPOSED', 'harbor'))
            CellPlanDispatch.objects.filter(pk=row.pk).update(state='COMPLETED')
            row = cell_plan_dispatch.dispatch_rosy_directive(self.operator, order_id=self.order.pk,
                                                             observation_id=self.observation.pk)
            self.assertEqual((row.state, row.order_source), ('PROPOSED', 'rosy_r'))
            CellPlanDispatch.objects.filter(pk=row.pk).update(state='COMPLETED')
            self.check(arm_clear='UNMET')
            with self.assertRaises(services.Conflict) as held:
                cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk)
        self.assertEqual(str(held.exception), 'READINESS_WORKCELL_INCIDENT_HOLD')
        self.assertEqual(len(fake.calls), 2)

    # Problem 2: the executor keeps the read-only check fresh as the system.
    def test_system_refresh_records_a_check_only_when_due(self):
        self.assertIsNone(bridge.refresh_check_as_system())  # unconfigured: nothing to check, nothing recorded
        self.assertFalse(Audit.objects.filter(action='fleet.check').exists())
        rosy = ProductionRosy()
        with tempfile.TemporaryDirectory() as folder, self.site(folder), \
                patch('harbor.rosy_http.RosyClient._http', autospec=True, side_effect=rosy):
            result = bridge.refresh_check_as_system()
            self.assertEqual((result['session'], result['conditions']['cell_plans']), ('MATCHED', 'MET'))
            self.assertTrue(all(method == 'GET' for method, _ in rosy.calls))
            stored = bridge.last_check()
            self.assertEqual((stored.actor, stored.detail['source']), (None, 'system'))
            self.assertEqual(readiness.first_unmet_cell_plan(self.observation), '')
            self.assertIsNone(bridge.refresh_check_as_system(now=timezone.now() + timedelta(minutes=4)))
            self.assertIsNotNone(bridge.refresh_check_as_system(now=timezone.now() + timedelta(minutes=6)))
        self.assertEqual(Audit.objects.filter(action='fleet.check', actor=None).count(), 2)

    # Problem 3: without the legacy bindings the executor still runs the cell-plan loop.
    def test_executor_without_bindings_refreshes_and_proposes_by_cell_plan(self):
        self.addCleanup(self.release_fleet_only_lease)
        rosy = ProductionRosy()
        with tempfile.TemporaryDirectory() as folder, self.site(folder), \
                patch('harbor.rosy_http.RosyClient._http', autospec=True, side_effect=rosy), \
                patch('harbor_web.commands.advance_command') as advance:
            result = executor.tick()
            self.release_fleet_only_lease()
        self.assertTrue(result['ran'])
        advance.assert_not_called()
        self.assertTrue(Audit.objects.filter(action='fleet.check', actor=None, detail__source='system').exists())
        row = CellPlanDispatch.objects.get(plan=self.plan)
        self.assertEqual((row.state, row.created_by.username), ('PROPOSED', cell_plan_dispatch.AUTO_DISPATCH_USERNAME))
        self.assertIn(('POST', '/api/fleet/pipeline/cell-plans'), rosy.calls)
        self.assertFalse(any(m == 'POST' and '/arm-transfers' in p for m, p in rosy.calls))

    def test_fleet_only_loop_stays_idle_while_another_owns_the_lease(self):
        self.addCleanup(self.release_fleet_only_lease)
        with tempfile.TemporaryDirectory() as folder, self.site(folder), \
                patch('harbor.rosy_http.RosyClient._http', autospec=True, side_effect=ProductionRosy()):
            other = WriterLease(str(Path(folder) / 'ledger.db') + '.cell-plan').__enter__()
            try:
                self.assertIsNone(executor._fleet_only_registry())
                self.assertEqual(executor.tick()['reason'], 'EXECUTION_CONFIGURATION_REQUIRED')
            finally:
                other.__exit__()
            self.assertIsInstance(executor._fleet_only_registry(), cell_plan_dispatch.FleetOnlyRegistry)
            self.release_fleet_only_lease()

    # Problem 4: an authorized automatic rearrangement goes out as a cell plan when the Command gate is closed.
    def test_automatic_rearrangement_is_proposed_as_a_cell_plan(self):
        rearrangement.enable_authorization(self.operator)
        self.check()
        fake = base._FakeCellPlanClient()
        with patch.object(cell_plan_dispatch, '_default_client', return_value=fake):
            result = rearrangement.auto_check()
        self.assertIsInstance(result, CellPlanDispatch, result)
        plan = Plan.objects.get(order__kind='REARRANGE')
        self.assertEqual((result.plan_id, result.state, result.dispatch_mode), (plan.pk, 'PROPOSED', 'HARBOR_PLAN'))
        steps = result.request_payload['steps']
        self.assertEqual(len(steps), len(plan.result['moves']))
        self.assertTrue(all(step['src_site'] and step['dst_site'] for step in steps))
        self.assertFalse(Command.objects.filter(order__kind='REARRANGE').exists())
        self.assertTrue(Audit.objects.filter(action='rearrange.cell_plan_proposed', object_id=str(result.pk)).exists())
        self.assertEqual(rearrangement._automatic_today(timezone.now()), 1)  # the daily limit counts cell plans too
        # One cell job at a time: the live proposal holds the next automatic check.
        self.assertEqual(rearrangement.auto_check(), 'OTHER_COMMAND_ACTIVE')

    # Review follow-ups: fallback scope, one submitter at a time, no Plan while no route is open.
    def test_fleet_only_fallback_needs_every_other_setting_valid(self):
        self.addCleanup(self.release_fleet_only_lease)
        with tempfile.TemporaryDirectory() as folder, self.site(folder), \
                patch('harbor.rosy_http.RosyClient._http', autospec=True, side_effect=ProductionRosy()):
            for extra in ({'HARBOR_FLEET_PRINCIPAL': 'bad\x01principal'},  # bindings missing too: still refused
                          {'HARBOR_FLEET_PRINCIPAL': 'bad\x01principal',
                           'HARBOR_BINDINGS_PATH': str(Path(folder) / 'bindings.json')}):
                with self.subTest(extra=sorted(extra)), patch.dict(os.environ, extra):
                    self.assertIsNone(executor._fleet_only_registry())
                    self.assertEqual(executor.tick()['reason'], 'EXECUTION_CONFIGURATION_REQUIRED')
        self.assertIsNone(executor._fleet_only_lease)
        self.assertFalse(CellPlanDispatch.objects.exists())

    def test_automatic_submissions_run_under_the_queue_lock(self):
        rearrangement.enable_authorization(self.operator)
        self.check()
        fake = base._FakeCellPlanClient()
        with patch('harbor_web.coordination.queue_lock') as lock:
            row = cell_plan_dispatch.auto_dispatch_next(client=fake, production=False)
            self.assertEqual(lock.__enter__.call_count, 1)
            CellPlanDispatch.objects.filter(pk=row.pk).update(state='COMPLETED')
            with patch.object(cell_plan_dispatch, '_default_client', return_value=fake):
                cell_plan_dispatch.dispatch_harbor_plan(self.operator, plan_id=self.plan.pk)
            self.assertEqual(lock.__enter__.call_count, 2)

    def test_closed_route_holds_rearrangement_without_making_a_plan(self):
        rearrangement.enable_authorization(self.operator)
        for _ in range(2):
            self.assertEqual(rearrangement.auto_check(), 'READINESS_CHECK_REQUIRED')
        self.assertFalse(Plan.objects.filter(order__kind='REARRANGE').exists())
        self.assertEqual(list(Audit.objects.filter(action=rearrangement.HOLD_ACTION).values_list('detail__reason', flat=True)),
                         ['READINESS_CHECK_REQUIRED'])
        self.check()  # the gate opens later: the same observation is still planned and proposed
        with patch.object(cell_plan_dispatch, '_default_client', return_value=base._FakeCellPlanClient()):
            self.assertIsInstance(rearrangement.auto_check(), CellPlanDispatch)
        self.assertEqual(Plan.objects.filter(order__kind='REARRANGE').count(), 1)
