"""Present the score saved with a plan; never search or infer physical performance."""
from copy import deepcopy


SCORE_LABELS = (
    ('different_area_count', '복귀 구역 변경', '개체'),
    ('different_position_count', '복귀 행·열 변경', '개체'),
    ('same_area_manhattan_distance', '같은 구역 주소 차이 합', '칸'),
    ('final_level_sum', '복귀 층 합계', '층'),
)


def explain_optimization(saved):
    if not isinstance(saved, dict) or saved.get('objective_version') != 'restoration_lex_v1':
        return None
    report = deepcopy(saved)
    score, first = report.get('score'), report.get('first_feasible_score')
    if score is not None and (not isinstance(score, dict) or any(
            type(score.get(key)) is not int or score[key] < 0 for key, _, _ in SCORE_LABELS)):
        return None
    report['score_rows'] = [dict(label=label, value=score[key], unit=unit,
        first=first.get(key) if isinstance(first, dict) else None) for key, label, unit in SCORE_LABELS] if score else []
    report['quality_label'] = ('정의한 작업군 비교 완료' if report.get('search_complete') is True else '탐색 한도 내 추천') if score else '완전한 추천안 없음'
    report['improved'] = bool(score and isinstance(first, dict) and all(
        type(first.get(key)) is int for key, _, _ in SCORE_LABELS) and
        tuple(score[key] for key, _, _ in SCORE_LABELS) < tuple(first[key] for key, _, _ in SCORE_LABELS))
    return report
