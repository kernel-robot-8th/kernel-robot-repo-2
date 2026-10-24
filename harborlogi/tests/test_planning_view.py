import json
import unittest

from harbor.planning_view import preview
from harbor.web import Application, DemoLedger, Workbench
from test_web import request


class PlanningViewTests(unittest.TestCase):
    def test_real_planner_produces_rehandle_outbound_restore_without_changing_observations(self):
        result = preview('C1', 'normal')
        self.assertEqual(result['proposal']['status'], 'READY')
        self.assertEqual([m['phase'] for m in result['proposal']['moves']], ['REHANDLE', 'OUTBOUND', 'RESTORE'])
        self.assertEqual(result['observed_bays'][0]['cargo'], ['C1', 'C2'])
        self.assertEqual(result['projected_bays'][0]['cargo'], ['C2'])
        self.assertEqual(result['projected_bays'][2]['cargo'], ['C1'])
        self.assertEqual(result, preview('C1', 'normal'))
        self.assertEqual(preview('C2', 'normal')['proposal']['moves'][0]['phase'], 'OUTBOUND')
        self.assertEqual(len(preview('C2', 'normal')['proposal']['moves']), 1)

    def test_hold_has_no_partial_moves_or_projected_completion(self):
        for scenario, reason in (('no-space', 'NO_LEGAL_TEMPORARY_BAY'), ('stale', 'OBSERVATION_NOT_CURRENT')):
            with self.subTest(scenario=scenario):
                result = preview('C1', scenario)
                self.assertEqual(result['proposal']['status'], 'HOLD')
                self.assertEqual(result['proposal']['reason'], reason)
                self.assertEqual(result['proposal']['moves'], [])
                self.assertIsNone(result['projected_bays'])
        self.assertEqual(preview('missing', 'normal')['proposal']['reason'], 'OUTBOUND_NOT_OBSERVED')

    def test_api_is_demo_only_and_never_uses_a_live_ledger_or_client(self):
        app = Application(Workbench(DemoLedger(), demo=True))
        status, _, body = request(app, '/api/planning', query='cargo=C1&scenario=normal')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['mode'], 'demo')
        for query in ('scenario=bad', 'cargo=C1&cargo=C2', 'extra=1', 'cargo=', 'scenario='):
            self.assertEqual(request(app, '/api/planning', query=query)[0], 400)
        self.assertEqual(request(app, '/api/planning', method='POST')[0], 405)
        self.assertEqual(request(app, '/api/planning', remote='192.0.2.1')[0], 403)
        live = Application(Workbench(object(), object()))
        status, _, body = request(live, '/api/planning')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)['error'], 'INVENTORY_NOT_CONFIGURED')
        self.assertNotIn('observed_bays', json.loads(body))


if __name__ == '__main__':
    unittest.main()
