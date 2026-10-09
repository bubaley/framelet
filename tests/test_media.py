import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from framelet.config import Settings
from framelet.errors import PreviewError
from framelet.media import prepare_video

TOKEN = 'test-media-token-with-enough-length'


def probe(codec: str = 'hevc', **values: object) -> bytes:
    return json.dumps(
        {
            'streams': [
                {
                    'codec_name': codec,
                    'width': 128,
                    'height': 96,
                    'pix_fmt': 'yuv420p',
                    'duration': '1',
                    'color_space': 'bt709',
                    **values,
                }
            ]
        }
    ).encode()


def test_only_hevc_is_prepared(tmp_path: Path) -> None:
    path = tmp_path / 'input.video'
    with patch('framelet.media.run_media', return_value=probe('h264')) as run:
        assert prepare_video(path, Settings(api_token=SecretStr(TOKEN)), 0) == path
        assert run.call_count == 1


@pytest.mark.parametrize(
    ('data', 'timestamp', 'code'),
    [
        (b'{}', 0, 'invalid_video'),
        (b'[]', 0, 'invalid_video'),
        (probe(width=10000, height=10000), 0, 'frame_too_large'),
        (probe(pix_fmt='yuv444p12le'), 0, 'unsupported_video'),
        (probe(), 1000, 'timestamp_out_of_range'),
        (probe(duration='nan'), 1, 'invalid_video'),
    ],
)
def test_invalid_media_never_runs_encoder(tmp_path: Path, data: bytes, timestamp: int, code: str) -> None:
    with patch('framelet.media.run_media', return_value=data) as run:
        with pytest.raises(PreviewError) as error:
            prepare_video(tmp_path / 'input.video', Settings(api_token=SecretStr(TOKEN)), timestamp)
        assert error.value.code == code
        assert run.call_count == 1


def test_intermediate_limit_and_failure_remove_file(tmp_path: Path) -> None:
    path = tmp_path / 'input.video'
    prepared = path.with_suffix('.chromium.webm')
    settings = Settings(api_token=SecretStr(TOKEN), max_intermediate_bytes=1024)

    def run(command: list[str], deadline: float, *, output: bool = False) -> bytes:
        if output:
            return probe()
        prepared.write_bytes(b'x' * 1024)
        return b''

    with patch('framelet.media.run_media', side_effect=run), pytest.raises(PreviewError) as error:
        prepare_video(path, settings, 0)
    assert error.value.code == 'frame_too_large'
    assert not prepared.exists()


@pytest.mark.parametrize('extension', ['hevc', 'hevc-10bit'])
def test_lossless_preserves_decoded_yuv_and_color_metadata(extension: str, tmp_path: Path) -> None:
    source = Path(__file__).parent / 'fixtures' / f'red-blue-{extension}.mp4'
    path = tmp_path / 'input.video'
    path.write_bytes(source.read_bytes())
    prepared = prepare_video(path, Settings(api_token=SecretStr(TOKEN)), 0)
    command = ['ffmpeg', '-v', 'error', '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-']
    original = subprocess.check_output(command)
    command[4] = str(prepared)
    assert subprocess.check_output(command) == original
    for file in [path, prepared]:
        data = json.loads(
            subprocess.check_output(
                [
                    'ffprobe',
                    '-v',
                    'quiet',
                    '-select_streams',
                    'v:0',
                    '-show_entries',
                    'stream=pix_fmt,color_space,color_transfer,color_primaries,color_range',
                    '-of',
                    'json',
                    str(file),
                ]
            )
        )
        if file == path:
            expected = data['streams'][0]
        else:
            assert data['streams'][0] == expected


@pytest.mark.parametrize(
    ('failure', 'code'),
    [
        (FileNotFoundError('private filesystem path'), 'decoder_unavailable'),
        (subprocess.TimeoutExpired(['private command'], 1), 'render_timeout'),
    ],
)
def test_media_tool_errors_are_sanitized(failure: Exception, code: str) -> None:
    from time import monotonic

    from framelet.media import run_media

    with patch('framelet.media.subprocess.run', side_effect=failure), pytest.raises(PreviewError) as error:
        run_media(['ffprobe'], monotonic() + 1)
    assert error.value.code == code
    assert 'private' not in error.value.message
