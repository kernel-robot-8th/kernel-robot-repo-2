from copy import deepcopy
import json
from pathlib import Path
import unittest

from tools.design_tokens import build_css


ROOT = Path(__file__).resolve().parents[1]


class DesignTokenTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((ROOT / 'design/tokens.json').read_text(encoding='utf-8'))

    def test_committed_stylesheet_is_exactly_generated_from_adopted_source(self):
        self.assertEqual(self.source['meta']['status'], 'adopted')
        self.assertTrue(self.source['meta']['runtimeConsumer'])
        self.assertEqual(build_css(self.source), (ROOT / 'harbor/web_assets/tokens.css').read_text(encoding='utf-8'))

    def test_unknown_reference_wrong_type_cycle_and_name_collision_fail(self):
        for value in ('{semantic.missing}', '{primitive.size.control}', '{component.button.background}'):
            data = deepcopy(self.source)
            data['semantic']['action']['primary']['$value'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_css(data)
        data = deepcopy(self.source)
        data['primitive']['a-b'] = {'c': {'$type': 'number', '$value': 1}}
        data['primitive']['a'] = {'b-c': {'$type': 'number', '$value': 2}}
        with self.assertRaises(ValueError):
            build_css(data)


if __name__ == '__main__':
    unittest.main()
