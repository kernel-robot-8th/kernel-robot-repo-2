"""Present the score saved with a plan; never search or infer physical performance."""
from copy import deepcopy


SCORE_LABELS = (
    ('different_area_count', '복귀 구역 변경', '개체'),
    ('different_position_count', '복귀 행·열 변경', '개체'),
    ('same_area_manhattan_distance', '같은 구역 주소 차이 합', '칸'),
    ('final_level_sum', '복귀 층 합계', '층'),
)


def _valid_score(score):
    return isinstance(score, dict) and all(type(score.get(key)) is int and score[key] >= 0
                                         for key, _, _ in SCORE_LABELS)


def explain_optimization(saved):
    if not isinstance(saved, dict) or saved.get('objective_version') != 'restoration_lex_v1':
        return None
    report = deepcopy(saved)
    score, first = report.get('score'), report.get('first_feasible_score')
    if score is not None and not _valid_score(score):
        return None
    report['score_rows'] = [dict(label=label, value=score[key], unit=unit,
        first=first.get(key) if isinstance(first, dict) else None) for key, label, unit in SCORE_LABELS] if score else []
    report['quality_label'] = ('정의한 작업군 비교 완료' if report.get('search_complete') is True else '탐색 한도 내 추천') if score else '완전한 추천안 없음'
    report['improved'] = bool(score and isinstance(first, dict) and all(
        type(first.get(key)) is int for key, _, _ in SCORE_LABELS) and
        tuple(score[key] for key, _, _ in SCORE_LABELS) < tuple(first[key] for key, _, _ in SCORE_LABELS))
    return report


def candidate_details(result, rank):
    """Read a bounded saved alternative, without replacing the authoritative plan."""
    if not isinstance(result, dict):
        return None
    report = explain_optimization(result.get('optimization'))
    alternatives = report.get('alternatives') if report else None
    if result.get('status') != 'READY' or not isinstance(alternatives, list) or not 1 <= rank <= min(5, len(alternatives)):
        return None
    first, candidate = alternatives[0], alternatives[rank - 1]
    if not isinstance(first, dict) or first.get('moves') != result.get('moves') or first.get('score') != report.get('score'):
        return None
    if not isinstance(candidate, dict) or not _valid_score(candidate.get('score')):
        return None
    moves = candidate.get('moves')
    if not isinstance(moves, list) or not moves or any(not isinstance(move, dict) for move in moves):
        return None
    return dict(candidate=candidate, candidate_count=min(5, len(alternatives)),
        quality_label=report['quality_label'], search_complete=report.get('search_complete') is True,
        score_rows=[dict(label=label, value=candidate['score'][key], recommended=report['score'][key], unit=unit)
                    for key, label, unit in SCORE_LABELS]) if _valid_score(report.get('score')) else None
