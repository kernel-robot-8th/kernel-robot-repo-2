"""위 카메라 프레임 lease·조회 클라이언트 (topview-research.md 근거).

RosyClient._http는 JSON 전용이므로 바꾸지 않는다. 여기서는 같은
관례(Bearer, ca_file, timeout, _NoRedirect, 주입 가능한 transport)를
따르되, JPEG 바이너리 응답을 직접 읽는다.

확인 못 함:
  - R(통합관제)이 CELL-A 카메라를 vision sources에 내놓는지
  - Harbor 서비스 계정이 require_camera_viewer 권한을 통과하는지
  - 그림을 Fleet가 아닌 다른 호스트가 주는지 (frame_path가 다른 origin일
    수 있음 → 같은 origin일 때만 따라가고 아니면 거절)
"""
import http.client
import json
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request

from harbor.rosy_http import RosyClient


_MAX_FRAME_BYTES = 4 * 1024 * 1024
_MAX_JSON_BYTES = 1024 * 1024
FRAME_HEADERS = (
    'X-Frame-Seq', 'X-Frame-Age-Ms', 'X-Frame-Captured-At',
    'X-Frame-Width', 'X-Frame-Height', 'X-Frame-Rotation-Deg', 'X-Frame-Rectified',
)


class StaleFrameError(Exception):
    """프레임이 오래됨 (404 + X-Frame-State: stale)."""


class VisionClient(RosyClient):
    """transport(method, url, headers, data=None)는 (status, headers, bytes)를 반환한다."""

    def __init__(self, base_url=None, token=None, *, timeout=10, transport=None, ca_file=None):
        super().__init__(base_url, token, timeout=timeout, transport=transport, ca_file=ca_file)
        self._transport = transport
        if transport is not None:
            self._origin = base_url.rstrip('/') if base_url else ''
            self._token = token or ''

    def _open(self, method, url, headers, *, max_bytes, data=None):
        if self._transport is not None:
            return self._transport(method, url, headers, data)
        request = Request(url, data=data, method=method, headers=headers)
        opener = self._opener
        if self._discover:
            from harbor.fleet_discovery import discovery_opener
            opener = discovery_opener(self._origin, self._ca_file, self._timeout)
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return response.status, dict(response.headers.items()), response.read(max_bytes + 1)
        except http.client.HTTPException as error:
            raise ValueError('Invalid Fleet HTTP response') from error

    def _json_call(self, method, path, body):
        if self._transport is None:
            return super()._http(method, path, body)
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        status, headers, raw = self._open(method, self._origin + path, {
            'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json',
        }, max_bytes=_MAX_JSON_BYTES, data=data)
        if status != 200 or len(raw) > _MAX_JSON_BYTES:
            raise ValueError('Invalid Fleet JSON response')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Expected a Fleet response object')
        return value

    def sources(self):
        return self._json_call('GET', '/api/fleet/vision/sources', None)

    def lease(self, source_id, *, rectification=None, measured=None):
        if not isinstance(source_id, str) or not source_id:
            raise ValueError('Expected a camera source_id')
        body = {'source_id': source_id}
        if rectification is not None:
            body['rectification'] = rectification
        if measured is not None:
            body['measured'] = measured
        result = self._json_call('POST', '/api/fleet/vision/lease', body)
        if result.get('source_id') != source_id:
            raise ValueError('Vision lease source_id mismatch')
        self._frame_url(result)
        return result

    def _frame_url(self, lease_response):
        path, token = lease_response.get('frame_path'), lease_response.get('lease')
        source_id = lease_response.get('source_id')
        if (not isinstance(source_id, str) or not source_id
                or not isinstance(path, str) or not path or any(c.isspace() or ord(c) < 32 for c in path)
                or not isinstance(token, str) or not token or any(ord(c) < 32 or ord(c) == 127 for c in token)):
            raise ValueError('Invalid Vision lease response')
        parsed = urlsplit(path)
        if parsed.path != '/api/vision/sources/' + quote(source_id, safe='') + '/frame' or '?' in path:
            raise ValueError('Invalid Vision frame_path')
        origin = urlsplit(self._origin)
        target = urlsplit(urljoin(self._origin + '/', path))
        # 확인 못 함: Vision이 별도 호스트일 수 있으므로 같은 origin만 허용한다.
        if (target.port == 0 or parsed.fragment or parsed.username is not None or parsed.password is not None
                or not parsed.scheme and (not path.startswith('/') or path.startswith('//'))
                or (target.scheme, target.hostname, target.port or (443 if target.scheme == 'https' else 80))
                != (origin.scheme, origin.hostname, origin.port or (443 if origin.scheme == 'https' else 80))):
            raise ValueError('cross-origin or invalid frame_path rejected (확인 못 함)')
        return target.geturl()

    def fetch_frame(self, lease_response):
        url = self._frame_url(lease_response)
        try:
            status, headers, raw = self._open('GET', url, {
                'Authorization': 'Bearer ' + lease_response['lease'],
            }, max_bytes=_MAX_FRAME_BYTES)
        except HTTPError as error:
            stale = error.code == 404 and error.headers.get('X-Frame-State') == 'stale'
            error.close()
            if stale:
                raise StaleFrameError('frame stale') from error
            raise
        headers = {key.lower(): value for key, value in headers.items()}
        if status == 404 and headers.get('x-frame-state') == 'stale':
            raise StaleFrameError('frame stale')
        if status != 200:
            raise ValueError('Unexpected Vision frame status')
        if headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'image/jpeg':
            raise ValueError('Expected image/jpeg')
        if len(raw) > _MAX_FRAME_BYTES:
            raise ValueError('Frame exceeds 4 MiB')
        return raw, {key: headers[key.lower()] for key in FRAME_HEADERS if key.lower() in headers}
