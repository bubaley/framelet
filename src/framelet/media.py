"""Prepare HEVC for Chromium without converting decoded pixels to RGB."""

import json
import math
import subprocess
from pathlib import Path
from time import monotonic

from framelet.config import Settings
from framelet.errors import PreviewError

PIXEL_FORMATS = {'yuv420p', 'yuv420p10le'}


def run_media(command: list[str], deadline: float, *, output: bool = False) -> bytes:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise PreviewError(504, 'render_timeout', 'Video preparation exceeded the time limit.')
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PreviewError(503, 'decoder_unavailable', 'The video decoder is unavailable.') from exc
    except subprocess.TimeoutExpired as exc:
        raise PreviewError(504, 'render_timeout', 'Video preparation exceeded the time limit.') from exc
    if result.returncode:
        raise PreviewError(422, 'invalid_video', 'The video cannot be decoded.')
    return result.stdout or b''


def prepare_video(path: Path, settings: Settings, timestamp_ms: int) -> Path:
    deadline = monotonic() + settings.render_timeout_seconds
    raw = run_media(
        [
            'ffprobe',
            '-v',
            'quiet',
            '-protocol_whitelist',
            'file,pipe',
            '-select_streams',
            'v:0',
            '-show_entries',
            'stream=codec_name,width,height,pix_fmt,duration,color_range,color_space,color_transfer,color_primaries:'
            'format=duration',
            '-of',
            'json',
            str(path),
        ],
        deadline,
        output=True,
    )
    try:
        metadata = json.loads(raw)
        stream = metadata['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
        if width <= 0 or height <= 0:
            raise ValueError('Invalid dimensions')
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise PreviewError(422, 'invalid_video', 'The video has no valid video stream.') from exc
    if width * height > settings.max_frame_pixels:
        raise PreviewError(413, 'frame_too_large', 'The video frame exceeds the pixel limit.')
    if stream.get('codec_name') != 'hevc':
        return path
    pixel_format = stream.get('pix_fmt')
    if pixel_format not in PIXEL_FORMATS:
        raise PreviewError(422, 'unsupported_video', 'HEVC requires 8-bit or 10-bit YUV 4:2:0 video.')
    if timestamp_ms:
        try:
            duration = float(stream.get('duration', metadata.get('format', {}).get('duration', 'nan')))
        except (ValueError, TypeError) as exc:
            raise PreviewError(422, 'invalid_video', 'The video duration cannot be determined.') from exc
        if not math.isfinite(duration) or duration <= 0:
            raise PreviewError(422, 'invalid_video', 'The video duration cannot be determined.')
        if timestamp_ms / 1000 >= duration:
            raise PreviewError(422, 'timestamp_out_of_range', 'The timestamp must be less than the video duration.')
    destination = path.with_suffix('.chromium.webm')
    command = [
        'ffmpeg',
        '-v',
        'quiet',
        '-nostdin',
        '-y',
        '-protocol_whitelist',
        'file,pipe',
        '-threads',
        '2',
        '-i',
        str(path),
        '-map',
        '0:v:0',
        '-an',
        '-sn',
        '-dn',
        '-c:v',
        'libvpx-vp9',
        '-lossless',
        '1',
        '-deadline',
        'realtime',
        '-cpu-used',
        '8',
        '-lag-in-frames',
        '0',
        '-threads',
        '2',
        '-pix_fmt',
        pixel_format,
        '-fps_mode',
        'passthrough',
        '-fs',
        str(settings.max_intermediate_bytes),
    ]
    # A first-frame request only encodes one frame. For seeking, keep the source
    # frame timestamps through the requested position so Chromium still selects it.
    if timestamp_ms == 0:
        command += ['-frames:v', '1']
    else:
        command += ['-t', str(timestamp_ms / 1000 + 1)]
    for key, option in [
        ('color_range', '-color_range'),
        ('color_space', '-colorspace'),
        ('color_transfer', '-color_trc'),
        ('color_primaries', '-color_primaries'),
    ]:
        value = stream.get(key)
        if isinstance(value, str) and value not in {'unknown', 'unspecified'}:
            command += [option, value]
    command.append(str(destination))
    try:
        run_media(command, deadline)
        if not destination.exists() or not destination.stat().st_size:
            raise PreviewError(422, 'invalid_video', 'The video cannot be decoded.')
        if destination.stat().st_size >= settings.max_intermediate_bytes:
            raise PreviewError(413, 'frame_too_large', 'Video preparation exceeds the temporary file limit.')
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
