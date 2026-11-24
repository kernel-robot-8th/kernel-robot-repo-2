"""harbor/rosy_vision.py 단위 시험.

ThreadingHTTPServer 가짜 서버 패턴은 tests/test_http_execution.py:20,48을 따른다.
"""
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

from harbor.rosy_vision import (
    MAP_OVERLAY_RECTIFICATION,
    MAP_RECTIFICATION,
    PlaneUnavailableError,
    RectificationUnsupportedError,
    StaleFrameError,
    VisionClient,
)


# ── 가짜 Vision 서버 ──

_TINY_JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 100  # 최소 JPEG stub


class _VisionHandler(BaseHTTPRequestHandler):
    """source_id=test-cam 하나를 가진 가짜 Vision/Fleet 서버."""

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer fleet-token":
            self.send_error(403)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw) if raw else None
        self.server.calls.append(("POST", self.path, body))

        if self.path == "/api/fleet/vision/lease":
            source_id = body.get("source_id", "")
            # 허용: rectification, measured 외의 필드가 있으면 400
            allowed = {"source_id", "rectification", "measured"}
            if set(body.keys()) - allowed:
                self.send_error(400)
                return
            response = json.dumps({
                "source_id": source_id,
                "lease": "lease-token-" + source_id,
                "frame_path": f"/api/vision/sources/{source_id}/frame",
                "expires_in_s": 60,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        self.send_error(404)

    def do_GET(self):
        self.server.calls.append(("GET", self.path, None))

        if self.path == '/api/vision/sources/redirect-cam/frame':
            self.send_response(302)
            self.send_header('Location', '/must-not-follow')
            self.end_headers()
            return

        if self.path == "/api/fleet/vision/sources":
            if self.headers.get("Authorization") != "Bearer fleet-token":
                self.send_error(403)
                return
            response = json.dumps({"sources": [{"source_id": "test-cam"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return

        if self.path.startswith("/api/vision/sources/") and self.path.endswith("/frame"):
            bearer = self.headers.get("Authorization", "")

            # stale 시험용 source
            if "stale-cam" in self.path:
                self.send_response(404)
                self.send_header("X-Frame-State", "stale")
                self.end_headers()
                return

            # plane-unavailable 시험용 source
            if "uncalibrated-cam" in self.path:
                self.send_response(409)
                self.send_header("X-Frame-State", "plane-unavailable")
                self.end_headers()
                return

            # Bearer가 lease 토큰이어야 한다.
            source_id = self.path.split("/api/vision/sources/")[1].split("/frame")[0]
            if bearer != f"Bearer lease-token-{source_id}":
                self.send_error(403)
                return

            # not-jpeg 시험용 source
            if "text-cam" in self.path:
                body = b"this is not jpeg"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # too-big 시험용 source
            if "big-cam" in self.path:
                body = b'\xff\xd8\xff\xe0' + b'\x00' * (4 * 1024 * 1024 + 1)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(_TINY_JPEG)))
            self.send_header("X-Frame-Seq", "42")
            self.send_header("X-Frame-Age-Ms", "150")
            self.send_header("X-Frame-Captured-At", "2026-11-15T12:00:00Z")
            self.send_header("X-Frame-Width", "1920")
            self.send_header("X-Frame-Height", "1080")
            self.send_header("X-Frame-Rotation-Deg", "0")
            self.send_header("X-Frame-Rectified", "true")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(_TINY_JPEG)
            return

        self.send_error(404)


@contextmanager
def vision_server():
    http = ThreadingHTTPServer(("127.0.0.1", 0), _VisionHandler)
    http.calls = []
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", http
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


# ── 시험 ──

class VisionClientTests(unittest.TestCase):

    def test_lease_then_fetch_frame_success(self):
        """lease 성공 → frame 성공: Bearer로 lease 토큰을 보내고, X-Frame-* 헤더를 해석한다."""
        with vision_server() as (origin, http):
            client = VisionClient(origin, "fleet-token")
            lease = client.lease("test-cam")
            self.assertEqual(lease["source_id"], "test-cam")
            self.assertIn("lease", lease)
            self.assertIn("frame_path", lease)
            self.assertEqual(lease["expires_in_s"], 60)

            jpeg, metadata = client.fetch_frame(lease)
            self.assertEqual(jpeg, _TINY_JPEG)
            self.assertEqual(metadata["X-Frame-Seq"], "42")
            self.assertEqual(metadata["X-Frame-Age-Ms"], "150")
            self.assertEqual(metadata["X-Frame-Captured-At"], "2026-11-15T12:00:00Z")
            self.assertEqual(metadata["X-Frame-Width"], "1920")
            self.assertEqual(metadata["X-Frame-Height"], "1080")
            self.assertEqual(metadata["X-Frame-Rotation-Deg"], "0")
            self.assertEqual(metadata["X-Frame-Rectified"], "true")

        # frame GET에서 lease 토큰을 Bearer로 보냈는지 확인
        frame_calls = [(m, p) for m, p, _ in http.calls if p.endswith("/frame")]
        self.assertTrue(len(frame_calls) >= 1)

    def test_json_transport_is_used(self):
        calls = []

        def transport(method, url, headers, data=None):
            calls.append((method, url, headers, data))
            return 200, {'Content-Type': 'application/json'}, json.dumps({
                'source_id': 'test-cam', 'lease': 'test-lease',
                'frame_path': '/api/vision/sources/test-cam/frame'}).encode()

        client = VisionClient(base_url='http://127.0.0.1:1', token='test-token', transport=transport)
        client.lease('test-cam')
        self.assertEqual(json.loads(calls[0][3]), {'source_id': 'test-cam'})

    def test_invalid_mime_suffix_is_rejected(self):
        client = VisionClient(base_url='http://127.0.0.1:1', transport=lambda *args:
                              (200, {'Content-Type': 'image/jpeg-invalid'}, _TINY_JPEG))
        with self.assertRaises(ValueError):
            client.fetch_frame({'source_id': 'test-cam', 'lease': 'test',
                                'frame_path': '/api/vision/sources/test-cam/frame'})

    def test_sources(self):
        with vision_server() as (origin, _):
            client = VisionClient(origin, "fleet-token")
            result = client.sources()
            self.assertEqual(result, {"sources": [{"source_id": "test-cam"}]})

    def test_stale_frame_raises_stale_error(self):
        """404 + X-Frame-State: stale → StaleFrameError."""
        with vision_server() as (origin, _):
            client = VisionClient(origin, "fleet-token")
            lease = client.lease("stale-cam")
            with self.assertRaises(StaleFrameError):
                client.fetch_frame(lease)

    def test_plane_unavailable_raises_plane_unavailable_error(self):
        """409 + X-Frame-State: plane-unavailable → PlaneUnavailableError."""
        with vision_server() as (origin, http):
            client = VisionClient(origin, "fleet-token")
            lease = client.lease("uncalibrated-cam", rectification=MAP_RECTIFICATION)
            with self.assertRaises(PlaneUnavailableError):
                client.fetch_frame(lease)
            self.assertEqual(http.calls[0][2], {"source_id": "uncalibrated-cam", "rectification": {"mode": "map"}})

    def test_non_jpeg_content_type_rejected(self):
        """Content-Type이 image/jpeg가 아니면 ValueError."""
        with vision_server() as (origin, _):
            client = VisionClient(origin, "fleet-token")
            lease = client.lease("text-cam")
            with self.assertRaises(ValueError) as ctx:
                client.fetch_frame(lease)
            self.assertIn("image/jpeg", str(ctx.exception))

    def test_oversized_frame_rejected(self):
        """4 MiB를 초과하면 ValueError."""
        with vision_server() as (origin, _):
            client = VisionClient(origin, "fleet-token")
            lease = client.lease("big-cam")
            with self.assertRaises(ValueError) as ctx:
                client.fetch_frame(lease)
            self.assertIn("exceeds", str(ctx.exception))

    def test_redirect_rejected(self):
        """실제 HTTP 302를 따라가지 않는다."""
        with vision_server() as (origin, http):
            client = VisionClient(origin, 'fleet-token')
            with self.assertRaises(HTTPError) as caught:
                client.fetch_frame({'source_id': 'redirect-cam', 'lease': 'test',
                                    'frame_path': '/api/vision/sources/redirect-cam/frame'})
            caught.exception.close()
            self.assertEqual(caught.exception.code, 302)
            self.assertEqual(http.calls, [('GET', '/api/vision/sources/redirect-cam/frame', None)])

    def test_cross_origin_frame_path_rejected(self):
        """frame_path가 다른 origin이면 ValueError로 거절한다."""
        calls = []

        def transport(method, url, headers, data=None):
            calls.append((method, url))
            return 200, {"Content-Type": "image/jpeg"}, _TINY_JPEG

        client = VisionClient(transport=transport, base_url="http://127.0.0.1:1")
        client._origin = "http://127.0.0.1:1"
        lease_response = {
            "source_id": "cam",
            "lease": "tok",
            "frame_path": "http://evil.example:9999/api/vision/sources/cam/frame",
        }
        with self.assertRaises(ValueError) as ctx:
            client.fetch_frame(lease_response)
        self.assertIn("cross-origin", str(ctx.exception))
        self.assertEqual(len(calls), 0)  # 요청을 보내지 않았다

    def test_zero_port_is_not_the_default_origin(self):
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            200, {'Content-Type': 'image/jpeg'}, _TINY_JPEG))
        with self.assertRaises(ValueError):
            client.fetch_frame({'source_id': 'test-cam', 'lease': 'test',
                                'frame_path': 'https://fleet.example:0/api/vision/sources/test-cam/frame'})

    def test_unsafe_paths_are_rejected_before_transport(self):
        calls = []
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: calls.append(args))
        for path in ('//other.example/api/vision/sources/test-cam/frame',
                     'https://other.example/api/vision/sources/test-cam/frame',
                     'https://user@fleet.example/api/vision/sources/test-cam/frame',
                     '/api/vision/sources/test-cam/frame#fragment',
                     '/api/vision/sources/test-cam/frame\r\n', 'relative/frame'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.fetch_frame({'source_id': 'test-cam', 'lease': 'test', 'frame_path': path})
        self.assertEqual(calls, [])

    def test_transport_status_and_case_insensitive_headers(self):
        for status, headers, expected in (
                (404, {'x-frame-state': 'stale'}, StaleFrameError),
                (409, {'x-frame-state': 'plane-unavailable'}, PlaneUnavailableError),
                (409, {'x-frame-state': 'other'}, ValueError),
                (302, {'content-type': 'image/jpeg'}, ValueError),
                (500, {'content-type': 'image/jpeg'}, ValueError)):
            client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (status, headers, b''))
            with self.subTest(status=status, headers=headers), self.assertRaises(expected):
                client.fetch_frame({'source_id': 'test-cam', 'lease': 'test',
                                    'frame_path': '/api/vision/sources/test-cam/frame'})
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            200, {'content-type': 'image/jpeg; charset=binary', 'x-frame-seq': '8',
                  'x-frame-rectified': 'map', 'x-frame-plane': '-1.37,-0.76,1.37,0.76,400.0000'}, _TINY_JPEG))
        self.assertEqual(client.fetch_frame({'source_id': 'test-cam', 'lease': 'test',
                                             'frame_path': '/api/vision/sources/test-cam/frame'}),
                         (_TINY_JPEG, {'X-Frame-Seq': '8', 'X-Frame-Rectified': 'map',
                                       'X-Frame-Plane': '-1.37,-0.76,1.37,0.76,400.0000'}))

    def test_contract_paths_are_required_before_transport(self):
        calls = []
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            calls.append(args) or (200, {'Content-Type': 'image/jpeg'}, _TINY_JPEG)))
        for path in ('/api/vision/sources/other-cam/frame', '/frame', '/api/fleet/session',
                     '/api/vision/sources/test-cam/frame?extra=1',
                     '/api/vision/sources/test-cam/frame?'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.fetch_frame({'source_id': 'test-cam', 'lease': 'test', 'frame_path': path})
        self.assertEqual(calls, [])

    def test_frame_requires_source_id(self):
        calls = []
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            calls.append(args) or (200, {'Content-Type': 'image/jpeg'}, _TINY_JPEG)))
        for source in ({}, {'source_id': None}, {'source_id': ''}, {'source_id': 1}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                client.fetch_frame({**source, 'lease': 'test',
                                    'frame_path': '/api/vision/sources/test-cam/frame'})
        self.assertEqual(calls, [])

    def test_lease_response_must_match_requested_source(self):
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            200, {}, json.dumps({'source_id': 'other-cam', 'lease': 'test',
                                'frame_path': '/api/vision/sources/other-cam/frame'}).encode()))
        with self.assertRaises(ValueError):
            client.lease('test-cam')

    def test_encoded_source_path_and_same_origin_absolute_url(self):
        calls = []
        client = VisionClient(base_url='https://fleet.example', transport=lambda *args: (
            calls.append(args) or (200, {'Content-Type': 'image/jpeg'}, _TINY_JPEG)))
        for path in ('/api/vision/sources/cam%2F%ED%95%9C%20%3F%23/frame',
                     'https://fleet.example/api/vision/sources/cam%2F%ED%95%9C%20%3F%23/frame'):
            with self.subTest(path=path):
                jpeg, _ = client.fetch_frame({'source_id': 'cam/한 ?#', 'lease': 'test',
                                             'frame_path': path})
                self.assertEqual(jpeg, _TINY_JPEG)
        self.assertEqual(calls[0][1], calls[1][1])

    def test_transport_injection(self):
        """transport를 주입하면 HTTP 없이 시험할 수 있다."""
        calls = []

        def transport(method, url, headers, data=None):
            calls.append((method, url, headers))
            return 200, {"Content-Type": "image/jpeg", "X-Frame-Seq": "7"}, _TINY_JPEG

        client = VisionClient(transport=transport, base_url="http://127.0.0.1:1")
        lease_response = {"source_id": "cam", "lease": "my-lease", "frame_path": "/api/vision/sources/cam/frame"}
        jpeg, meta = client.fetch_frame(lease_response)
        self.assertEqual(jpeg, _TINY_JPEG)
        self.assertEqual(meta["X-Frame-Seq"], "7")
        # Bearer로 lease 토큰을 보냈는지 확인
        self.assertEqual(calls[0][2]["Authorization"], "Bearer my-lease")

    def test_lease_422_raises_rectification_unsupported(self):
        client = VisionClient(
            base_url='https://fleet.example',
            token='tok',
            transport=lambda *args: (422, {}, b'unsupported'),
        )
        with self.assertRaises(RectificationUnsupportedError):
            client.lease('test-cam', rectification=MAP_OVERLAY_RECTIFICATION)


if __name__ == "__main__":
    unittest.main()
