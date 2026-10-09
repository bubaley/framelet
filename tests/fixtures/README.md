# Generated video fixture

`red-blue.mp4` is a synthetic 128×96 H.264 MP4. The initial frame is red; frames from 0.5 seconds onward are blue.
It contains no third-party footage. Tests check that the initial red frame is captured rather than a later blue one.

Regenerate with FFmpeg (test fixture generation only; FFmpeg is not a service dependency):

```sh
ffmpeg -f lavfi -i 'color=c=red:s=128x96:r=2:d=0.5' \
  -f lavfi -i 'color=c=blue:s=128x96:r=2:d=0.5' \
  -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0[v]' -map '[v]' \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart -y tests/fixtures/red-blue.mp4
```
