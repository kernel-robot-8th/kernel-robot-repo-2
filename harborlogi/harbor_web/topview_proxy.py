"""위 카메라 탑뷰 프록시 보기 (읽기 전용, GET, 로그인 필수).

브라우저에 lease 토큰을 절대 내보내지 않는다.  JPEG와 고른 X-Frame-*
헤더만 돌려준다.  Fleet 설정이 없으면 503과 「ROSY 연동 전」.

source_id는 GET /api/fleet/vision/sources의 sources에서 고른다 —
새 env 변수를 만들지 않는다.

자격(ADR 0037): 로그인한 사람의 Rosy 접근 토큰. Fleet은 같은 site resource의 계정 토큰을
Bearer로 받는다(rosy-platform site_auth.authorize → account_receiver). 로컬 계정이면
HARBOR_FLEET_TOKEN(서비스 신원)을 쓴다.
"""
import hashlib
import json
import math
import os
import re
import threading
import time
from urllib.error import HTTPError

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from harbor.rosy_vision import (
    FRAME_HEADERS,
    MAP_OVERLAY_RECTIFICATION,
    MAP_RECTIFICATION,
    PlaneUnavailableError,
    RectificationUnsupportedError,
    StaleFrameError,
    VisionClient,
)
from harbor.topview_site_pixels import (
    load_arm_s_site_pixels, load_sideview_site_pixels, load_topview_site_pixels,
)
from harbor_web.access import EVERYONE
from harbor_web.fleet_connection import connection_settings

from harbor_web.auth import require_roles
from harbor_web.rosy_account import access_token

# The live site lists the cell top camera as 'top' (D-1065 role name); older setups use 'omx_overhead'.
YARD_CAMERA_SOURCES = ('top', 'omx_overhead')
# The side camera watches containers being stacked ('side' is Rosy's role name, 'omx_side' the live source id).
SIDE_CAMERA_SOURCES = ('side', 'omx_side')
# Supply arm S camera (inbound: Pinky at S3 -> arm S -> belt-in); 'arm_s_top' is Rosy's role name.
ARM_S_CAMERA_SOURCES = ('arm_s_top', 'omx_arm_s_top')
# Listed first in this order; every other registered camera follows (#141: show all, find a live preview).
PREFERRED_CAMERA_SOURCES = YARD_CAMERA_SOURCES + SIDE_CAMERA_SOURCES + ARM_S_CAMERA_SOURCES + ('ceiling_north',)
FORWARDED_FRAME_HEADERS = tuple(dict.fromkeys((*FRAME_HEADERS, 'X-Frame-Plane')))
_FINITE_NUMBER_RE = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')


def _is_valid_frame_plane(value):
    """X-Frame-Plane must be exactly 5 comma-separated finite numbers (x_min,y_min,x_max,y_max,ppm)."""
    if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    parts = value.split(',')
    if len(parts) != 5:
        return False
    for raw_part in parts:
        token = raw_part.strip()
        if not _FINITE_NUMBER_RE.match(token):
            return False
        try:
            num = float(token)
        except ValueError:
            return False
        if not math.isfinite(num):
            return False
    return True

_CACHE_MAX_ENTRIES = 64
_SOURCES_TTL_S = 15.0
_LEASE_DEFAULT_TTL_S = 30.0
_LEASE_MARGIN_S = 5.0
_cache_lock = threading.Lock()
_sources_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
_lease_cache: dict[tuple[str, str, str, str], tuple[float, dict]] = {}
_unrectified_sources: set[tuple[str, str]] = set()
_overlay_unsupported: set[str] = set()


def clear_topview_cache():
    with _cache_lock:
        _sources_cache.clear()
        _lease_cache.clear()
        _unrectified_sources.clear()
        _overlay_unsupported.clear()


def _token_digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _trim_cache(cache):
    while len(cache) > _CACHE_MAX_ENTRIES:
        oldest = min(cache, key=lambda k: cache[k][0])
        cache.pop(oldest, None)


def _lease_deadline(lease_response, now_wall, now_mono):
    ttl = lease_response.get('ttl_seconds') or lease_response.get('ttl')
    if isinstance(ttl, (int, float)) and ttl > _LEASE_MARGIN_S:
        return now_mono + float(ttl) - _LEASE_MARGIN_S
    exp = lease_response.get('expires_at')
    if isinstance(exp, (int, float)) and exp - now_wall > _LEASE_MARGIN_S:
        return now_mono + float(exp - now_wall) - _LEASE_MARGIN_S
    return now_mono + _LEASE_DEFAULT_TTL_S


def _fleet_settings(request):
    """DB(또는 env)로 저장된 Fleet 연결과 자격. 자격은 로그인한 사람의 Rosy 접근 토큰(ADR 0037),
    없으면(로컬 계정) 기존 HARBOR_FLEET_TOKEN. 어느 쪽도 브라우저에 내보내지 않는다.
    허용 목록에 없는 출처는 연동 전과 같이 취급한다."""
    conn = connection_settings()
    origin = (conn['origin'] or '').rstrip('/') if conn['allowed'] else ''
    if settings.ROSY_ACCOUNT and request.session.get("rosy_account"):
        token = access_token(request) or ""
    else:
        token = access_token(request) or os.environ.get("HARBOR_FLEET_TOKEN", "")
    return origin, token, conn['ca_file'] or None


def _camera_sources(origin, token, ca_file, *, timeout):
    """화면과 프레임 프록시가 같은 Fleet Vision 목록·카메라 검증을 쓴다."""
    payload = VisionClient(origin, token, timeout=timeout, ca_file=ca_file).sources()
    sources = payload.get('sources')
    if not isinstance(sources, list):
        raise ValueError('Invalid vision sources')
    # 출처 없는 다른 카메라가 정상 카메라까지 막거나 URL 경로를 바꾸지 않게 한다.
    valid = list(dict.fromkeys(source for source in sources
        if isinstance(source, str) and source and '/' not in source and source not in ('.', '..')
        and not any(ord(char) < 32 or ord(char) == 127 for char in source)))
    preferred = [source for source in PREFERRED_CAMERA_SOURCES if source in valid]
    return preferred + [source for source in valid if source not in preferred]


def _overlay_for(sources, camera_ids, loader):
    """Overlay config rebound to whichever id of this camera group the site offers, or None."""
    camera = next((source for source in sources if source in camera_ids), None)
    pixels = loader() if camera else None
    if pixels is not None and pixels['source_id'] in camera_ids:
        pixels['source_id'] = camera
    return pixels


def _default_source_id(sources, active_order_kind=None):
    """Pick default camera for the running job (INBOUND -> arm S; otherwise yard)."""
    if not sources:
        return ''
    if active_order_kind == 'INBOUND':
        arm_s = next((source for source in sources if source in ARM_S_CAMERA_SOURCES), None)
        if arm_s is not None:
            return arm_s
    yard = next((source for source in sources if source in YARD_CAMERA_SOURCES), None)
    return yard if yard is not None else sources[0]


def topview_context(request, *, active_order_kind=None):
    """연결 실패는 탑뷰 칸 안에만 표시하고 야드 화면을 계속 연다."""
    origin, token, ca_file = _fleet_settings(request)
    if not origin:
        return dict(sources=[], status='ROSY 연동 전', default_source_id='', default_source_url='', default_view_mode='auto',
                    site_pixels=None, side_pixels=None, arm_s_pixels=None, pending_groups=[], site_pixels_json='', pending_points=[],
                    default_has_points=False, points_unverified=False)
    if not token:
        return dict(sources=[], status='ROSY 로그인 필요', default_source_id='', default_source_url='', default_view_mode='auto',
                    site_pixels=None, side_pixels=None, arm_s_pixels=None, pending_groups=[], site_pixels_json='', pending_points=[],
                    default_has_points=False, points_unverified=False)
    try:
        sources = _camera_sources(origin, token, ca_file, timeout=2)
        default_source_id = _default_source_id(sources, active_order_kind=active_order_kind)
        default_view_mode = (
            'raw'
            if default_source_id in YARD_CAMERA_SOURCES or default_source_id in ARM_S_CAMERA_SOURCES
            else 'auto'
        )
        # Draw each overlay on whichever camera id of its group the site offers.
        site_pixels = _overlay_for(sources, YARD_CAMERA_SOURCES, load_topview_site_pixels)
        side_pixels = _overlay_for(sources, SIDE_CAMERA_SOURCES, load_sideview_site_pixels)
        arm_s_pixels = _overlay_for(sources, ARM_S_CAMERA_SOURCES, load_arm_s_site_pixels)
        overlays = [doc for doc in (site_pixels, side_pixels, arm_s_pixels) if doc is not None]
        site_pixels_json = (
            json.dumps(overlays, ensure_ascii=False, separators=(',', ':')) if overlays else ''
        )
        def _pending(doc):
            return [pt for pt in doc.get('points', ())
                    if (pt.get('kind') == 'slot' and pt.get('roi') is None and pt.get('polygon') is None)
                    or (pt.get('kind') == 'pick' and pt.get('point') is None)]
        pending_points = _pending(site_pixels or {})
        pending_groups = [dict(source_id=doc['source_id'], points=_pending(doc)) for doc in overlays if _pending(doc)]
        source_items = [dict(source_id=source, url=reverse('topview_frame', args=[source])) for source in sources]
        default_source_url = next(
            (item['url'] for item in source_items if item['source_id'] == default_source_id),
            source_items[0]['url'] if source_items else '',
        )
        return dict(
            sources=source_items,
            status='' if sources else 'ROSY 연결 안 됨',
            default_source_id=default_source_id,
            # A running job chose this camera: the page must not hop to another camera when frames are missing.
            job_camera=bool(active_order_kind) and default_source_id != _default_source_id(sources),
            default_source_url=default_source_url,
            default_view_mode=default_view_mode,
            site_pixels=site_pixels,
            side_pixels=side_pixels,
            arm_s_pixels=arm_s_pixels,
            pending_groups=pending_groups,
            site_pixels_json=site_pixels_json,
            pending_points=pending_points,
            default_has_points=any(doc['source_id'] == default_source_id for doc in overlays),
            points_unverified=any(doc['status'] == 'planned_unverified' for doc in overlays),
        )
    except (ValueError, OSError):
        return dict(sources=[], status='ROSY 연결 안 됨', default_source_id='', default_source_url='', default_view_mode='auto',
                    site_pixels=None, side_pixels=None, arm_s_pixels=None, pending_groups=[], site_pixels_json='', pending_points=[],
                    default_has_points=False, points_unverified=False)


def _request_topview_lease(client, origin, source_id, *, view_mode):
    rect_key = (origin, source_id)
    if view_mode == 'raw':
        return client.lease(source_id)
    with _cache_lock:
        use_rect = rect_key not in _unrectified_sources
        use_overlay = origin not in _overlay_unsupported
    if view_mode != 'map' and use_rect and use_overlay:
        try:
            return client.lease(source_id, rectification=MAP_OVERLAY_RECTIFICATION)
        except RectificationUnsupportedError:
            with _cache_lock:
                _overlay_unsupported.add(origin)
        except PlaneUnavailableError:
            with _cache_lock:
                _unrectified_sources.add(rect_key)
            return client.lease(source_id)
    if use_rect:
        try:
            return client.lease(source_id, rectification=MAP_RECTIFICATION)
        except (PlaneUnavailableError, RectificationUnsupportedError):
            with _cache_lock:
                _unrectified_sources.add(rect_key)
    return client.lease(source_id)


@require_roles(*EVERYONE)
@require_http_methods(['GET'])
def topview_frame(request, source_id):
    """source_id 카메라의 최신 탑뷰 JPEG 한 장을 프록시한다."""
    origin, token, ca_file = _fleet_settings(request)
    if not origin or not token:
        return HttpResponse("ROSY 연동 전" if not origin else "ROSY 로그인 필요", status=503,
                            content_type="text/plain; charset=utf-8")

    raw_view = (request.GET.get('view') or 'auto').strip().lower()
    view_mode = 'raw' if raw_view == 'raw' else 'map' if raw_view == 'map' else 'auto'

    try:
        client = VisionClient(origin, token, timeout=10, ca_file=ca_file)
        lease_key = (origin, _token_digest(token), source_id, view_mode)
        rect_key = (origin, source_id)
        now_mono = time.monotonic()
        with _cache_lock:
            cached_lease = _lease_cache.get(lease_key)
            lease_response = cached_lease[1] if (cached_lease is not None and now_mono < cached_lease[0]) else None
        from_cache = lease_response is not None
        if lease_response is None and source_id not in _camera_sources(origin, token, ca_file, timeout=10):
            return HttpResponse('frame unavailable', status=404, content_type='text/plain; charset=utf-8')
        try:
            if lease_response is None:
                lease_response = _request_topview_lease(client, origin, source_id, view_mode=view_mode)
                with _cache_lock:
                    _lease_cache[lease_key] = (_lease_deadline(lease_response, time.time(), now_mono), lease_response)
                    _trim_cache(_lease_cache)
            jpeg, metadata = client.fetch_frame(lease_response)
        except HTTPError as error:
            if from_cache and error.code in (401, 403):
                with _cache_lock:
                    _lease_cache.pop(lease_key, None)
                if source_id not in _camera_sources(origin, token, ca_file, timeout=10):
                    return HttpResponse('frame unavailable', status=404, content_type='text/plain; charset=utf-8')
                lease_response = _request_topview_lease(client, origin, source_id, view_mode=view_mode)
                with _cache_lock:
                    _lease_cache[lease_key] = (_lease_deadline(lease_response, time.time(), time.monotonic()), lease_response)
                jpeg, metadata = client.fetch_frame(lease_response)
            else:
                raise
        except PlaneUnavailableError:
            with _cache_lock:
                _unrectified_sources.add(rect_key)
                _lease_cache.pop(lease_key, None)
            lease_response = client.lease(source_id)
            with _cache_lock:
                _lease_cache[lease_key] = (_lease_deadline(lease_response, time.time(), time.monotonic()), lease_response)
            jpeg, metadata = client.fetch_frame(lease_response)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        for key in FORWARDED_FRAME_HEADERS:
            value = metadata.get(key)
            if value is None:
                continue
            if key == 'X-Frame-Plane' and not _is_valid_frame_plane(value):
                continue
            response[key] = value
        return response
    except StaleFrameError:
        return HttpResponse("frame stale", status=404, content_type="text/plain; charset=utf-8")
    except HTTPError as error:
        if error.code == 404:
            return HttpResponse("frame unavailable", status=404, content_type="text/plain; charset=utf-8")
        return HttpResponse("frame unavailable", status=502, content_type="text/plain; charset=utf-8")
    except (ValueError, OSError):
        return HttpResponse("frame unavailable", status=502, content_type="text/plain; charset=utf-8")

