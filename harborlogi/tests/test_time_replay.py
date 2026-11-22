from contextlib import redirect_stderr
from dataclasses import replace
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harbor import sequencing
from harbor.sequencing import evaluate_order
from harbor.time_replay import MAX_TIMESTAMP_NS, TimingRecord, build_timing_table, compare_retrieval_times, normalize_site_id
from tools.time_replay_report import DEFAULT_CONFIG, NOW, _policy, _rec, _snapshot, build_report, default_synthetic_records, load_records, main


class TimeReplayTests(unittest.TestCase):
    def _c2_c1_yard(self):
        return _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C4', 2, 1)]), _policy(_snapshot([('C1', 1, 1)]).layout)

    def test_c2_c1_parity_and_step_trace_match_evaluate_order(self):
        yard, policy = self._c2_c1_yard()
        original = yard.placements
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={'C4': (3, 0)},
                                       group=2, timing_table=table, end_to_end_proven=True)
        self.assertEqual(comp.candidate_count, 2)
        self.assertTrue(comp.exhaustive)
        self.assertEqual(comp.saved_order.scenario.order, ('C1', 'C2'))
        self.assertEqual((comp.saved_order.scenario.total_moves, comp.saved_order.scenario.rehandles), (4, 1))
        self.assertEqual(comp.existing_recommendation.scenario.order, ('C2', 'C1'))
        self.assertEqual((comp.existing_recommendation.scenario.total_moves, comp.existing_recommendation.scenario.rehandles), (2, 0))
        for cand in comp.candidates:
            direct = evaluate_order(yard, policy, cand.scenario.order, now=NOW, schedule={'C4': (3, 0)}, group=2)
            self.assertEqual(cand.scenario, replace(direct, exhaustive=True))
            self.assertEqual([s.moves for s in cand.scenario.steps], [s.moves for s in direct.steps])
        self.assertEqual(yard.placements, original)

    def test_four_cargo_expands_24_unique_orders_once_and_caps_exact_limit(self):
        yard = _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C3', 2, 1), ('C4', 3, 1)])
        policy = _policy(yard.layout)
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        with patch('harbor.sequencing.plan_spatial', wraps=sequencing.plan_spatial) as spy:
            comp = compare_retrieval_times(yard, policy, ('C1', 'C2', 'C3', 'C4'), now=NOW, schedule={},
                                           group=1, timing_table=table, end_to_end_proven=True)
        self.assertEqual(comp.candidate_count, 24)
        self.assertEqual(len({c.scenario.order for c in comp.candidates}), 24)
        self.assertTrue(comp.exhaustive)
        self.assertEqual(comp.coverage_ratio, 1.0)
        self.assertLessEqual(spy.call_count, 96)

        yard5 = _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C3', 2, 1), ('C4', 3, 1), ('C5', 2, 2)])
        comp5 = compare_retrieval_times(yard5, policy, ('C1', 'C2', 'C3', 'C4', 'C5'), now=NOW, schedule={},
                                        group=1, timing_table=table, end_to_end_proven=True)
        self.assertEqual(comp5.fallback_reason, 'TIER_EXCEEDS_EXACT_LIMIT')
        self.assertIsNone(comp5.coverage_ratio)
        self.assertEqual(comp5.effective_recommendation, comp5.existing_recommendation)
        self.assertIsNone(comp5.time_candidate)
        self.assertTrue(all(c.transfer_time_seconds is None and c.end_to_end_seconds is None for c in comp5.candidates))
        with self.assertRaises(ValueError):
            compare_retrieval_times(yard5, policy, ('C1', 'C2', 'C3', 'C4', 'C5'), now=NOW, schedule={},
                                    group=1, timing_table=table, exact_limit=5)

    def test_single_cargo_and_duplicate_codes_validation(self):
        yard, policy = self._c2_c1_yard()
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        single = compare_retrieval_times(yard, policy, ('C1',), now=NOW, schedule={},
                                         group=2, timing_table=table, end_to_end_proven=True)
        self.assertEqual(single.fallback_reason, 'TIER_TOO_SMALL')
        self.assertIsNone(single.coverage_ratio)
        with self.assertRaises(ValueError):
            compare_retrieval_times(yard, policy, ('C1', 'C1'), now=NOW, schedule={}, group=2, timing_table=table)
        with self.assertRaises(ValueError):
            normalize_site_id('  ')
        with self.assertRaises(ValueError):
            normalize_site_id('yard-a', {'yard-a': ' '})

    def test_synthetic_inversion_and_sub_nanosecond_median_precision(self):
        yard, policy = self._c2_c1_yard()
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True, invert_c2_c1=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                       group=2, timing_table=table, end_to_end_proven=True)
        self.assertIsNone(comp.fallback_reason)
        self.assertEqual(comp.existing_recommendation.scenario.order, ('C2', 'C1'))
        self.assertEqual(comp.existing_recommendation.transfer_time_seconds, 45.0)
        self.assertEqual(comp.time_candidate.scenario.order, ('C1', 'C2'))
        self.assertEqual(comp.time_candidate.transfer_time_seconds, 20.0)
        self.assertEqual(comp.effective_recommendation, comp.time_candidate)

        # Two-sample median yields 20.5 ns per move -> 41.0 ns for 2 moves vs 40.0 ns for 4 moves
        recs = [
            TimingRecord('A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', DEFAULT_CONFIG, 'synthetic',
                         'PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED', 'synthetic_ns',
                         100, 120, True, 'k1', end_to_end_complete=True),
            TimingRecord('A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', DEFAULT_CONFIG, 'synthetic',
                         'PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED', 'synthetic_ns',
                         100, 121, True, 'k2', end_to_end_complete=True),
            TimingRecord('A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', DEFAULT_CONFIG, 'synthetic',
                         'PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED', 'synthetic_ns',
                         100, 110, True, 'k3', end_to_end_complete=True),
            TimingRecord('A', 'Y11-L2', 'temp-R1C1-L1', 'rehandle', DEFAULT_CONFIG, 'synthetic',
                         'PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED', 'synthetic_ns',
                         100, 110, True, 'k4', end_to_end_complete=True),
            TimingRecord('A', 'temp-R1C1-L1', 'Y11-L1', 'restore', DEFAULT_CONFIG, 'synthetic',
                         'PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED', 'synthetic_ns',
                         100, 110, True, 'k5', end_to_end_complete=True),
        ]
        # 2-move C2->C1 = 20.5 + 10 = 30.5 ns (61 half-ns); 4-move C1->C2 = 10*4 = 40 ns -> wait make C1->C2 = 30 ns (7.5*4? use 10 + 10 + 5 + 5 = 30 ns):
        recs[3] = TimingRecord(**{**recs[3].__dict__, 'end_ns': 105})
        recs[4] = TimingRecord(**{**recs[4].__dict__, 'end_ns': 105})
        # Now C2->C1 = 20.5 + 10 = 30.5 ns (61 half-ns), C1->C2 = 5 + 10 + 5 + 10 = 30.0 ns (60 half-ns) -> C1->C2 strictly faster by 0.5 ns!
        fine_table = build_timing_table(recs, expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        fine = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                       group=2, timing_table=fine_table, end_to_end_proven=True)
        self.assertEqual(fine.time_candidate.scenario.order, ('C1', 'C2'))
        self.assertEqual(fine.time_candidate.transfer_half_ns, 60)
        self.assertEqual(fine.existing_recommendation.transfer_half_ns, 61)

    def test_end_to_end_unproven_and_non_synthetic_external_policy(self):
        yard, policy = self._c2_c1_yard()
        table = build_timing_table(default_synthetic_records(end_to_end_complete=False, invert_c2_c1=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                       group=2, timing_table=table, end_to_end_proven=False)
        self.assertEqual(comp.fallback_reason, 'END_TO_END_BOUNDARY_UNPROVEN')
        self.assertEqual(comp.effective_recommendation, comp.existing_recommendation)
        self.assertEqual(comp.time_candidate.scenario.order, ('C1', 'C2'))
        self.assertEqual(comp.time_candidate.transfer_time_seconds, 20.0)
        self.assertIsNone(comp.time_candidate.end_to_end_seconds)

        # Real/non-synthetic records never assert end_to_end_complete even if caller passes True
        real_recs = [_rec(i, r.arm, r.source_site, r.destination_site, r.transfer_kind,
                          (r.end_ns - r.start_ns) / 1e9, src_type='fleet_world_automatic', e2e=True)
                     for i, r in enumerate(default_synthetic_records(invert_c2_c1=True))]
        real_table = build_timing_table(real_recs, expected_config_version=DEFAULT_CONFIG,
                                        allow_synthetic=False, require_fleet_contract=True)
        self.assertFalse(real_table.all_end_to_end_proven)
        real_comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                            group=2, timing_table=real_table, end_to_end_proven=True)
        self.assertEqual(real_comp.fallback_reason, 'END_TO_END_BOUNDARY_UNPROVEN')
        self.assertEqual(real_comp.effective_recommendation, real_comp.existing_recommendation)
        self.assertIsNone(real_comp.time_candidate.end_to_end_seconds)

    def test_fixed_tier_outputs_counterfactual_only(self):
        yard, policy = self._c2_c1_yard()
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True, invert_c2_c1=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                       group=2, timing_table=table, reorderable=False, end_to_end_proven=True)
        self.assertTrue(comp.counterfactual_only)
        self.assertEqual(comp.fallback_reason, 'FIXED_OR_APPROVED_ORDER')
        self.assertEqual(comp.effective_recommendation, comp.existing_recommendation)
        self.assertEqual(comp.time_candidate.scenario.order, ('C1', 'C2'))

    def test_timing_record_validation_and_exclusions(self):
        base = _rec(1, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='fleet_world_automatic')
        synth = _rec(2, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='synthetic')
        attested = _rec(3, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='operator_attested')
        failed = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-fail', 'succeeded': False})
        retry = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-retry', 'is_retry': True})
        reversed_clk = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-rev', 'end_ns': base.start_ns})
        wrong_cfg = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-cfg', 'config_version': 'other'})
        bad_kind = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-kind', 'transfer_kind': 'unknown'})
        bad_site = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-site', 'source_site': ' '})
        bad_ts = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-ts', 'end_ns': MAX_TIMESTAMP_NS + 1})
        dup = TimingRecord(**base.__dict__)
        # Same duration but shifted absolute timestamps -> must conflict and exclude both
        shifted_a = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-shift', 'start_ns': 100, 'end_ns': 200})
        shifted_b = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-shift', 'start_ns': 101, 'end_ns': 201})
        # Failed + succeeded on same attempt_key -> must conflict and exclude both
        state_a = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-state', 'succeeded': False})
        state_b = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-state', 'succeeded': True})
        unapproved = TimingRecord(**{**base.__dict__, 'attempt_key': 'k-pipe', 'observation_source': 'pipeline_export'})

        # Type mismatches (bool vs int, int vs float) on same attempt_key must conflict in both orders
        for pair in (
            (TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t1', 'succeeded': True}),
             TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t1', 'succeeded': 1})),
            (TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t2', 'succeeded': 1}),
             TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t2', 'succeeded': True})),
            (TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t3', 'start_ns': 100, 'end_ns': 200}),
             TimingRecord(**{**base.__dict__, 'attempt_key': 'k-t3', 'start_ns': 100.0, 'end_ns': 200})),
        ):
            t = build_timing_table(pair, expected_config_version=DEFAULT_CONFIG)
            self.assertEqual(t.accepted_count, 0)
            self.assertEqual(t.rejected_counts['conflicting_attempt'], 2)

        table = build_timing_table(
            [base, synth, attested, failed, retry, reversed_clk, wrong_cfg, bad_kind, bad_site, bad_ts,
             dup, shifted_a, shifted_b, state_a, state_b, unapproved],
            expected_config_version=DEFAULT_CONFIG,
            allow_synthetic=False,
            allowed_sources={'fleet_world_automatic', 'operator_attested'},
            require_fleet_contract=True,
        )
        self.assertEqual(table.accepted_count, 1)
        self.assertEqual(table.rejected_counts['synthetic_disallowed'], 1)
        self.assertEqual(table.rejected_counts['operator_attested_excluded'], 1)
        self.assertEqual(table.rejected_counts['failed'], 1)
        self.assertEqual(table.rejected_counts['retry'], 1)
        self.assertEqual(table.rejected_counts['clock_reversal'], 1)
        self.assertEqual(table.rejected_counts['config_mismatch'], 1)
        self.assertEqual(table.rejected_counts['invalid_kind'], 1)
        self.assertEqual(table.rejected_counts['invalid_site'], 1)
        self.assertEqual(table.rejected_counts['invalid_timestamp'], 1)
        self.assertEqual(table.rejected_counts['duplicate_attempt'], 1)
        self.assertEqual(table.rejected_counts['conflicting_attempt'], 4)
        self.assertEqual(table.rejected_counts['unapproved_fleet_contract'], 1)
        self.assertEqual(normalize_site_id('  yard-a  ', {'yard-a': 'Y11-L1'}), 'Y11-L1')

    def test_stale_missing_budget_vs_no_plan_and_eta_tie_determinism(self):
        yard, policy = self._c2_c1_yard()
        table = build_timing_table(default_synthetic_records(end_to_end_complete=True),
                                   expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)

        stale = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW + timedelta(hours=1), schedule={},
                                        group=2, timing_table=table, end_to_end_proven=True)
        self.assertEqual(stale.fallback_reason, 'STALE_OBSERVATION')

        empty = build_timing_table([], expected_config_version=DEFAULT_CONFIG)
        missing = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                          group=2, timing_table=empty, end_to_end_proven=True)
        self.assertEqual(missing.fallback_reason, 'MISSING_TIMING_COVERAGE')
        self.assertIsNone(missing.time_candidate)

        with patch('harbor.spatial.SPATIAL_SEARCH_LIMIT', 1):
            budget = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                             group=2, timing_table=table, end_to_end_proven=True)
        self.assertEqual(budget.fallback_reason, 'INCOMPLETE_SPATIAL_SEARCH')
        self.assertTrue(any(c.budget_exhausted for c in budget.candidates))
        self.assertFalse(any(c.infeasible_no_plan for c in budget.candidates))

        no_reach = compare_retrieval_times(
            yard,
            replace(policy, allowed_moves=frozenset({('yard', 'temp')})),
            ('C1', 'C2'),
            now=NOW,
            schedule={},
            group=2,
            timing_table=table,
            end_to_end_proven=True,
        )
        self.assertEqual(no_reach.fallback_reason, 'NO_FEASIBLE_CANDIDATE')
        self.assertTrue(any(c.infeasible_no_plan for c in no_reach.candidates))
        self.assertFalse(any(c.budget_exhausted for c in no_reach.candidates))

        tie_yard = _snapshot([('C1', 1, 1), ('C2', 2, 1)])
        tie_recs = [
            _rec(1, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 10.0, e2e=True),
            _rec(2, 'A', 'Y21-L1', 'stand-R1C1-L1', 'outbound', 10.0, e2e=True),
        ]
        tie_table = build_timing_table(tie_recs, expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
        r1 = compare_retrieval_times(tie_yard, policy, ('C2', 'C1'), now=NOW, schedule={},
                                     group=1, timing_table=tie_table, end_to_end_proven=True)
        r2 = compare_retrieval_times(tie_yard, policy, ('C2', 'C1'), now=NOW, schedule={},
                                     group=1, timing_table=tie_table, end_to_end_proven=True)
        self.assertEqual(r1, r2)
        self.assertEqual(r1.time_candidate.scenario.order, ('C2', 'C1'))

    def test_mixed_domains_clears_etas_and_fails_closed(self):
        yard, policy = self._c2_c1_yard()
        r1 = _rec(1, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='fleet_world_automatic', e2e=True)
        r2 = TimingRecord(**{**_rec(2, 'A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', 40.0, src_type='fleet_world_automatic', e2e=True).__dict__,
                             'clock': 'owner_monotonic_ns'})
        mixed = build_timing_table([r1, r2], expected_config_version=DEFAULT_CONFIG)
        comp = compare_retrieval_times(yard, policy, ('C1', 'C2'), now=NOW, schedule={},
                                       group=2, timing_table=mixed, end_to_end_proven=True)
        self.assertEqual(comp.fallback_reason, 'MIXED_TIMING_DOMAINS')
        self.assertIsNone(comp.time_candidate)
        self.assertEqual(comp.effective_recommendation, comp.existing_recommendation)
        self.assertTrue(all(c.transfer_time_seconds is None and c.end_to_end_seconds is None for c in comp.candidates))

    def test_external_json_loader_and_cli_report_hardening(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            bad_str_bool = td_path / 'bad_bool.json'
            bad_str_bool.write_text(json.dumps([{
                **_rec(1, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='fleet_world_automatic').__dict__,
                'end_to_end_complete': 'false',
            }]), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_records(bad_str_bool)

            bad_null_src = td_path / 'bad_null.json'
            bad_null_src.write_text(json.dumps([{
                **_rec(1, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, src_type='fleet_world_automatic').__dict__,
                'observation_source': None,
            }]), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_records(bad_null_src)

            err_buf = StringIO()
            with redirect_stderr(err_buf):
                bad_rc = main(['--evidence', str(bad_null_src)])
            self.assertEqual(bad_rc, 1)
            self.assertIn('time_replay_report error:', err_buf.getvalue())

            for dup_json in (
                '[{"arm":"A","source_site":"Y11-L1","destination_site":"stand-R1C1-L1","transfer_kind":"outbound",'
                '"config_version":"v1-synthetic","observation_source":"fleet_world_automatic",'
                '"start_event":"PHYSICAL_TRANSFER_SUBMITTING","end_event":"PHYSICAL_TRANSFER_CONFIRMED",'
                '"clock":"fleet_utc_wall_ns","start_ns":100,"end_ns":200,"succeeded":false,"succeeded":true,"attempt_key":"d1"}]',
                '[{"arm":"A","source_site":"Y11-L1","destination_site":"stand-R1C1-L1","transfer_kind":"outbound",'
                '"config_version":"v1-synthetic","observation_source":"fleet_world_automatic",'
                '"start_event":"PHYSICAL_TRANSFER_SUBMITTING","end_event":"PHYSICAL_TRANSFER_CONFIRMED",'
                '"clock":"fleet_utc_wall_ns","start_ns":100,"end_ns":200,"succeeded":true,"succeeded":false,"attempt_key":"d2"}]',
            ):
                dup_file = td_path / 'dup_key.json'
                dup_file.write_text(dup_json, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_records(dup_file)

            valid_fleet = td_path / 'fleet with space.json'
            recs = [{
                **_rec(i, r.arm, r.source_site, r.destination_site, r.transfer_kind,
                       (r.end_ns - r.start_ns) / 1e9, src_type='fleet_world_automatic', e2e=True).__dict__,
            } for i, r in enumerate(default_synthetic_records())]
            valid_fleet.write_text(json.dumps(recs), encoding='utf-8')
            ext_report = build_report(evidence_path=valid_fleet, end_to_end_proven=True, repeats=1,
                                      argv=['--evidence', str(valid_fleet)])
            self.assertFalse(ext_report['synthetic'])
            self.assertFalse(ext_report['options']['end_to_end_proven'])
            self.assertEqual(ext_report['fixtures'][0]['fallback_reason'], 'END_TO_END_BOUNDARY_UNPROVEN')
            self.assertIsNone(ext_report['fixtures'][0]['time_candidate']['end_to_end_seconds'])
            self.assertEqual(ext_report['command_argv'][-1], str(valid_fleet))

            # ETA tie prefers existing move-count winner over higher-move order even when saved order puts 4-move first
            tie_yard = _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C4', 2, 1)])
            # Make C2->C1 (2 moves, 5+5=10s) and C1->C2 (4 moves, 2.5*4=10s) tie strictly at 10s
            tie_recs = [
                _rec(1, 'A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', 5.0, e2e=True),
                _rec(2, 'A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0, e2e=True),
                _rec(3, 'A', 'Y11-L2', 'temp-R1C1-L1', 'rehandle', 2.5, e2e=True),
                _rec(4, 'A', 'temp-R1C1-L1', 'Y11-L1', 'restore', 2.5, e2e=True),
            ]
            # 2 moves: 5+5=10s; 4 moves: 2.5+5+2.5+5=15s -> adjust Y11-L1 outbound to 5s, rehandle 2.5s, restore 2.5s: wait 4 moves uses Y11-L1->stand twice? No!
            # Y11-L2->temp (rehandle) + Y11-L1->stand (outbound) + temp->Y11-L1 (restore) + Y11-L1->stand (outbound) = 2*outbound(Y11-L1) + rehandle + restore!
            # So set Y11-L2->stand = 10s (total 2-move = 10+5=15s), and rehandle+restore = 2.5+2.5=5s (total 4-move = 5+5+2.5+2.5=15s)!
            tie_recs[0] = _rec(1, 'A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', 10.0, e2e=True)
            tie_table = build_timing_table(tie_recs, expected_config_version=DEFAULT_CONFIG, allow_synthetic=True)
            tie_comp = compare_retrieval_times(tie_yard, _policy(tie_yard.layout), ('C1', 'C2'), now=NOW,
                                               schedule={}, group=2, timing_table=tie_table, end_to_end_proven=True)
            self.assertEqual(tie_comp.existing_recommendation.scenario.order, ('C2', 'C1'))
            self.assertEqual(tie_comp.time_candidate.scenario.order, ('C2', 'C1'))
            self.assertEqual(tie_comp.time_candidate.transfer_time_seconds, 15.0)

            out_file = td_path / 'out.json'
            rc = main(['--allow-synthetic', '--repeats', '2', '--output', str(out_file)])
            self.assertEqual(rc, 0)
            loaded = json.loads(out_file.read_text(encoding='utf-8'))
            self.assertEqual(loaded['latency_ms']['repeats'], 2)
            self.assertIn('--allow-synthetic', loaded['command'])


if __name__ == '__main__':
    unittest.main()
