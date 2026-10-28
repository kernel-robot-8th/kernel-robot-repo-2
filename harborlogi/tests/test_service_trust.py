"""Pinned application identity must be checked before a Fleet mutation."""
import unittest
from types import SimpleNamespace

from harbor.rosy_http import RosyClient


class ServiceTrustTests(unittest.TestCase):
    def client(self, session):
        self.calls = []
        self.identity = session
        def transport(method, path, body):
            self.calls.append((method,path))
            if path == '/api/fleet/session':
                return self.identity
            return {'job': {'proposal_principal_id': 'harbor-service'}}
        client = RosyClient(transport=transport)
        client.expected_principal = 'harbor-service'
        client.expected_role = 'service'
        return client

    def binding(self):
        return SimpleNamespace(proposal_request=lambda key: {'request_key':key})

    def test_wrong_identity_or_human_role_never_sends_mutation(self):
        for session in ({'principal_id':'other-service','role':'service'},
                        {'principal_id':'harbor-service','role':'operator'},
                        {'principal_id':'harbor-service','role':'viewer'},
                        {'principal_id':'harbor-service','role':[]}, {}, [], None):
            for action in ('propose','cancel'):
                with self.subTest(session=session,action=action):
                    client = self.client(session)
                    with self.assertRaises(ValueError):
                        if action == 'propose': client.propose_arm(self.binding(),request_key='key')
                        else: client.cancel_arm('mission',reason='requested stop')
                    self.assertEqual(self.calls,[('GET','/api/fleet/session')])

    def test_every_write_rechecks_identity_and_does_not_reuse_old_probe(self):
        client = self.client({'principal_id':'harbor-service','role':'service'})
        client.propose_arm(self.binding(),request_key='key')
        self.assertEqual(self.calls,[('GET','/api/fleet/session'),('POST','/api/fleet/arm-transfers/jobs')])
        self.identity = {'principal_id':'different-service','role':'service'}
        with self.assertRaises(ValueError):
            client.cancel_arm('mission',reason='requested stop')
        self.assertEqual(sum(method=='POST' for method,_ in self.calls),1)
        self.assertEqual(self.calls[-1],('GET','/api/fleet/session'))

    def test_same_principal_and_service_role_can_cancel_without_approval_authority(self):
        client = self.client({'principal_id':'harbor-service','role':'service'})
        client.cancel_arm('mission',reason='requested stop')
        self.assertEqual(self.calls[-1],('POST','/api/fleet/arm-transfers/jobs/mission/cancel'))
        self.assertFalse(any('/admit' in path for _,path in self.calls))

    def test_wrong_service_cannot_read_or_reconcile_another_identity(self):
        client = self.client({'principal_id':'other-service','role':'service'})
        for read in (lambda:client.arm_request('key'),lambda:client.arm_job('mission'),
                     lambda:client.physical_receipt('mission')):
            self.calls.clear()
            with self.assertRaises(ValueError): read()
            self.assertEqual(self.calls,[('GET','/api/fleet/session')])

    def test_real_http_rechecks_current_identity_before_a_write(self):
        from test_http_execution import server
        calls = []
        identity = {'principal_id':'harbor-service','role':'service'}
        def endpoint(method,path,body):
            calls.append((method,path))
            return identity if path == '/api/fleet/session' else {'job': {'proposal_principal_id':'harbor-service'}}
        with server(endpoint) as origin:
            client = RosyClient(origin,'synthetic-operator')
            client.expected_principal, client.expected_role = 'harbor-service','service'
            client.propose_arm(self.binding(),request_key='same-key')
            identity['role'] = 'operator'
            with self.assertRaises(ValueError): client.cancel_arm('mission',reason='requested stop')
        self.assertEqual(calls,[('GET','/api/fleet/session'),('POST','/api/fleet/arm-transfers/jobs'),
                                ('GET','/api/fleet/session')])
