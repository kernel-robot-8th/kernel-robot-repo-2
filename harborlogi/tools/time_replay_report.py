"""Offline comparison report and p50/p95 latency benchmark for time-based outbound ordering."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

from harbor.layout import YardCell, YardLayout
from harbor.sequencing import rank_retrieval_orders
from harbor.spatial import Area, CargoShape, Placement, SpatialPolicy, SpatialSnapshot
from harbor.time_replay import MAX_TIMESTAMP_NS, TimingRecord, build_timing_table, compare_retrieval_times

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
DEFAULT_CONFIG = 'v1-synthetic'
REQUIRED_STR_FIELDS = (
    'arm', 'source_site', 'destination_site', 'transfer_kind',
    'config_version', 'observation_source', 'start_event', 'end_event',
    'clock', 'attempt_key',
)
REQUIRED_BOOL_FIELDS = ('succeeded',)
OPTIONAL_BOOL_FIELDS = ('is_retry', 'end_to_end_complete')
REQUIRED_INT_FIELDS = ('start_ns', 'end_ns')


def _layout(rows=3, cols=3, levels=3):
    return YardLayout(
        rows, cols, levels,
        tuple(YardCell(f'Y{r}{c}', r, c) for r in range(1, rows + 1) for c in range(1, cols + 1)),
    )


def _policy(layout, *, arms=('A', 'B'), age_min=10):
    places = ('temp', 'stand', *(c.bay_id for c in layout.cells))
    return SpatialPolicy(
        'stand',
        ('temp',),
        frozenset({('yard', 'yard'), ('yard', 'temp'), ('temp', 'yard'), ('yard', 'stand')}),
        frozenset((arm, place) for arm in arms for place in places),
        timedelta(minutes=age_min),
    )


def _snapshot(placements, *, layout=None):
    layout = layout or _layout()
    return SpatialSnapshot(
        1, NOW, layout,
        (Area('yard', layout.rows, layout.columns, layout.levels),
         Area('temp', layout.rows, layout.columns, layout.levels),
         Area('stand', 1, 3, 1)),
        tuple(CargoShape(code, 3, i + 1) for i, (code, _, _) in enumerate(placements)),
        tuple(Placement(code, 'yard', row, 1, level) for code, row, level in placements),
    )


def _rec(idx, arm, src, dst, kind, sec, *, cfg=DEFAULT_CONFIG, src_type='synthetic',
         succeeded=True, is_retry=False, e2e=False, start_ns=1_700_000_000_000_000_000):
    return TimingRecord(
        arm=arm,
        source_site=src,
        destination_site=dst,
        transfer_kind=kind,
        config_version=cfg,
        observation_source=src_type,
        start_event='PHYSICAL_TRANSFER_SUBMITTING',
        end_event='PHYSICAL_TRANSFER_CONFIRMED',
        clock='synthetic_ns' if src_type == 'synthetic' else 'fleet_utc_wall_ns',
        start_ns=start_ns,
        end_ns=start_ns + int(round(sec * 1_000_000_000)),
        succeeded=succeeded,
        attempt_key=f'synth-{idx}-{arm}-{src}-{dst}-{kind}',
        is_retry=is_retry,
        end_to_end_complete=e2e,
    )


def default_synthetic_records(*, end_to_end_complete=True, invert_c2_c1=False):
    """Cover every move edge across the built-in 1x3 fixtures (synthetic only)."""
    edges = [
        # (arm, src, dst, kind, seconds)
        ('A', 'Y11-L1', 'stand-R1C1-L1', 'outbound', 5.0),
        ('A', 'Y11-L2', 'stand-R1C1-L1', 'outbound', 40.0 if invert_c2_c1 else 5.0),
        ('A', 'Y11-L2', 'temp-R1C1-L1', 'rehandle', 5.0),
        ('A', 'temp-R1C1-L1', 'Y11-L1', 'restore', 5.0),
        ('A', 'Y11-L2', 'temp-R1C1-L2', 'rehandle', 6.0),
        ('A', 'temp-R1C1-L2', 'Y11-L1', 'restore', 6.0),
        ('A', 'Y21-L1', 'stand-R1C1-L1', 'outbound', 4.0),
        ('A', 'Y21-L2', 'stand-R1C1-L1', 'outbound', 12.0),
        ('A', 'Y21-L2', 'temp-R1C1-L1', 'rehandle', 4.0),
        ('A', 'temp-R1C1-L1', 'Y21-L1', 'restore', 4.0),
        ('A', 'Y21-L1', 'temp-R1C1-L1', 'rehandle', 4.0),
        ('A', 'Y31-L1', 'stand-R1C1-L1', 'outbound', 3.0),
    ]
    for r in (1, 2, 3):
        for l_src in (1, 2, 3):
            for l_tmp in (1, 2, 3):
                edges.append(('A', f'Y{r}1-L{l_src}', f'temp-R1C1-L{l_tmp}', 'rehandle', 4.0 + r * 0.1))
                edges.append(('A', f'temp-R1C1-L{l_tmp}', f'Y{r}1-L{l_src}', 'restore', 4.0 + r * 0.1))
            edges.append(('A', f'Y{r}1-L{l_src}', 'stand-R1C1-L1', 'outbound',
                          (40.0 if invert_c2_c1 else 5.0) if (r, l_src) == (1, 2) else 4.0 + r + l_src))
    seen = set()
    records = []
    for idx, (arm, src, dst, kind, sec) in enumerate(edges):
        key = (arm, src, dst, kind)
        if key in seen:
            continue
        seen.add(key)
        records.append(_rec(idx, arm, src, dst, kind, sec, e2e=end_to_end_complete))
    return records


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = max(0, math.ceil((pct / 100.0) * len(ordered)) - 1)
    return ordered[idx]


def _serialize_candidate(cand):
    if cand is None:
        return None
    return {
        'order': list(cand.scenario.order),
        'feasible': cand.scenario.feasible,
        'total_moves': cand.scenario.total_moves,
        'rehandles': cand.scenario.rehandles,
        'transfer_time_seconds': cand.transfer_time_seconds,
        'end_to_end_seconds': cand.end_to_end_seconds,
        'hold_reasons': list(cand.hold_reasons),
        'budget_reasons': list(cand.budget_reasons),
        'missing_keys': [list(k) for k in cand.missing_keys],
    }


def _serialize_comparison(name, comp):
    return {
        'fixture': name,
        'observed_codes': list(comp.observed_codes),
        'missing_from_yard': list(comp.missing_from_yard),
        'candidate_count': comp.candidate_count,
        'exhaustive': comp.exhaustive,
        'counterfactual_only': comp.counterfactual_only,
        'fallback_reason': comp.fallback_reason,
        'data_version': comp.data_version,
        'coverage_ratio': round(comp.coverage_ratio, 4) if comp.coverage_ratio is not None else None,
        'saved_order': _serialize_candidate(comp.saved_order),
        'existing_recommendation': _serialize_candidate(comp.existing_recommendation),
        'time_candidate': _serialize_candidate(comp.time_candidate),
        'effective_recommendation': _serialize_candidate(comp.effective_recommendation),
    }


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError(f'Duplicate JSON key {key!r} in TimingRecord object')
        obj[key] = val
    return obj


def load_records(path: Path) -> list[TimingRecord]:
    raw = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_no_duplicate_keys)
    if not isinstance(raw, list):
        raise ValueError('Evidence file must contain a JSON array of TimingRecord objects')
    allowed = set(REQUIRED_STR_FIELDS) | set(REQUIRED_BOOL_FIELDS) | set(OPTIONAL_BOOL_FIELDS) | set(REQUIRED_INT_FIELDS)
    records = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f'Invalid TimingRecord at index {idx}: expected JSON object')
        extra = set(item) - allowed
        if extra:
            raise ValueError(f'Invalid TimingRecord at index {idx}: unexpected fields {sorted(extra)}')
        for field in REQUIRED_STR_FIELDS:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f'Invalid TimingRecord at index {idx}: field {field!r} must be a nonempty string')
        for field in REQUIRED_INT_FIELDS:
            val = item.get(field)
            if type(val) is not int or not 0 <= val <= MAX_TIMESTAMP_NS:
                raise ValueError(f'Invalid TimingRecord at index {idx}: field {field!r} must be an int in [0, 2^63-1]')
        for field in REQUIRED_BOOL_FIELDS:
            if type(item.get(field)) is not bool:
                raise ValueError(f'Invalid TimingRecord at index {idx}: field {field!r} must be a JSON boolean')
        for field in OPTIONAL_BOOL_FIELDS:
            if field in item and type(item[field]) is not bool:
                raise ValueError(f'Invalid TimingRecord at index {idx}: field {field!r} must be a JSON boolean')
        records.append(TimingRecord(**item))
    return records


def build_report(
    *,
    evidence_path: Path | None = None,
    config_version: str = DEFAULT_CONFIG,
    allow_synthetic: bool = False,
    end_to_end_proven: bool = False,
    repeats: int = 5,
    argv: list[str] | None = None,
) -> dict:
    if type(repeats) is not int or repeats < 1:
        raise ValueError('repeats must be a positive integer')

    if evidence_path is not None:
        records = load_records(evidence_path)
        synthetic = False
        effective_e2e = False  # Real/external Fleet receipts never assert end-to-end completion in v1.
        allow_synth_table = False
        require_fleet = True
    else:
        effective_e2e = bool(allow_synthetic and end_to_end_proven)
        allow_synth_table = bool(allow_synthetic)
        require_fleet = False
        if not allow_synthetic:
            records = []
            synthetic = False
        else:
            records = default_synthetic_records(end_to_end_complete=effective_e2e)
            synthetic = True

    table = build_timing_table(
        records,
        expected_config_version=config_version,
        allow_synthetic=allow_synth_table,
        require_fleet_contract=require_fleet,
    )

    layout = _layout()
    policy = _policy(layout)

    c2_c1_yard = _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C4', 2, 1)], layout=layout)
    c2_c1_comp = compare_retrieval_times(
        c2_c1_yard, policy, ('C1', 'C2'), now=NOW, schedule={'C1': (2, 0), 'C2': (2, 1), 'C4': (3, 0)},
        group=2, timing_table=table, reorderable=True, end_to_end_proven=effective_e2e,
    )

    inversion_table = (
        build_timing_table(
            default_synthetic_records(end_to_end_complete=effective_e2e, invert_c2_c1=True),
            expected_config_version=config_version,
            allow_synthetic=True,
        )
        if evidence_path is None and allow_synthetic
        else table
    )
    inversion_comp = compare_retrieval_times(
        c2_c1_yard, policy, ('C1', 'C2'), now=NOW, schedule={'C1': (2, 0), 'C2': (2, 1), 'C4': (3, 0)},
        group=2, timing_table=inversion_table, reorderable=True, end_to_end_proven=effective_e2e,
    )

    four_yard = _snapshot([('C1', 1, 1), ('C2', 1, 2), ('C3', 2, 1), ('C4', 3, 1)], layout=layout)
    four_codes = ('C1', 'C2', 'C3', 'C4')
    four_sched = {c: (1, i) for i, c in enumerate(four_codes)}
    four_comp = compare_retrieval_times(
        four_yard, policy, four_codes, now=NOW, schedule=four_sched,
        group=1, timing_table=table, reorderable=True, end_to_end_proven=effective_e2e,
    )

    baseline_ms: list[float] = []
    replay_ms: list[float] = []
    for rep in range(repeats):
        order = ('baseline', 'replay') if rep % 2 == 0 else ('replay', 'baseline')
        for mode in order:
            t0 = perf_counter()
            if mode == 'baseline':
                rank_retrieval_orders(four_yard, policy, four_codes, now=NOW, schedule=four_sched, group=1)
                baseline_ms.append((perf_counter() - t0) * 1000.0)
            else:
                compare_retrieval_times(
                    four_yard, policy, four_codes, now=NOW, schedule=four_sched,
                    group=1, timing_table=table, reorderable=True, end_to_end_proven=effective_e2e,
                )
                replay_ms.append((perf_counter() - t0) * 1000.0)

    stale_comp = compare_retrieval_times(
        c2_c1_yard, policy, ('C1', 'C2'), now=NOW + timedelta(hours=1), schedule={},
        group=2, timing_table=table, reorderable=True, end_to_end_proven=effective_e2e,
    )
    empty_table = build_timing_table([], expected_config_version=config_version)
    missing_comp = compare_retrieval_times(
        c2_c1_yard, policy, ('C1', 'C2'), now=NOW, schedule={},
        group=2, timing_table=empty_table, reorderable=True, end_to_end_proven=effective_e2e,
    )
    fixed_comp = compare_retrieval_times(
        c2_c1_yard, policy, ('C1', 'C2'), now=NOW, schedule={},
        group=2, timing_table=table, reorderable=False, end_to_end_proven=effective_e2e,
    )
    with patch('harbor.spatial.SPATIAL_SEARCH_LIMIT', 1):
        budget_comp = compare_retrieval_times(
            c2_c1_yard, policy, ('C1', 'C2'), now=NOW, schedule={},
            group=2, timing_table=table, reorderable=True, end_to_end_proven=effective_e2e,
        )

    b_p50, b_p95 = median(baseline_ms), _percentile(baseline_ms, 95)
    r_p50, r_p95 = median(replay_ms), _percentile(replay_ms, 95)

    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
    except (OSError, subprocess.SubprocessError):
        commit = 'unknown'
        dirty = None

    argv_list = ['python', '-m', 'tools.time_replay_report', *(argv or [])]
    cmd_str = subprocess.list2cmdline(argv_list)

    return {
        'source_commit': commit,
        'working_tree_dirty': dirty,
        'command': cmd_str,
        'command_argv': argv_list,
        'options': {
            'evidence': str(evidence_path) if evidence_path is not None else None,
            'config_version': config_version,
            'allow_synthetic': allow_synthetic,
            'end_to_end_proven': effective_e2e,
            'repeats': repeats,
        },
        'synthetic': synthetic,
        'timing_scope': (
            'offline_planner_and_transfer_lookup_only; baseline runs in same worktree '
            'with additive RetrievalStep trace so delta measures timing lookup overhead'
        ),
        'data_version': table.config_version,
        'coverage': {
            'accepted_records': table.accepted_count,
            'rejected_counts': table.rejected_counts,
            'stats_keys': len(table.stats),
            'clocks': list(table.clocks),
            'event_pairs': [list(p) for p in table.event_pairs],
            'sources': list(table.sources),
            'all_end_to_end_proven': table.all_end_to_end_proven,
        },
        'fixtures': [
            _serialize_comparison('c2_c1_product', c2_c1_comp),
            _serialize_comparison('synthetic_inversion', inversion_comp),
            _serialize_comparison('four_cargo_exhaustive', four_comp),
            _serialize_comparison('fallback_stale_observation', stale_comp),
            _serialize_comparison('fallback_missing_timing', missing_comp),
            _serialize_comparison('fallback_fixed_counterfactual', fixed_comp),
            _serialize_comparison('fallback_budget_exhausted', budget_comp),
        ],
        'latency_ms': {
            'fixture': 'four_cargo_exhaustive',
            'repeats': repeats,
            'baseline_p50_ms': round(b_p50, 3),
            'baseline_p95_ms': round(b_p95, 3),
            'replay_p50_ms': round(r_p50, 3),
            'replay_p95_ms': round(r_p95, 3),
            'p95_delta_ms': round(r_p95 - b_p95, 3),
            'p95_delta_pct': round(((r_p95 - b_p95) / b_p95 * 100.0) if b_p95 else 0.0, 2),
        },
    }


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=None)
    parser.add_argument('--config-version', default=DEFAULT_CONFIG)
    parser.add_argument('--allow-synthetic', action='store_true')
    parser.add_argument('--end-to-end-proven', action='store_true')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args(raw_argv)

    try:
        report = build_report(
            evidence_path=args.evidence,
            config_version=args.config_version,
            allow_synthetic=args.allow_synthetic,
            end_to_end_proven=args.end_to_end_proven,
            repeats=args.repeats,
            argv=raw_argv,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + '\n', encoding='utf-8')
    except (ValueError, OSError) as exc:
        print(f'time_replay_report error: {exc}', file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
