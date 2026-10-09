"""Exercise the running runtime image using only the Python standard library."""

import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    base = 'http://127.0.0.1:8000'
    for _ in range(40):
        try:
            with urllib.request.urlopen(f'{base}/health/ready', timeout=2) as response:
                assert json.load(response)['status'] == 'ready'
            break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise SystemExit('Runtime container did not become ready')
    video = Path('tests/fixtures/red-blue.mp4').read_bytes()
    boundary = 'framelet-smoke-test'
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="sample.mp4"\r\n'
            'Content-Type: video/mp4\r\n\r\n'
        ).encode()
        + video
        + f'\r\n--{boundary}--\r\n'.encode()
    )
    request = urllib.request.Request(
        f'{base}/v1/preview',
        data=body,
        headers={
            'Authorization': 'Bearer ci-only-token-not-for-production',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        png = response.read()
        assert response.headers['Content-Type'] == 'image/png'
        assert png[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', png[16:24]) == (128, 96)
    print('Runtime container is ready and returned a 128x96 PNG')


if __name__ == '__main__':
    main()
