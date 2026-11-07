"""D-997 Harbor row: business HOLD notification outbox identity, atomicity, and delivery gate."""
from contextlib import closing
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harbor.hold_notify import HoldNotifier, occurrence_id, pre_send_check, validated_screen_base
from harbor.process import ArmTransferBinding
from harbor.rosy_http import RosyClient
from harbor.workflow import BusinessLedger


FIXED = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)


class HoldNotificationTests(unittest.TestCase):
    def ledger(self, directory, **changes):
        options = dict(placement_sources=('camera',), retreat_sources=('owner',),
                       clock=lambda: FIXED, **changes)
        return BusinessLedger(Path(directory) / 'ledger.db', **options)

    def prepared(self, directory, **changes):
        ledger = self.ledger(directory, **changes)
        binding = ArmTransferBinding('C', 'bay', 'out', 'cell', 'instance', 'object', 'source', 'target')
        ledger.prepare('request', 1, (binding,), outbound_id='C', outbound_stand='out', move_arms=(('A',),))
        transfer = {k: v for k, v in binding.proposal_request('request:0').items() if k != 'request_key'}
        job = dict(action_kind='ARM_TRANSFER', request_key='request:0', transfer=transfer,
            request_digest=hashlib.sha256(json.dumps(transfer, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            mission_id='mission-1', status='READY', resource_policy_digest='c' * 64, authority_epoch=2,
            dispatch_generation=3)
        return ledger, binding, job

    def client(self, job):
        return RosyClient(transport=lambda method, path, body: {'job': dict(job)})

    def outbox(self, ledger):
        return HoldNotifier(ledger).outbox()

    def test_hold_records_one_occurrence_and_polling_keeps_it(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            self.assertEqual(ledger.read('request')['state'], 'HOLD')
            rows = self.outbox(ledger)
            self.assertEqual([r['occurrence_id'] for r in rows], ['request#hold-1'])
            self.assertEqual(rows[0]['delivery_state'], 'PENDING')
            self.assertEqual(ledger.read('request')['hold_sequence'], 1)
            ledger.hold('request')  # HOLD → HOLD bookkeeping is the same occurrence.
            ledger.recover_arm('request', self.client(job), now=FIXED)  # A poll that stays HOLD.
            self.assertEqual(ledger.read('request')['state'], 'HOLD')
            self.assertEqual([r['occurrence_id'] for r in self.outbox(ledger)], ['request#hold-1'])

    def test_hold_exit_then_reentry_creates_a_new_occurrence(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            ledger.resume_arm('request', self.client(job), now=FIXED)
            self.assertEqual(ledger.read('request')['state'], 'WAITING')
            self.assertEqual([r['occurrence_id'] for r in self.outbox(ledger)], ['request#hold-1'])
            ledger.hold('request')
            self.assertEqual(ledger.read('request')['hold_sequence'], 2)
            self.assertEqual([r['occurrence_id'] for r in self.outbox(ledger)],
                             ['request#hold-1', 'request#hold-2'])

    def test_failed_submission_records_a_hold_occurrence(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)

            def transport(method, path, body):
                raise OSError('connection refused')
            with self.assertRaises(OSError):
                ledger.submit('request', RosyClient(transport=transport))
            self.assertEqual(ledger.read('request')['state'], 'HOLD')
            self.assertEqual([r['occurrence_id'] for r in self.outbox(ledger)], ['request#hold-1'])

    def test_outbox_write_is_atomic_with_the_hold_transition(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            with closing(sqlite3.connect(Path(directory) / 'ledger.db')) as direct:  # A conflicting row.
                with direct:
                    direct.execute("INSERT INTO hold_outbox VALUES(?,?,?,?,?,?,?,?)",
                                   ('request#hold-1', 'request', 1, '', '', '{}', 'PENDING', None))
            with self.assertRaises(sqlite3.IntegrityError):
                ledger.hold('request')
            self.assertEqual(ledger.read('request')['state'], 'PLANNED')
            self.assertEqual(ledger.read('request')['hold_sequence'], 0)

    def test_restart_recovery_records_a_new_occurrence_and_preserves_the_outbox(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            with closing(sqlite3.connect(Path(directory) / 'ledger.db')) as direct:  # Crash mid-submission.
                with direct:
                    direct.execute("UPDATE business SET state='SUBMITTING' WHERE request_key='request'")
            reopened = self.ledger(directory)
            self.assertEqual(reopened.read('request')['state'], 'HOLD')
            self.assertEqual(reopened.read('request')['hold_sequence'], 2)
            rows = self.outbox(reopened)
            self.assertEqual([r['occurrence_id'] for r in rows], ['request#hold-1', 'request#hold-2'])
            self.assertEqual([r['delivery_state'] for r in rows], ['PENDING', 'PENDING'])

    def test_body_carries_only_the_allowed_fields(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory, screen_base='https://harbor.invalid')
            ledger.hold('request')
            payload = json.loads(self.outbox(ledger)[0]['payload'])
            self.assertEqual(payload, {
                'classification': 'INTERVENTION_REQUIRED', 'business_id': 'request',
                'occurrence_id': 'request#hold-1', 'occurred_at': FIXED.isoformat(),
                'expires_at': (FIXED + timedelta(hours=1)).isoformat(),
                'screen_url': 'https://harbor.invalid/work/request'})
            self.assertNotIn('?', payload['screen_url'])  # No token or query on the protected link.

    def test_screen_base_must_be_a_protected_origin(self):
        with TemporaryDirectory() as directory:
            for base in ('http://fleet.example/', 'https://harbor.invalid/ops?token=1',
                         'https://user:pw@harbor.invalid/', 'https://harbor.invalid/#frag'):
                with self.subTest(base=base):
                    with self.assertRaises(ValueError):
                        self.prepared(directory, screen_base=base)

    def test_delivery_stays_off_without_approved_channels(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            self.assertEqual(HoldNotifier(ledger).deliver_pending(now=FIXED + timedelta(minutes=5)), [])
            self.assertEqual(self.outbox(ledger)[0]['delivery_state'], 'PENDING')
            self.assertEqual(ledger.read('request')['state'], 'HOLD')  # The local HOLD stays.
            with self.assertRaises(ValueError):
                HoldNotifier(ledger, client=lambda channel, body: None)

    def test_waiting_notification_is_rejected_after_the_hold_ends(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            ledger.resume_arm('request', self.client(job), now=FIXED)
            sent = []
            notifier = HoldNotifier(ledger, channels=('ops',),
                                    client=lambda channel, body: sent.append((channel, body)))
            self.assertEqual(notifier.deliver_pending(now=FIXED + timedelta(minutes=5)), [])
            self.assertEqual(sent, [])
            row = self.outbox(ledger)[0]
            self.assertEqual(row['delivery_state'], 'REJECTED')
            self.assertIn('left this HOLD', row['delivery_note'])

    def test_valid_occurrence_is_sent_once_per_approved_channel(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            sent = []
            notifier = HoldNotifier(ledger, channels=('ops',),
                                    client=lambda channel, body: sent.append((channel, body)))
            self.assertEqual(notifier.deliver_pending(now=FIXED + timedelta(minutes=5)), ['request#hold-1'])
            self.assertEqual([channel for channel, _ in sent], ['ops'])
            self.assertEqual(sent[0][1]['occurrence_id'], 'request#hold-1')
            self.assertEqual(self.outbox(ledger)[0]['delivery_state'], 'SENT')
            self.assertEqual(notifier.deliver_pending(now=FIXED + timedelta(minutes=6)), [])
            self.assertEqual(len(sent), 1)

    def test_expired_occurrence_is_rejected_without_sending(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory, notify_expiry=timedelta(seconds=30))
            ledger.hold('request')
            sent = []
            notifier = HoldNotifier(ledger, channels=('ops',),
                                    client=lambda channel, body: sent.append((channel, body)))
            self.assertEqual(notifier.deliver_pending(now=FIXED + timedelta(minutes=1)), [])
            self.assertEqual(sent, [])
            self.assertEqual(self.outbox(ledger)[0]['delivery_state'], 'REJECTED')
            self.assertEqual(self.outbox(ledger)[0]['delivery_note'], 'Occurrence expired')

    def test_pre_send_check_covers_channel_scope_and_unknown_occurrences(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            with self.assertRaisesRegex(ValueError, 'receive scope'):
                with closing(ledger._connect()) as c:
                    pre_send_check(c, 'request#hold-1', channel='ops', channels=('other',), now=FIXED)
            with self.assertRaisesRegex(ValueError, 'Unknown HOLD occurrence'):
                with closing(ledger._connect()) as c:
                    pre_send_check(c, 'request#hold-9', channel='ops', channels=('ops',), now=FIXED)
            with closing(ledger._connect()) as c:
                row = pre_send_check(c, 'request#hold-1', channel='ops', channels=('ops',), now=FIXED)
                self.assertEqual(row['request_key'], 'request')

    def test_occurrence_identity_does_not_follow_row_version(self):
        with TemporaryDirectory() as directory:
            ledger, binding, job = self.prepared(directory)
            ledger.hold('request')
            version = ledger.read('request')['row_version']
            ledger.hold('request')  # row_version moves, the occurrence does not.
            self.assertEqual(ledger.read('request')['row_version'], version + 1)
            self.assertEqual([r['occurrence_id'] for r in self.outbox(ledger)], ['request#hold-1'])
            self.assertEqual(occurrence_id('request', 2), 'request#hold-2')


if __name__ == '__main__':
    unittest.main()
