"""Read-only offline time-based comparison of Harbor retrieval orders."""
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Iterable, Mapping

from harbor.sequencing import EXACT_LIMIT, RetrievalScenario, evaluate_order, rank_retrieval_orders
from harbor.spatial import SpatialPolicy, SpatialSnapshot

PHASE_TO_KIND = {
    '재취급': 'rehandle',
    '반출': 'outbound',
    '복귀': 'restore',
    'rehandle': 'rehandle',
    'outbound': 'outbound',
    'restore': 'restore',
}
VALID_TRANSFER_KINDS = frozenset({'rehandle', 'outbound', 'restore'})
MAX_TIMESTAMP_NS = (1 << 63) - 1
APPROVED_FLEET_CLOCK = 'fleet_utc_wall_ns'
APPROVED_FLEET_EVENTS = ('PHYSICAL_TRANSFER_SUBMITTING', 'PHYSICAL_TRANSFER_CONFIRMED')
APPROVED_FLEET_SOURCE = 'fleet_world_automatic'


def _nonempty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def normalize_site_id(site_id: str, aliases: Mapping[str, str] | None = None) -> str:
    """Normalize a site ID cleanly with optional explicit versioned aliases."""
    cleaned = _nonempty_str(site_id)
    if cleaned is None:
        raise ValueError('Site ID must be a nonempty string')
    if aliases is not None and cleaned in aliases:
        mapped = _nonempty_str(aliases[cleaned])
        if mapped is None:
            raise ValueError(f'Alias mapped {cleaned!r} to an empty site ID')
        return mapped
    return cleaned


@dataclass(frozen=True)
class TimingRecord:
    arm: str
    source_site: str
    destination_site: str
    transfer_kind: str
    config_version: str
    observation_source: str
    start_event: str
    end_event: str
    clock: str
    start_ns: int
    end_ns: int
    succeeded: bool
    attempt_key: str
    is_retry: bool = False
    end_to_end_complete: bool = False


@dataclass(frozen=True)
class TimingStats:
    arm: str
    source_site: str
    destination_site: str
    transfer_kind: str
    count: int
    median_seconds: float
    min_seconds: float
    max_seconds: float
    p95_seconds: float
    median_half_ns: int


@dataclass(frozen=True)
class TimingTable:
    config_version: str
    stats: dict[tuple[str, str, str, str], TimingStats]
    accepted_count: int
    rejected_counts: dict[str, int]
    clocks: tuple[str, ...]
    event_pairs: tuple[tuple[str, str], ...]
    sources: tuple[str, ...]
    all_end_to_end_proven: bool

    def lookup(self, arm: str, source_site: str, destination_site: str, transfer_kind: str,
               *, aliases: Mapping[str, str] | None = None) -> TimingStats | None:
        key = (arm, normalize_site_id(source_site, aliases), normalize_site_id(destination_site, aliases), transfer_kind)
        return self.stats.get(key)


def _median_half_ns(sorted_ns: list[int]) -> int:
    mid = len(sorted_ns) // 2
    if len(sorted_ns) % 2 == 1:
        return sorted_ns[mid] * 2
    return sorted_ns[mid - 1] + sorted_ns[mid]


def _p95(sorted_ns: list[int]) -> float:
    idx = max(0, math.ceil(0.95 * len(sorted_ns)) - 1)
    return sorted_ns[idx] / 1_000_000_000


def _canonical_record_identity(
    rec: TimingRecord,
    *,
    site_aliases: Mapping[str, str] | None,
) -> tuple:
    src = _nonempty_str(rec.source_site)
    dst = _nonempty_str(rec.destination_site)
    if site_aliases is not None:
        if src is not None and src in site_aliases:
            src = _nonempty_str(site_aliases[src])
        if dst is not None and dst in site_aliases:
            dst = _nonempty_str(site_aliases[dst])
    return (
        _nonempty_str(rec.arm),
        src,
        dst,
        rec.transfer_kind,
        _nonempty_str(rec.config_version),
        _nonempty_str(rec.observation_source),
        _nonempty_str(rec.start_event),
        _nonempty_str(rec.end_event),
        _nonempty_str(rec.clock),
        (type(rec.start_ns).__name__, rec.start_ns),
        (type(rec.end_ns).__name__, rec.end_ns),
        (type(rec.succeeded).__name__, rec.succeeded),
        (type(rec.is_retry).__name__, rec.is_retry),
        (type(rec.end_to_end_complete).__name__, rec.end_to_end_complete),
    )


def _validate_record(
    rec: TimingRecord,
    *,
    cfg_expected: str,
    allow_synthetic: bool,
    site_aliases: Mapping[str, str] | None,
    allowed_sources: frozenset[str] | set[str] | None,
    require_fleet_contract: bool,
) -> tuple[str | None, tuple[str, str, str, str] | None, int | None, str | None, str | None, tuple[str, str] | None, bool]:
    if type(rec.succeeded) is not bool or type(rec.is_retry) is not bool or type(rec.end_to_end_complete) is not bool:
        return 'invalid_record_type', None, None, None, None, None, False
    if rec.succeeded is not True:
        return 'failed', None, None, None, None, None, False
    if rec.is_retry:
        return 'retry', None, None, None, None, None, False

    attempt = _nonempty_str(rec.attempt_key)
    arm = _nonempty_str(rec.arm)
    obs_src = _nonempty_str(rec.observation_source)
    start_ev = _nonempty_str(rec.start_event)
    end_ev = _nonempty_str(rec.end_event)
    clock = _nonempty_str(rec.clock)
    cfg = _nonempty_str(rec.config_version)
    if None in (attempt, arm, obs_src, start_ev, end_ev, clock, cfg):
        return 'missing_provenance', None, None, None, None, None, False
    if cfg != cfg_expected:
        return 'config_mismatch', None, None, None, None, None, False
    if obs_src == 'operator_attested':
        return 'operator_attested_excluded', None, None, None, None, None, False
    is_synth = obs_src.lower().startswith('synthetic')
    if is_synth and not allow_synthetic:
        return 'synthetic_disallowed', None, None, None, None, None, False
    if require_fleet_contract and not is_synth:
        if (obs_src != APPROVED_FLEET_SOURCE or clock != APPROVED_FLEET_CLOCK
                or (start_ev, end_ev) != APPROVED_FLEET_EVENTS):
            return 'unapproved_fleet_contract', None, None, None, None, None, False
    if allowed_sources is not None and obs_src not in allowed_sources:
        return 'disallowed_observation_source', None, None, None, None, None, False
    if rec.transfer_kind not in VALID_TRANSFER_KINDS:
        return 'invalid_kind', None, None, None, None, None, False
    if (type(rec.start_ns) is not int or type(rec.end_ns) is not int
            or not 0 <= rec.start_ns <= MAX_TIMESTAMP_NS
            or not 0 <= rec.end_ns <= MAX_TIMESTAMP_NS):
        return 'invalid_timestamp', None, None, None, None, None, False
    if rec.end_ns <= rec.start_ns:
        return 'clock_reversal', None, None, None, None, None, False
    try:
        src_norm = normalize_site_id(rec.source_site, site_aliases)
        dst_norm = normalize_site_id(rec.destination_site, site_aliases)
    except ValueError:
        return 'invalid_site', None, None, None, None, None, False
    e2e = bool(rec.end_to_end_complete) and is_synth
    return None, (arm, src_norm, dst_norm, rec.transfer_kind), rec.end_ns - rec.start_ns, clock, obs_src, (start_ev, end_ev), e2e


def build_timing_table(
    records: Iterable[TimingRecord],
    *,
    expected_config_version: str,
    allow_synthetic: bool = False,
    site_aliases: Mapping[str, str] | None = None,
    allowed_sources: frozenset[str] | set[str] | None = None,
    require_fleet_contract: bool = False,
) -> TimingTable:
    """Validate, filter, and aggregate timing records into an immutable lookup table."""
    cfg_expected = _nonempty_str(expected_config_version)
    if cfg_expected is None:
        raise ValueError('expected_config_version must be a nonempty string')

    rejected: dict[str, int] = {}

    def reject(reason: str, count: int = 1) -> None:
        rejected[reason] = rejected.get(reason, 0) + count

    # First index every nonempty attempt_key so conflicting states (e.g., failed + succeeded
    # or shifted start_ns/end_ns) on the same attempt_key never leave one row accepted.
    raw_list = list(records)
    by_attempt: dict[str, list[TimingRecord]] = {}
    no_attempt: list[TimingRecord] = []
    for rec in raw_list:
        attempt = _nonempty_str(rec.attempt_key)
        if attempt is None:
            no_attempt.append(rec)
        else:
            by_attempt.setdefault(attempt, []).append(rec)

    for _ in no_attempt:
        reject('missing_provenance')

    accepted_rows: list[tuple[tuple[str, str, str, str], int, str, str, tuple[str, str], bool]] = []
    for attempt, group in by_attempt.items():
        if len(group) > 1:
            identities = {_canonical_record_identity(r, site_aliases=site_aliases) for r in group}
            if len(identities) > 1:
                reject('conflicting_attempt', len(group))
                continue
            reject('duplicate_attempt', len(group) - 1)
        rec = group[0]
        err, key, dur_ns, clock, obs_src, pair, e2e = _validate_record(
            rec,
            cfg_expected=cfg_expected,
            allow_synthetic=allow_synthetic,
            site_aliases=site_aliases,
            allowed_sources=allowed_sources,
            require_fleet_contract=require_fleet_contract,
        )
        if err is not None:
            reject(err)
            continue
        accepted_rows.append((key, dur_ns, clock, obs_src, pair, e2e))

    grouped_ns: dict[tuple[str, str, str, str], list[int]] = {}
    clocks: set[str] = set()
    event_pairs: set[tuple[str, str]] = set()
    sources: set[str] = set()
    e2e_flags: list[bool] = []

    for key, dur_ns, clock, obs_src, pair, e2e in accepted_rows:
        grouped_ns.setdefault(key, []).append(dur_ns)
        clocks.add(clock)
        event_pairs.add(pair)
        sources.add(obs_src)
        e2e_flags.append(e2e)

    stats: dict[tuple[str, str, str, str], TimingStats] = {}
    for key, durations in sorted(grouped_ns.items()):
        ordered = sorted(durations)
        med_half = _median_half_ns(ordered)
        stats[key] = TimingStats(
            arm=key[0],
            source_site=key[1],
            destination_site=key[2],
            transfer_kind=key[3],
            count=len(ordered),
            median_seconds=med_half / 2_000_000_000,
            min_seconds=ordered[0] / 1_000_000_000,
            max_seconds=ordered[-1] / 1_000_000_000,
            p95_seconds=_p95(ordered),
            median_half_ns=med_half,
        )

    return TimingTable(
        config_version=cfg_expected,
        stats=stats,
        accepted_count=len(accepted_rows),
        rejected_counts=rejected,
        clocks=tuple(sorted(clocks)),
        event_pairs=tuple(sorted(event_pairs)),
        sources=tuple(sorted(sources)),
        all_end_to_end_proven=bool(e2e_flags) and all(e2e_flags),
    )


@dataclass(frozen=True)
class CandidateTimeResult:
    scenario: RetrievalScenario
    budget_exhausted: bool
    infeasible_no_plan: bool
    hold_reasons: tuple[str, ...]
    budget_reasons: tuple[str, ...]
    move_count_traced: int
    missing_keys: tuple[tuple[str, str, str, str], ...]
    transfer_time_seconds: float | None
    end_to_end_seconds: float | None
    transfer_half_ns: int | None = None


@dataclass(frozen=True)
class ReplayComparison:
    observed_codes: tuple[str, ...]
    missing_from_yard: tuple[str, ...]
    candidate_count: int
    exhaustive: bool
    candidates: tuple[CandidateTimeResult, ...]
    saved_order: CandidateTimeResult | None
    existing_recommendation: CandidateTimeResult | None
    time_candidate: CandidateTimeResult | None
    effective_recommendation: CandidateTimeResult | None
    counterfactual_only: bool
    fallback_reason: str | None
    data_version: str
    coverage_ratio: float | None


def _evaluate_candidate(
    scenario: RetrievalScenario,
    timing_table: TimingTable,
    *,
    site_aliases: Mapping[str, str] | None,
    end_to_end_proven: bool,
    domains_valid: bool,
) -> tuple[CandidateTimeResult, int, int]:
    budget_exhausted = any(
        s.search_complete is False
        or s.budget_reason is not None
        or s.reason == 'SPATIAL_SEARCH_LIMIT_REACHED'
        for s in scenario.steps
    )
    infeasible_no_plan = any(
        s.status != 'READY' and s.reason == 'NO_LEGAL_SPATIAL_PLAN'
        for s in scenario.steps
    )
    hold_reasons = tuple(f'{s.cargo_id}:{s.reason}' for s in scenario.steps if s.status != 'READY')
    budget_reasons = tuple(
        f'{s.cargo_id}:{s.budget_reason or s.reason}'
        for s in scenario.steps
        if s.search_complete is False
        or s.budget_reason is not None
        or s.reason == 'SPATIAL_SEARCH_LIMIT_REACHED'
    )

    traced = 0
    covered = 0
    total_half_ns = 0
    missing: set[tuple[str, str, str, str]] = set()
    for step in scenario.steps:
        for move in step.moves:
            traced += 1
            if move.phase_label not in PHASE_TO_KIND:
                raise ValueError(f'Unsupported phase label for timing lookup: {move.phase_label!r}')
            kind = PHASE_TO_KIND[move.phase_label]
            key = (
                move.primary_arm,
                normalize_site_id(move.source, site_aliases),
                normalize_site_id(move.destination, site_aliases),
                kind,
            )
            entry = timing_table.stats.get(key)
            if entry is None:
                missing.add(key)
            else:
                covered += 1
                total_half_ns += entry.median_half_ns

    valid_eta = domains_valid and scenario.feasible and not missing and not budget_exhausted
    half_ns = total_half_ns if valid_eta else None
    transfer_time = (half_ns / 2_000_000_000) if half_ns is not None else None
    e2e_time = (
        transfer_time
        if (transfer_time is not None and end_to_end_proven and timing_table.all_end_to_end_proven)
        else None
    )
    candidate = CandidateTimeResult(
        scenario=scenario,
        budget_exhausted=budget_exhausted,
        infeasible_no_plan=infeasible_no_plan,
        hold_reasons=hold_reasons,
        budget_reasons=budget_reasons,
        move_count_traced=traced,
        missing_keys=tuple(sorted(missing)),
        transfer_time_seconds=transfer_time,
        end_to_end_seconds=e2e_time,
        transfer_half_ns=half_ns,
    )
    return candidate, traced, covered


def compare_retrieval_times(
    snapshot: SpatialSnapshot,
    policy: SpatialPolicy,
    codes: tuple[str, ...] | list[str],
    *,
    now: datetime,
    schedule: dict,
    group: int,
    timing_table: TimingTable,
    reorderable: bool = True,
    end_to_end_proven: bool = False,
    site_aliases: Mapping[str, str] | None = None,
    exact_limit: int = EXACT_LIMIT,
) -> ReplayComparison:
    """Compare saved, move-count, and time-based orders using a single planner expansion."""
    if type(exact_limit) is not int or not 1 <= exact_limit <= EXACT_LIMIT:
        raise ValueError(f'exact_limit must be between 1 and {EXACT_LIMIT}')
    codes_tuple = tuple(codes)
    if len(set(codes_tuple)) != len(codes_tuple):
        raise ValueError('Retrieval order must not contain duplicate cargo')

    placed = {p.cargo_id for p in snapshot.placements}
    observed = tuple(c for c in codes_tuple if c in placed)
    missing_from_yard = tuple(c for c in codes_tuple if c not in placed)

    domains_valid = (
        len(timing_table.clocks) <= 1
        and len(timing_table.event_pairs) <= 1
        and len(timing_table.sources) <= 1
    )

    if len(observed) < 2:
        if not observed:
            return ReplayComparison(
                observed_codes=observed,
                missing_from_yard=missing_from_yard,
                candidate_count=0,
                exhaustive=False,
                candidates=(),
                saved_order=None,
                existing_recommendation=None,
                time_candidate=None,
                effective_recommendation=None,
                counterfactual_only=False,
                fallback_reason='TIER_TOO_SMALL',
                data_version=timing_table.config_version,
                coverage_ratio=None,
            )
        single = evaluate_order(snapshot, policy, observed, now=now, schedule=schedule, group=group)
        cand, _, _ = _evaluate_candidate(
            single,
            timing_table,
            site_aliases=site_aliases,
            end_to_end_proven=end_to_end_proven,
            domains_valid=False,
        )
        return ReplayComparison(
            observed_codes=observed,
            missing_from_yard=missing_from_yard,
            candidate_count=1,
            exhaustive=False,
            candidates=(cand,),
            saved_order=cand,
            existing_recommendation=cand,
            time_candidate=None,
            effective_recommendation=cand,
            counterfactual_only=False,
            fallback_reason='TIER_TOO_SMALL',
            data_version=timing_table.config_version,
            coverage_ratio=None,
        )

    if len(observed) > exact_limit:
        ranked = rank_retrieval_orders(
            snapshot, policy, observed, now=now, schedule=schedule, group=group, keep=2, exact_limit=exact_limit
        )
        saved_sc = next((s for s in ranked if s.order == observed), None)
        if saved_sc is None:
            saved_sc = evaluate_order(snapshot, policy, observed, now=now, schedule=schedule, group=group)
        cands = tuple(
            _evaluate_candidate(
                s,
                timing_table,
                site_aliases=site_aliases,
                end_to_end_proven=end_to_end_proven,
                domains_valid=False,
            )[0]
            for s in ranked
        )
        saved_cand = next((c for c in cands if c.scenario.order == observed), None)
        if saved_cand is None:
            saved_cand = _evaluate_candidate(
                saved_sc,
                timing_table,
                site_aliases=site_aliases,
                end_to_end_proven=end_to_end_proven,
                domains_valid=False,
            )[0]
        existing = cands[0]
        return ReplayComparison(
            observed_codes=observed,
            missing_from_yard=missing_from_yard,
            candidate_count=len(cands),
            exhaustive=False,
            candidates=cands,
            saved_order=saved_cand,
            existing_recommendation=existing,
            time_candidate=None,
            effective_recommendation=existing,
            counterfactual_only=False,
            fallback_reason='TIER_EXCEEDS_EXACT_LIMIT',
            data_version=timing_table.config_version,
            coverage_ratio=None,
        )

    scenarios = rank_retrieval_orders(
        snapshot,
        policy,
        observed,
        now=now,
        schedule=schedule,
        group=group,
        keep=math.factorial(len(observed)),
        exact_limit=exact_limit,
    )

    candidates_list: list[CandidateTimeResult] = []
    feasible_traced = 0
    feasible_covered = 0
    for sc in scenarios:
        cand, traced, covered = _evaluate_candidate(
            sc,
            timing_table,
            site_aliases=site_aliases,
            end_to_end_proven=end_to_end_proven,
            domains_valid=domains_valid,
        )
        candidates_list.append(cand)
        if sc.feasible:
            feasible_traced += traced
            feasible_covered += covered

    candidates = tuple(candidates_list)
    coverage_ratio = (feasible_covered / feasible_traced) if feasible_traced else None
    saved_order = next((c for c in candidates if c.scenario.order == observed), None)
    existing = candidates[0] if candidates else None
    feasible_cands = [c for c in candidates if c.scenario.feasible]

    any_budget = any(c.budget_exhausted for c in candidates)
    time_candidate: CandidateTimeResult | None = None
    counterfactual_only = False

    if any_budget:
        fallback_reason: str | None = 'INCOMPLETE_SPATIAL_SEARCH'
        effective = existing
    elif not feasible_cands:
        stale = any(
            s.reason == 'OBSERVATION_NOT_CURRENT'
            for c in candidates
            for s in c.scenario.steps
        )
        fallback_reason = 'STALE_OBSERVATION' if stale else 'NO_FEASIBLE_CANDIDATE'
        effective = existing
    elif not domains_valid:
        fallback_reason = 'MIXED_TIMING_DOMAINS'
        effective = existing
    elif not timing_table.stats or any(c.missing_keys for c in feasible_cands):
        fallback_reason = 'MISSING_TIMING_COVERAGE'
        effective = existing
    else:
        # `candidates` preserves Harbor's exact cost order (total_moves, rehandles, given_order_index).
        order_index = {c.scenario.order: idx for idx, c in enumerate(candidates)}
        time_candidate = min(
            feasible_cands,
            key=lambda c: (c.transfer_half_ns, order_index[c.scenario.order]),
        )
        if not reorderable:
            counterfactual_only = True
            fallback_reason = 'FIXED_OR_APPROVED_ORDER'
            effective = existing
        elif not (end_to_end_proven and timing_table.all_end_to_end_proven):
            fallback_reason = 'END_TO_END_BOUNDARY_UNPROVEN'
            effective = existing
        else:
            fallback_reason = None
            effective = time_candidate

    return ReplayComparison(
        observed_codes=observed,
        missing_from_yard=missing_from_yard,
        candidate_count=len(candidates),
        exhaustive=all(c.scenario.exhaustive for c in candidates),
        candidates=candidates,
        saved_order=saved_order,
        existing_recommendation=existing,
        time_candidate=time_candidate,
        effective_recommendation=effective,
        counterfactual_only=counterfactual_only,
        fallback_reason=fallback_reason,
        data_version=timing_table.config_version,
        coverage_ratio=coverage_ratio,
    )
