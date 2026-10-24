from django.test import SimpleTestCase
from harbor.ownership import WriterLease
import tempfile
from pathlib import Path
import os
import json
from unittest.mock import patch
from harbor_web.tests import test_services as fixtures
from django.test import TestCase
from harbor_web import commands
from harbor_web.models import Command, CommandFact
from harbor_web.services import Conflict
from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import User


class LeaseTests(SimpleTestCase):
    def test_second_writer_refused_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite3'
            with WriterLease(path):
                with self.assertRaises(RuntimeError):
                    with WriterLease(path):
                        pass
            with WriterLease(path):
                pass


class CommandTests(TestCase):
    def test_restore_gate_reason_survives_capability_and_submission(self):
        plan = self.plan()
        command = commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1)
        with patch('harbor_web.runtime.execution_block_reason', return_value='RESTORE_RECONCILIATION_REQUIRED'):
            self.assertEqual(commands.capability(plan)['reason'], 'RESTORE_RECONCILIATION_REQUIRED')
            result = commands.submit_command(self.operator, command_id=command.pk, expected_version=command.version)
            self.assertEqual(result.state, 'BLOCKED')
            self.assertEqual(result.reason, 'RESTORE_RECONCILIATION_REQUIRED')

    cargo = fixtures.ServicesTests.cargo
    condition = fixtures.ServicesTests.condition
    observation = fixtures.ServicesTests.observation
    order = fixtures.ServicesTests.order

    def setUp(self):
        fixtures.ServicesTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(commands.registry_close)
        root = Path(self.directory.name)
        bindings = root / 'bindings.json'
        bindings.write_text(json.dumps([dict(cargo_id='C1', source='bay', destination='out', workcell_id='cell',
            instance_id='instance', object_ref='cargo', source_ref='bay-ref', target_ref='out-ref')]))
        self.environment = patch.dict(os.environ, dict(HARBOR_LEDGER_PATH=str(root / 'ledger.db'),
            HARBOR_FLEET_ORIGIN='http://127.0.0.1:9999', HARBOR_FLEET_TOKEN='test',
            HARBOR_FLEET_PRINCIPAL='operator-1', HARBOR_BINDINGS_PATH=str(bindings), HARBOR_WEB_ALLOW_SIMULATION='1'))
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def plan(self, provenance='SYNTHETIC'):
        from harbor_web import services
        order = self.order()
        observation = self.observation(provenance=provenance)
        return services.make_plan(self.operator, order_id=order.pk, expected_version=1, observation_id=observation.pk)

    def test_declared_observation_cannot_enable_execution(self):
        self.assertFalse(commands.capability(self.plan('DECLARED'))['enabled'])

    def test_prepare_idempotent_and_no_ledger_business_yet(self):
        plan = self.plan()
        command = commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1)
        self.assertEqual(commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=1).pk, command.pk)
        self.assertIsNone(commands.command_detail(command)['ledger'])
        with self.assertRaises(Conflict):
            commands.prepare_command(self.operator, plan_id=plan.pk, expected_version=2)

    def test_role_explicit_even_superuser(self):
        plan = self.plan()
        root = User.objects.create_superuser('command-root')
        with self.assertRaises(PermissionDenied):
            commands.prepare_command(root, plan_id=plan.pk, expected_version=1)

    def test_failed_dispatch_is_never_posted_again(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        with patch.object(registry.client, 'propose_arm', side_effect=TimeoutError) as post:
            command = commands.submit_command(self.operator, command_id=command.pk, expected_version=1)
            self.assertEqual(command.state, 'UNKNOWN')
            with self.assertRaises(Conflict):
                commands.submit_command(self.operator, command_id=command.pk, expected_version=command.version)
            self.assertEqual(post.call_count, 1)
        self.assertEqual(commands.command_detail(command)['ledger']['state'], 'HOLD')

    def test_identity_rotation_refused(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        with patch.dict(os.environ, HARBOR_FLEET_PRINCIPAL='other'):
            with self.assertRaises(Conflict):
                commands.submit_command(self.operator, command_id=command.pk, expected_version=1)

    def test_interruption_preserves_dispatch_marker(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        with patch.object(registry.client, 'propose_arm', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                commands.submit_command(self.operator, command_id=command.pk, expected_version=1)
        command.refresh_from_db()
        self.assertEqual(command.state, 'DISPATCHING')
        self.assertEqual(commands.command_detail(command)['ledger']['state'], 'HOLD')

    def job(self, command):
        import hashlib
        from harbor.process import ArmTransferBinding
        binding = ArmTransferBinding(**command.binding['moves'][0])
        request_key = commands._key(command) + ':0'
        transfer = {k: v for k, v in binding.proposal_request(request_key).items() if k != 'request_key'}
        return dict(action_kind='ARM_TRANSFER', request_key=request_key, transfer=transfer,
            request_digest=hashlib.sha256(json.dumps(transfer, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            mission_id='mission-1', status='READY', proposal_principal_id='operator-1')

    def test_recovery_get_only_and_cancel_ack_keeps_business_hold(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        with patch.object(registry.client, 'propose_arm', side_effect=TimeoutError):
            command = commands.submit_command(self.operator, command_id=command.pk, expected_version=1)
        job = self.job(command)
        with patch.object(registry.client, 'arm_request', return_value={'job': job}), patch.object(registry.client, 'propose_arm') as post:
            command = commands.reconcile_command(self.operator, command_id=command.pk, expected_version=command.version)
            self.assertEqual(command.mission_id, 'mission-1')
            self.assertEqual(command.reason, 'BUSINESS_HOLD')
            post.assert_not_called()
        commands.registry_close()
        self.assertEqual(commands.command_detail(command)['job']['mission_id'], 'mission-1')
        registry = commands.acquire_registry()
        with patch.object(registry.client, 'cancel_arm', return_value={'ack': True}) as cancel:
            command = commands.cancel_command(self.operator, command_id=command.pk, expected_version=command.version, reason='Operator stop')
            cancel.assert_called_once_with('mission-1', reason='Operator stop')
        self.assertEqual(command.state, 'CANCEL_PENDING')
        self.assertEqual(commands.command_detail(command)['ledger']['state'], 'HOLD')

    def test_saved_binding_cannot_be_overwritten(self):
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError, transaction
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        with self.assertRaises(ValidationError):
            Command.objects.filter(pk=command.pk).update(binding={})
        command.binding = {}
        with self.assertRaises(ValidationError):
            command.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Command(pk=command.pk, plan=command.plan, actor=self.operator, plan_version=1,
                ledger_identity='different', fleet_origin='different', fleet_principal='different', body_digest='a'*64, binding={}).save()

    def test_validated_receipt_persisted_without_business_completion(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        commands._prepare_ledger(command, registry)
        registry.ledger.hold(commands._key(command))
        job = self.job(command)
        job.update(status='GOAL_CONFIRMED', resource_policy_digest='a'*64, authority_epoch=1, dispatch_generation=2)
        receipt = dict(mission_id='mission-1', workcell_id='cell', instance_id='instance', object_ref='cargo',
            source='bay-ref', target='out-ref', policy_digest='a'*64, authority_epoch=1, dispatch_generation=2,
            physical_status='AT_TARGET_AND_CLEAR', validated_at_ns=3, request_digest=job['request_digest'],
            evidence_digest='b'*64, receipt_id='receipt-1', action_id='action-1', attempt_id='attempt-1')
        with patch.object(registry.client, 'arm_request', return_value={'job': job}), patch.object(registry.client, 'physical_receipt', return_value={'receipt': receipt}):
            command = commands.reconcile_command(self.operator, command_id=command.pk, expected_version=1)
        commands.registry_close()
        detail = commands.command_detail(command)
        self.assertEqual(detail['receipt']['receipt_id'], 'receipt-1')
        self.assertEqual(detail['ledger']['state'], 'HOLD')
        self.assertEqual(detail['ledger']['goal_confirmed'], 0)
        CommandFact.objects.create(created_by=self.operator, command=command, command_key=command.key,
            body_digest=command.body_digest, fleet_origin=command.fleet_origin, fleet_principal=command.fleet_principal,
            job=job, receipt=dict(receipt, request_digest='c'*64), read_at=self.now + __import__('datetime').timedelta(seconds=5))
        self.assertEqual(commands.command_detail(command)['receipt']['request_digest'], 'c'*64)
        bad = dict(job, mission_id='wrong')
        CommandFact.objects.create(created_by=self.operator, command=command, command_key=command.key,
            body_digest=command.body_digest, fleet_origin=command.fleet_origin, fleet_principal=command.fleet_principal,
            job=bad, receipt=receipt, read_at=self.now + __import__('datetime').timedelta(seconds=10))
        self.assertIsNone(commands.command_detail(command)['job'])

    def test_binding_profile_rotation_blocks_dispatch(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        path = Path(os.environ['HARBOR_BINDINGS_PATH'])
        bindings = json.loads(path.read_text())
        bindings[0]['target_ref'] = 'different-target'
        path.write_text(json.dumps(bindings))
        with patch.object(commands.acquire_registry().client, 'propose_arm') as post:
            result = commands.submit_command(self.operator, command_id=command.pk, expected_version=1)
        post.assert_not_called()
        self.assertEqual(result.state, 'BLOCKED')
        self.assertEqual(result.binding, command.binding)

    def test_cancel_interruption_repaired_after_registry_restart(self):
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        registry = commands.acquire_registry()
        job = self.job(command)
        with patch.object(registry.client, 'propose_arm', return_value={'job': job}), patch.object(registry.client, 'arm_request', return_value={'job': job}):
            command = commands.submit_command(self.operator, command_id=command.pk, expected_version=1)
        with patch.object(registry.ledger, 'hold', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                commands.cancel_command(self.operator, command_id=command.pk, expected_version=command.version, reason='stop')
        command.refresh_from_db()
        self.assertEqual(command.state, 'CANCEL_PENDING')
        commands.registry_close()
        registry = commands.acquire_registry()
        self.assertEqual(registry.ledger.read(commands._key(command))['state'], 'HOLD')
        with patch.object(registry.client, 'arm_request', return_value={'job': job}), patch.object(registry.client, 'cancel_arm') as post:
            command = commands.reconcile_command(self.operator, command_id=command.pk, expected_version=command.version)
        post.assert_not_called()
        self.assertEqual(command.state, 'CANCEL_PENDING')
        self.assertEqual(commands.command_detail(command)['ledger']['state'], 'HOLD')

    def test_receipt_grant_digest_can_differ_from_transfer_body_digest(self):
        from harbor.process import ArmTransferBinding
        command = commands.prepare_command(self.operator, plan_id=self.plan().pk, expected_version=1)
        binding = ArmTransferBinding(**command.binding['moves'][0])
        job = self.job(command)
        job.update(status='GOAL_CONFIRMED', resource_policy_digest='a'*64, authority_epoch=1, dispatch_generation=2)
        receipt = dict(mission_id='mission-1', workcell_id='cell', instance_id='instance', object_ref='cargo',
            source='bay-ref', target='out-ref', policy_digest='a'*64, authority_epoch=1, dispatch_generation=2,
            physical_status='AT_TARGET_AND_CLEAR', validated_at_ns=3, request_digest='c'*64,
            evidence_digest='b'*64, receipt_id='receipt-1', action_id='action-1', attempt_id='attempt-1')
        self.assertNotEqual(receipt['request_digest'], job['request_digest'])
        self.assertEqual(binding.check_receipt(job, receipt), receipt)
        with self.assertRaises(ValueError):
            binding.check_receipt(job, dict(receipt, dispatch_generation=3))
        with self.assertRaises(ValueError):
            binding.check_receipt(job, dict(receipt, target='other-scope'))
