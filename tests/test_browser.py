from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from framelet.app import create_app
from framelet.config import Settings
from framelet.errors import PreviewError
from framelet.worker import Renderer

VIDEO = Path(__file__).parent / 'fixtures' / 'red-blue.mp4'
TOKEN = 'browser-test-token-with-enough-length'


@pytest.mark.browser
def test_first_frame_real_http_and_cleanup(tmp_path: Path) -> None:
    settings = Settings(api_token=SecretStr(TOKEN), temp_directory=str(tmp_path))
    with TestClient(create_app(settings)) as client:
        assert client.get('/health/ready').status_code == 200
        for _ in range(2):
            with VIDEO.open('rb') as video:
                response = client.post(
                    '/v1/preview',
                    headers={'Authorization': f'Bearer {TOKEN}'},
                    files={'video': ('red-blue.mp4', video)},
                )
            assert response.status_code == 200, response.text
            image = Image.open(BytesIO(response.content)).convert('RGB')
            assert image.size == (128, 96)
            assert image.getpixel((64, 48)) == pytest.approx((253, 0, 0), abs=6)
            assert not list(tmp_path.iterdir())
        response = client.post(
            '/v1/preview',
            headers={'Authorization': f'Bearer {TOKEN}'},
            files={'video': ('broken.mp4', b'\x00\x00\x00\x18ftypisom' + b'\x00' * 24)},
        )
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'invalid_video'
        assert not list(tmp_path.iterdir())


@pytest.mark.browser
def test_pixel_limit_and_browser_crash_recovery() -> None:
    renderer = Renderer(Settings(api_token=SecretStr(TOKEN), max_frame_pixels=100))
    try:
        renderer.start()
        with pytest.raises(PreviewError) as error:
            renderer.extract(VIDEO)
        assert error.value.code == 'frame_too_large'
        process = renderer._process
        assert process is not None
        process.kill()
        process.join(timeout=3)
        renderer.settings.max_frame_pixels = 128 * 96
        frame = renderer.extract(VIDEO)
        assert (frame.width, frame.height) == (128, 96)
        assert renderer.ready()
    finally:
        renderer.close()


@pytest.mark.browser
@pytest.mark.parametrize('extension', ['mp4', 'mov', 'webm'])
def test_timestamp_and_container_support(extension: str, tmp_path: Path) -> None:
    video_path = VIDEO.with_suffix(f'.{extension}')
    settings = Settings(api_token=SecretStr(TOKEN), temp_directory=str(tmp_path))
    with TestClient(create_app(settings)) as client:
        for timestamp, color in [
            (None, (253, 0, 0)),
            (0, (253, 0, 0)),
            (1, (253, 0, 0)),
            (750, (0, 0, 254)),
            (999, (0, 0, 254)),
        ]:
            with video_path.open('rb') as video:
                response = client.post(
                    '/v1/preview',
                    headers={'Authorization': f'Bearer {TOKEN}'},
                    files={'video': ('upload.bin', video)},
                    data={} if timestamp is None else {'timestamp_ms': str(timestamp)},
                )
            assert response.status_code == 200, response.text
            image = Image.open(BytesIO(response.content)).convert('RGB')
            assert image.getpixel((64, 48)) == pytest.approx(color, abs=6), (extension, timestamp)
            assert not list(tmp_path.iterdir())
        for timestamp in [1000, 1001]:
            with video_path.open('rb') as video:
                response = client.post(
                    '/v1/preview',
                    headers={'Authorization': f'Bearer {TOKEN}'},
                    files={'video': ('upload.bin', video)},
                    data={'timestamp_ms': str(timestamp)},
                )
            assert response.status_code == 422, response.text
            assert response.json()['error']['code'] == 'timestamp_out_of_range'
            assert not list(tmp_path.iterdir())
        assert client.get('/health/ready').status_code == 200


@pytest.mark.browser
@pytest.mark.parametrize('extension', ['hevc', 'hevc-10bit'])
def test_hevc_first_frame_timestamp_and_cleanup(extension: str, tmp_path: Path) -> None:
    settings = Settings(api_token=SecretStr(TOKEN), temp_directory=str(tmp_path))
    source = VIDEO.with_name(f'red-blue-{extension}.mp4')
    with TestClient(create_app(settings)) as client:
        for timestamp, color in [(None, (253, 0, 0)), (1, (253, 0, 0)), (750, (0, 0, 254)), (999, (0, 0, 254))]:
            response = client.post(
                '/v1/preview',
                headers={'Authorization': f'Bearer {TOKEN}'},
                files={'video': ('upload.mp4', source.read_bytes())},
                data={} if timestamp is None else {'timestamp_ms': str(timestamp)},
            )
            assert response.status_code == 200, response.text
            image = Image.open(BytesIO(response.content)).convert('RGB')
            assert image.size == (128, 96)
            assert image.getpixel((64, 48)) == pytest.approx(color, abs=6)
            assert not list(tmp_path.iterdir())
        response = client.post(
            '/v1/preview',
            headers={'Authorization': f'Bearer {TOKEN}'},
            files={'video': ('upload.mp4', source.read_bytes())},
            data={'timestamp_ms': '1000'},
        )
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'timestamp_out_of_range'
        assert not list(tmp_path.iterdir())
