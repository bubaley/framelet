import base64
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser

from framelet.config import Settings
from framelet.errors import PreviewError

HTML = '<input id="video" type="file" accept="video/*">'

# Keep playback paused. Only seek for a positive timestamp: seeking to zero
# can omit the seeked event when the video is already at zero.
CAPTURE = r"""({maxPixels, timeoutMs, timestampMs}) => new Promise((resolve) => {
    const input = document.querySelector('#video');
    const video = document.createElement('video');
    video.preload = 'auto';
    video.muted = true;
    video.playsInline = true;
    document.body.appendChild(video);
    let url;
    let completed = false;
    const finish = (result) => {
        if (completed) return;
        completed = true;
        clearTimeout(timer);
        video.pause();
        video.removeAttribute('src');
        video.load();
        if (url) URL.revokeObjectURL(url);
        resolve(result);
    };
    const timer = setTimeout(() => finish({error: 'timeout'}), timeoutMs);
    video.addEventListener('error', () => finish({error: 'invalid_video'}), {once: true});
    const draw = () => {
        if (completed) return;
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) return finish({error: 'invalid_video'});
        if (width * height > maxPixels) return finish({error: 'frame_too_large'});
        try {
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const context = canvas.getContext('2d');
            context.drawImage(video, 0, 0, width, height);
            const png = canvas.toDataURL('image/png');
            if (!png.startsWith('data:image/png;base64,')) return finish({error: 'invalid_video'});
            finish({width, height, png: png.split(',')[1]});
        } catch {
            finish({error: 'invalid_video'});
        }
    };
    let loaded = false;
    let presented = false;
    const initialReady = () => {
        if (completed || !loaded || !presented) return;
        if (timestampMs === 0) return draw();
        const time = timestampMs / 1000;
        if (!Number.isFinite(video.duration) || time >= video.duration) {
            return finish({error: 'timestamp_out_of_range'});
        }
        let seeked = false;
        let seekPresented = false;
        const seekReady = () => {
            if (seeked && seekPresented) draw();
        };
        video.requestVideoFrameCallback(() => {
            seekPresented = true;
            seekReady();
        });
        video.addEventListener('seeked', () => {
            seeked = true;
            seekReady();
        }, {once: true});
        video.currentTime = time;
    };
    video.requestVideoFrameCallback(() => {
        presented = true;
        initialReady();
    });
    video.addEventListener('loadeddata', () => {
        loaded = true;
        initialReady();
    }, {once: true});
    input.addEventListener('change', () => {
        const file = input.files[0];
        if (!file) return finish({error: 'invalid_video'});
        url = URL.createObjectURL(file);
        video.src = url;
        video.load();
    }, {once: true});
    window.captureArmed = true;
})"""


@dataclass(frozen=True)
class Preview:
    png: bytes
    width: int
    height: int


def capture(browser: Browser, path: Path, settings: Settings, timestamp_ms: int = 0) -> Preview:
    context = browser.new_context(service_workers='block', accept_downloads=False)
    try:
        context.route('**/*', lambda route: route.abort())
        page = context.new_page()
        page.set_default_timeout(settings.render_timeout_seconds * 1000)
        page.set_content(HTML)
        # Arm the promise before attaching a file. evaluate() would wait for it,
        # so keep it on the page and await it after set_input_files().
        page.evaluate(
            f'(options) => {{ window.captureResult = ({CAPTURE})(options); }}',
            {
                'maxPixels': settings.max_frame_pixels,
                'timeoutMs': settings.render_timeout_seconds * 1000,
                'timestampMs': timestamp_ms,
            },
        )
        page.locator('#video').set_input_files(path)
        result: object = page.evaluate('window.captureResult')
        if not isinstance(result, dict):
            raise PreviewError(502, 'invalid_renderer_result', 'The renderer returned an invalid result.')
        error = result.get('error')
        if error == 'timeout':
            raise PreviewError(504, 'render_timeout', 'Video decoding timed out.')
        if error == 'timestamp_out_of_range':
            raise PreviewError(422, 'timestamp_out_of_range', 'The timestamp must be less than the video duration.')
        if error == 'frame_too_large':
            raise PreviewError(413, 'frame_too_large', 'The video frame exceeds the pixel limit.')
        if error:
            raise PreviewError(422, 'invalid_video', 'The video cannot be decoded by Chromium.')
        png_base64, width, height = result.get('png'), result.get('width'), result.get('height')
        if not isinstance(png_base64, str) or not isinstance(width, int) or not isinstance(height, int):
            raise PreviewError(502, 'invalid_renderer_result', 'The renderer returned an invalid result.')
        if len(png_base64) > ((settings.max_frame_bytes + 2) // 3) * 4:
            raise PreviewError(413, 'frame_too_large', 'The PNG exceeds the output size limit.')
        png = base64.b64decode(png_base64, validate=True)
        if len(png) > settings.max_frame_bytes:
            raise PreviewError(413, 'frame_too_large', 'The PNG exceeds the output size limit.')
        return Preview(png, width, height)
    finally:
        context.close()
