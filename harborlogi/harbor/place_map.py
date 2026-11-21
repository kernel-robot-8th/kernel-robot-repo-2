"""HARBOR_BINDINGS_PATH 형식의 설치 바인딩에서 자리 주소 → target_ref를 읽는다.

일반 이송은 destination, 공간 배치는 anchor_address를 자리 주소로 쓴다.
기본 대응표와 시뮬레이션 템플릿에서 추정한 이름은 제공하지 않는다.
"""
import json
import re

from harbor.process import ArmTransferBinding, SpatialPlacementBinding, load_bindings

_SLOT_RE = re.compile(r'[^\s]+-r[1-9][0-9]*-c[1-9][0-9]*-l[1-9][0-9]*')


def _place_map(bindings_path):
    try:
        with open(bindings_path, encoding='utf-8') as source:
            items = json.load(source)
        if not isinstance(items, list):
            raise ValueError('바인딩 파일은 JSON 배열이어야 합니다')
        bindings = load_bindings(items)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f'바인딩 파일을 읽거나 검증할 수 없습니다: {bindings_path}') from exc
    places = {}
    for binding in bindings:
        if isinstance(binding, SpatialPlacementBinding):
            slot = binding.anchor()
        elif isinstance(binding, ArmTransferBinding):
            slot = binding.destination
        else:
            continue
        if not _SLOT_RE.fullmatch(slot):
            continue
        if slot in places and places[slot] != binding.target_ref:
            raise ValueError(f'자리 주소의 target_ref 대응이 충돌합니다: {slot}')
        places[slot] = binding.target_ref
    return places


def check_slot(slot_ref: str, bindings_path) -> str:
    """자리 주소에 명시적으로 설치된 target_ref를 반환한다. 없거나 잘못되면 오류다."""
    if not isinstance(slot_ref, str) or not _SLOT_RE.fullmatch(slot_ref):
        raise ValueError(f'자리 주소 문법이 올바르지 않습니다: {slot_ref!r}')
    places = _place_map(bindings_path)
    if slot_ref not in places:
        raise ValueError(f'자리 주소의 target_ref 대응이 없습니다: {slot_ref}')
    return places[slot_ref]


def all_slots(bindings_path) -> tuple[str, ...]:
    """명시적으로 설치된 자리 주소만 정렬해 반환한다."""
    return tuple(sorted(_place_map(bindings_path)))
