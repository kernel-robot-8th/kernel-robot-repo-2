"""위 카메라 탑뷰 프록시 보기 (읽기 전용, GET, 로그인 필수).

브라우저에 lease 토큰을 절대 내보내지 않는다.  JPEG와 고른 X-Frame-*
헤더만 돌려준다.  Fleet 설정이 없으면 503과 「ROSY 연동 전」.

source_id는 GET /api/fleet/workcell-observations의 cameras[].source_id에서
고른다 — 새 env 변수를 만들지 않는다.

확인 못 함:
  - R이 CELL-A 카메라를 vision sources에 내놓는지
  - Harbor 서비스 계정이 require_camera_viewer를 통과하는지
  - 그림을 Fleet가 아닌 다른 호스트가 주는지
"""
import os

from django.http import HttpResponse
from django.views.decorators.http import require_http_methods

from harbor.rosy_http import RosyClient
from harbor.rosy_vision import FRAME_HEADERS, StaleFrameError, VisionClient
from harbor_web.access import READ

from harbor_web.auth import require_roles


def _fleet_settings():
    """기존 HARBOR_FLEET_ORIGIN, HARBOR_FLEET_TOKEN, HARBOR_FLEET_CA_FILE을 읽는다."""
    origin = os.environ.get("HARBOR_FLEET_ORIGIN", "").rstrip("/")
    token = os.environ.get("HARBOR_FLEET_TOKEN", "")
    ca_file = os.environ.get("HARBOR_FLEET_CA_FILE", "") or None
    return origin, token, ca_file


@require_roles(*READ)
@require_http_methods(['GET'])
def topview_frame(request, source_id):
    """source_id 카메라의 최신 탑뷰 JPEG 한 장을 프록시한다."""
    origin, token, ca_file = _fleet_settings()
    if not origin or not token:
        return HttpResponse("ROSY 연동 전", status=503, content_type="text/plain; charset=utf-8")

    try:
        snapshot = RosyClient(origin, token, timeout=10, ca_file=ca_file).workcell_observations()
        cells = snapshot.get('workcells')
        if not isinstance(cells, list) or any(
                not isinstance(cell, dict) or not isinstance(cell.get('cameras', []), list)
                or any(not isinstance(camera, dict) for camera in cell.get('cameras', [])) for cell in cells):
            raise ValueError('Invalid workcell cameras')
        if not any(camera.get('source_id') == source_id for cell in cells for camera in cell.get('cameras', [])):
            return HttpResponse('frame unavailable', status=404, content_type='text/plain; charset=utf-8')
        client = VisionClient(origin, token, timeout=10, ca_file=ca_file)
        lease_response = client.lease(source_id)
        jpeg, metadata = client.fetch_frame(lease_response)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        for key in FRAME_HEADERS:
            value = metadata.get(key)
            if value is not None:
                response[key] = value
        return response
    except StaleFrameError:
        return HttpResponse("frame stale", status=404, content_type="text/plain; charset=utf-8")
    except (ValueError, OSError):
        return HttpResponse("frame unavailable", status=502, content_type="text/plain; charset=utf-8")

