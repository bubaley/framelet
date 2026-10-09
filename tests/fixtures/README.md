# Generated video fixture

`red-blue.mp4` is a synthetic 128×96 H.264 MP4. The initial frame is red; frames from 0.5 seconds onward are blue.
It contains no third-party footage. Tests check that the initial red frame is captured rather than a later blue one.

Regenerate the H.264 fixture with FFmpeg:

```sh
ffmpeg -f lavfi -i 'color=c=red:s=128x96:r=2:d=0.5' \
  -f lavfi -i 'color=c=blue:s=128x96:r=2:d=0.5' \
  -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0[v]' -map '[v]' \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart -y tests/fixtures/red-blue.mp4
```

`red-blue-hevc.mp4` and `red-blue-hevc-10bit.mp4` are lossless HEVC copies generated
from the same synthetic video with libx265. They test first-frame/timestamp selection,
8/10-bit preservation, decoded YUV equality and color tags. No client video is committed.

```sh
ffmpeg -i tests/fixtures/red-blue.mp4 -an -c:v libx265 \
  -x265-params 'lossless=1:log-level=error:pools=1:frame-threads=1' \
  -pix_fmt yuv420p -tag:v hvc1 tests/fixtures/red-blue-hevc.mp4
# Use yuv420p10le for red-blue-hevc-10bit.mp4.
```
