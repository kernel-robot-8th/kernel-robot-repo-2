"""Read-only Harbor web helper for ROSY cell-site recognition with 15s in-process cache."""
from datetime import datetime
import hashlib
import os
import threading
import time

from django.utils import timezone

from harbor.rosy_http import SITE_RECOGNITION_PATH, load_site_recognition
from harbor.site_recognition import compare_with_yard
from harbor_web import fleet_connection
from harbor_web.models import Observation

_CACHE_TTL_S = 15.0
_CACHE_MAX_ENTRIES = 64
_REQUEST_TIMEOUT_S = 2
DEFAULT_TOTAL_SITES = 37

_cache_lock = threading.Lock()
_recognition_cache: dict[tuple[str, str, str], tuple[float, dict | None, str]] = {}


def clear_site_recognition_cache():
    with _cache_lock:
        _recognition_cache.clear()


fleet_connection.on_connection_saved(clear_site_recognition_cache)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _trim_cache():
    while len(_recognition_cache) > _CACHE_MAX_ENTRIES:
        oldest = min(_recognition_cache, key=lambda key: _recognition_cache[key][0])
        _recognition_cache.pop(oldest, None)


def _recognition_path() -> str:
    return os.environ.get('HARBOR_ROSY_SITE_RECOGNITION_PATH', SITE_RECOGNITION_PATH)


def _connection_target(request):
    if os.environ.get('HARBOR_ROSY_SITE_RECOGNITION', '').strip() == '0':
        return None, None, None, 'ROSY 인식 연동 전'
    # Same credential choice as the topview proxy (ADR 0037): the signed-in person's ROSY token, service token only for local accounts.
    from harbor_web.topview_proxy import _fleet_settings
    origin, token, ca_file = _fleet_settings(request)
    if not origin:
        return None, None, None, 'ROSY 인식 연동 전'
    if not token:
        return None, None, None, 'ROSY 로그인 필요'
    return origin, token, ca_file, ''


def fetch_site_recognition(request, *, known_site_refs=None):
    """Fetch ROSY cell-site recognition with the viewer's Fleet credential and a 15-second cache."""
    origin, token, ca_file, status = _connection_target(request)
    if status:
        return None, status
    key = (origin, _token_digest(token), _recognition_path())
    now_mono = time.monotonic()
    with _cache_lock:
        hit = _recognition_cache.get(key)
        if hit is not None and now_mono < hit[0]:
            return hit[1], hit[2]
    try:
        client = fleet_connection.fleet_client(
            dict(origin=origin, ca_file=ca_file),
            token,
            timeout=_REQUEST_TIMEOUT_S,
            verify_identity=False,
        )
    except ValueError:
        result = (None, 'ROSY 인식 연결 오류')
    else:
        result = load_site_recognition(client, known_site_refs=known_site_refs)
    with _cache_lock:
        _recognition_cache[key] = (time.monotonic() + _CACHE_TTL_S, result[0], result[1])
        _trim_cache()
    return result


def cached_site_recognition(request):
    """Return cached (snapshot, status) without making network calls or unnecessary DB queries."""
    if os.environ.get('HARBOR_ROSY_SITE_RECOGNITION', '').strip() == '0':
        return None, 'ROSY 인식 연동 전'
    with _cache_lock:
        if not _recognition_cache:
            return None, ''
    origin, token, _ca_file, status = _connection_target(request)
    if status:
        return None, status
    key = (origin, _token_digest(token), _recognition_path())
    now_mono = time.monotonic()
    with _cache_lock:
        hit = _recognition_cache.get(key)
        if hit is not None and now_mono < hit[0]:
            return hit[1], hit[2]
    return None, ''


def _total_sites(condition) -> int:
    payload = condition.payload if condition is not None and isinstance(condition.payload, dict) else None
    if not payload:
        return DEFAULT_TOTAL_SITES
    slot_cells = payload.get('slot_cells')
    yard = payload.get('yard_layout')
    if type(slot_cells) is not int or slot_cells < 1 or not isinstance(yard, dict):
        return DEFAULT_TOTAL_SITES
    rows = yard.get('rows')
    columns = yard.get('columns')
    levels = yard.get('levels')
    if not all(type(v) is int and v > 0 for v in (rows, columns, levels)):
        return DEFAULT_TOTAL_SITES
    total = rows * max(1, columns // slot_cells) * levels
    for area in payload.get('spatial_areas') or ():
        if not isinstance(area, dict):
            continue
        ar, ac, al = area.get('rows'), area.get('columns'), area.get('levels')
        if all(type(v) is int and v > 0 for v in (ar, ac, al)):
            total += ar * max(1, ac // slot_cells) * al
    return total if total > 0 else DEFAULT_TOTAL_SITES


def _ledger_observation(observation):
    if observation is None:
        return None
    if isinstance(observation.payload, dict) and observation.payload.get('schema') == 'grid-placement-v1':
        return observation
    return (
        Observation.objects.filter(payload__schema='grid-placement-v1')
        .select_related('condition')
        .order_by('-pk')
        .first()
    )


def _observation_placements(observation):
    raw_placements = observation.payload.get('placements') if isinstance(observation.payload, dict) else None
    if not isinstance(raw_placements, list):
        return None
    placements = []
    for item in raw_placements:
        if not isinstance(item, dict):
            continue
        area = item.get('area') or item.get('area_id')
        row = item.get('row')
        column = item.get('column')
        level = item.get('level')
        cargo_id = item.get('cargo_id')
        if (
            isinstance(area, str)
            and type(row) is int
            and type(column) is int
            and column in (1, 4)
            and type(level) is int
            and isinstance(cargo_id, str)
            and cargo_id
        ):
            placements.append(
                {
                    'area': area,
                    'row': row,
                    'column': column,
                    'level': level,
                    'cargo_id': cargo_id,
                }
            )
    return placements


def _mismatch_reason(row: dict) -> str:
    result = row['result']
    cargo = row['harbor_cargo_id']
    read = row['object_ref_read']
    if result == 'ROSY_EMPTY_HARBOR_OCCUPIED':
        return f'ROSY는 비어 있다고 봄 · 장부엔 {cargo}'
    if result == 'ROSY_OCCUPIED_HARBOR_EMPTY':
        if read:
            return f'ROSY는 {read} 있음으로 봄 · 장부는 비어 있음'
        return 'ROSY는 화물 있음으로 봄 · 장부는 비어 있음'
    if result == 'CLASS_MISMATCH':
        return f'ROSY는 {read}로 읽음 · 장부엔 {cargo}'
    if cargo:
        return f'ROSY가 읽지 못함 · 장부엔 {cargo}'
    return 'ROSY가 읽지 못함 · 장부는 비어 있음'


def _mismatch_cargo_label(row: dict) -> str:
    cargo = row['harbor_cargo_id']
    read = row['object_ref_read']
    if cargo and read and cargo != read:
        return f'{cargo} / {read}'
    return cargo or read or '미확인'


def _mismatch_title(row: dict) -> str:
    site_ref = row['site_ref']
    cargo = row['harbor_cargo_id'] or row['object_ref_read']
    return f'{site_ref} · {cargo}' if cargo else site_ref


def site_recognition_context(request, *, observation=None, condition=None, can_open_field=True, fetch=True):
    """Build read-only recognition display context for /inventory/ and /alerts/."""
    snapshot, status = fetch_site_recognition(request) if fetch else cached_site_recognition(request)
    field_href = '/field/' if can_open_field else None
    if snapshot is None:
        return dict(
            available=False,
            status=status,
            summary_line=status,
            observed_hm='',
            source_id='',
            total_sites=DEFAULT_TOTAL_SITES,
            recognized_count=0,
            compared=False,
            match_count=0,
            mismatch_count=0,
            mismatches=[],
            can_open_field=can_open_field,
            field_href=field_href,
        )

    dt = datetime.fromisoformat(snapshot['observed_at'].replace('Z', '+00:00'))
    observed_hm = timezone.localtime(dt).strftime('%H:%M')
    ledger_obs = _ledger_observation(observation)
    active_condition = condition or (
        ledger_obs.condition if ledger_obs is not None else (observation.condition if observation is not None else None)
    )
    total_sites = _total_sites(active_condition)
    recognized_count = len(snapshot['sites'])
    summary_line = f"ROSY 인식 · {observed_hm} · {snapshot['source_id']} · {total_sites}자리 중 {recognized_count}자리"

    compared = False
    match_count = 0
    mismatches = []
    if ledger_obs is not None:
        placements = _observation_placements(ledger_obs)
        if placements is not None:
            try:
                rows = compare_with_yard(snapshot, placements)
            except ValueError:
                rows = None
            if rows is not None:
                compared = True
                for row in rows:
                    if row['result'] == 'MATCH':
                        match_count += 1
                    else:
                        mismatches.append(
                            dict(
                                site_ref=row['site_ref'],
                                result=row['result'],
                                harbor_cargo_id=row['harbor_cargo_id'],
                                object_ref_read=row['object_ref_read'],
                                cargo_label=_mismatch_cargo_label(row),
                                reason=_mismatch_reason(row),
                                title=_mismatch_title(row),
                            )
                        )

    return dict(
        available=True,
        status='',
        summary_line=summary_line,
        observed_hm=observed_hm,
        source_id=snapshot['source_id'],
        total_sites=total_sites,
        recognized_count=recognized_count,
        compared=compared,
        match_count=match_count,
        mismatch_count=len(mismatches),
        mismatches=mismatches,
        can_open_field=can_open_field,
        field_href=field_href,
    )


def site_recognition_alert_items(request, *, observation=None, can_open_field=False, kind='인식 불일치'):
    """Return read-only alert rows for ROSY recognition mismatches."""
    ctx = site_recognition_context(request, observation=observation, can_open_field=can_open_field, fetch=True)
    if not ctx['available'] or not ctx['compared'] or not ctx['mismatches']:
        return []
    href = '/field/' if can_open_field else '/inventory/#site-recognition'
    action = '현장 확인' if can_open_field else '야드 보기'
    return [
        dict(
            kind=kind,
            title=item['title'],
            detail=item['reason'],
            count=1,
            href=href,
            action=action,
        )
        for item in ctx['mismatches']
    ]


def cached_site_recognition_mismatch_count(request, observation=None) -> int:
    """Return mismatch count from in-memory cache only (zero network calls, zero extra queries on cold cache)."""
    ctx = site_recognition_context(request, observation=observation, fetch=False)
    return ctx['mismatch_count'] if (ctx['available'] and ctx['compared']) else 0
