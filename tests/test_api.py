import asyncio
import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message, Scope

from framelet.app import create_app
from framelet.browser import Preview
from framelet.config import Settings
from framelet.errors import PreviewError

TOKEN = 'test-only-token-with-enough-length'
AUTH = {'Authorization': f'Bearer {TOKEN}'}
MP4 = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 24


class FakeRenderer:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.paths: list[Path] = []
        self.timestamps: list[int] = []
        self.failure: PreviewError | None = None
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def start(self) -> None:
        self.started = True

    def extract(self, path: Path, timestamp_ms: int = 0) -> Preview:
        assert path.read_bytes().startswith(MP4[:12])
        self.paths.append(path)
        self.timestamps.append(timestamp_ms)
        self.entered.set()
        if self.block:
            assert self.release.wait(timeout=10)
        if self.failure:
            raise self.failure
        return Preview(b'\x89PNG\r\n\x1a\n', 128, 96)

    def ready(self) -> bool:
        return self.started and not self.closed

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def config(tmp_path: Path) -> Settings:
    return Settings(api_token=SecretStr(TOKEN), max_video_bytes=1024, temp_directory=str(tmp_path))


def test_success_and_lifecycle(config: Settings, tmp_path: Path) -> None:
    renderer = FakeRenderer()
    with TestClient(create_app(config, renderer)) as client:
        response = client.post('/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4, 'video/mp4')})
        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/png'
        assert response.headers['x-frame-width'] == '128'
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 200
        assert client.get('/openapi.json').json()['components']['securitySchemes']['BearerAuth']['scheme'] == 'bearer'
        assert not list(tmp_path.iterdir())
        assert all(not path.exists() for path in renderer.paths)
    assert renderer.closed


@pytest.mark.parametrize('authorization', ['', 'Bearer wrong', 'Bearer \u2603'])
def test_unauthorized_rejected_before_body(config: Settings, authorization: str) -> None:
    renderer = FakeRenderer()
    with TestClient(create_app(config, renderer)) as client:
        # Non-ASCII bytes exercise constant-time comparison without crashing.
        response = client.post(
            '/v1/preview',
            headers={b'authorization': authorization.encode('utf-8')},
            content=b'invalid multipart body',
        )
        assert response.status_code == 401
        assert response.json()['error']['code'] == 'unauthorized'
        assert renderer.paths == []


@pytest.mark.parametrize(
    ('filename', 'data', 'status', 'code'),
    [
        ('sample.avi', b'RIFFinvalid', 415, 'unsupported_video'),
        ('sample.mp4', b'not-an-mp4', 415, 'unsupported_video'),
        ('sample.mp4', b'', 422, 'empty_video'),
        ('sample.mp4', MP4 + b'x' * 1024, 413, 'upload_too_large'),
    ],
)
def test_input_errors(config: Settings, tmp_path: Path, filename: str, data: bytes, status: int, code: str) -> None:
    with TestClient(create_app(config, FakeRenderer())) as client:
        response = client.post('/v1/preview', headers=AUTH, files={'video': (filename, data, 'video/mp4')})
        assert response.status_code == status
        assert response.json()['error']['code'] == code
        assert not list(tmp_path.iterdir())


def test_failure_cleans_files_and_releases_capacity(config: Settings, tmp_path: Path) -> None:
    renderer = FakeRenderer()
    renderer.failure = PreviewError(504, 'render_timeout', 'Timed out.')
    with TestClient(create_app(config, renderer)) as client:
        response = client.post('/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)})
        assert response.status_code == 504
        assert not list(tmp_path.iterdir())
        renderer.failure = None
        assert client.post('/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)}).status_code == 200


def test_busy_rejected_and_health_remains_available(config: Settings) -> None:
    renderer = FakeRenderer()
    renderer.block = True
    with TestClient(create_app(config, renderer)) as client, ThreadPoolExecutor() as executor:
        request = executor.submit(client.post, '/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)})
        assert renderer.entered.wait(timeout=5)
        try:
            response = client.post('/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)})
            assert response.status_code == 429
            assert response.headers['retry-after'] == '1'
            assert client.get('/health/ready').status_code == 200
        finally:
            renderer.release.set()
        assert request.result(timeout=5).status_code == 200


def test_chunked_request_body_limit(config: Settings) -> None:
    def body() -> Iterator[bytes]:
        yield b'--test\r\nContent-Disposition: form-data; name="video"; filename="sample.mp4"\r\n\r\n'
        yield MP4
        yield b'x' * config.max_request_bytes
        yield b'\r\n--test--\r\n'

    with TestClient(create_app(config, FakeRenderer())) as client:
        response = client.post(
            '/v1/preview', headers={**AUTH, 'Content-Type': 'multipart/form-data; boundary=test'}, content=body()
        )
        assert response.status_code == 413
        assert response.json()['error']['code'] == 'upload_too_large'
        assert client.post('/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)}).status_code == 200


def test_large_declared_body_and_missing_field(config: Settings) -> None:
    with TestClient(create_app(config, FakeRenderer())) as client:
        response = client.post(
            '/v1/preview',
            headers={**AUTH, 'Content-Length': str(config.max_request_bytes + 1)},
            files={'video': ('sample.mp4', MP4)},
        )
        assert response.status_code == 413
        response = client.post('/v1/preview', headers=AUTH, files={'other': ('sample.mp4', MP4)})
        assert response.status_code == 422
        response = client.post('/v1/preview', headers=AUTH, content=MP4)
        assert response.status_code == 415


def test_slow_upload_deadline(config: Settings) -> None:
    config.upload_timeout_seconds = 0.1
    app = create_app(config, FakeRenderer())
    messages: list[Message] = []
    calls = 0

    async def receive() -> Message:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                'type': 'http.request',
                'body': b'--test\r\nContent-Disposition: form-data; name="video"; filename="sample.mp4"\r\n\r\n' + MP4,
                'more_body': True,
            }
        await asyncio.sleep(10)
        return {'type': 'http.request', 'body': b'\r\n--test--\r\n', 'more_body': False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/v1/preview',
        'root_path': '',
        'query_string': b'',
        'http_version': '1.1',
        'scheme': 'http',
        'server': ('testserver', 80),
        'client': ('127.0.0.1', 12345),
        'headers': [
            (b'authorization', f'Bearer {TOKEN}'.encode()),
            (b'content-type', b'multipart/form-data; boundary=test'),
        ],
    }
    asyncio.run(app(scope, receive, send))
    assert messages[0]['status'] == 408
    assert json.loads(messages[1]['body'])['error']['code'] == 'upload_timeout'


@pytest.mark.parametrize('timestamp', ['-1', '1.5', 'nope', str(2**53)])
def test_invalid_timestamp(config: Settings, timestamp: str) -> None:
    renderer = FakeRenderer()
    with TestClient(create_app(config, renderer)) as client:
        response = client.post(
            '/v1/preview', headers=AUTH, files={'video': ('sample.mp4', MP4)}, data={'timestamp_ms': timestamp}
        )
        assert response.status_code == 422
        assert renderer.paths == []


def test_timestamp_passed_to_renderer(config: Settings) -> None:
    renderer = FakeRenderer()
    with TestClient(create_app(config, renderer)) as client:
        for timestamp in [None, '0', '1250']:
            response = client.post(
                '/v1/preview',
                headers=AUTH,
                files={'video': ('video.bin', MP4)},
                data={} if timestamp is None else {'timestamp_ms': timestamp},
            )
            assert response.status_code == 200
        assert renderer.timestamps == [0, 0, 1250]
