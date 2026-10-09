# Framelet

Extract a video frame as PNG at a selected timestamp (or the first frame by default), using Chromium's video-to-canvas rendering pipeline.

Framelet is a standalone, synchronous HTTP service. It has no database or permanent storage. It does not transcode
videos, fetch URLs, or upload files to object storage. All API handlers use normal Python `def`; rendering uses
Playwright's synchronous API in a dedicated process. The small ASGI middleware/lifespan layer is asynchronous as
required by FastAPI, but clients receive the PNG in the original HTTP response, without jobs, polling, or callbacks.

## Run with Docker

Create a local `.env` file with a random token (at least 16 characters):

```sh
printf 'FRAMELET_API_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env
docker build --platform linux/amd64 -t framelet:local .
docker run --rm --name framelet \
  --env-file .env -p 127.0.0.1:8000:8000 \
  --memory 1g --cpus 2 --shm-size 256m \
  --read-only --tmpfs /tmp:rw,nosuid,size=256m \
  --cap-drop ALL --security-opt no-new-privileges \
  framelet:local
```

Published images use `bubaley/framelet:<version>` on Docker Hub. No images are published to GHCR.
The supported container platform is Linux amd64; native ARM Chromium lacks the required H.264 codec in this build.
The process runs as a non-root user, with Tini to reap terminated browser children.
Chromium's own sandbox is disabled by Playwright's default launch mode;
run the service in an isolated container with the limits above, on a private network or behind an authenticated proxy.
The render page blocks network requests and only reads the supplied local video.

## Docker Compose

The repository includes [docker-compose.yaml](docker-compose.yaml) and [.env.example](.env.example) with all limits.
Create `.env` with a random token and start the service:

```sh
printf 'FRAMELET_API_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env
docker compose pull
docker compose up -d
curl --fail http://127.0.0.1:8000/health/ready
```

Set `FRAMELET_IMAGE_TAG` in `.env` to a release version to pin deployments. The default is `latest`.
`FRAMELET_BIND_ADDRESS` defaults to `127.0.0.1`; containers on the same Docker network can use `http://framelet:8000`.
Set `FRAMELET_PORT` to change the host port. No persistent volumes are needed.

## API

```sh
curl --fail-with-body http://localhost:8000/v1/preview \
  -H "Authorization: Bearer $FRAMELET_API_TOKEN" \
  -F 'video=@example.mp4;type=video/mp4' \
  -F 'timestamp_ms=1250' \
  --output preview.png
```

The shell variable must contain the same token as `.env`; Docker does not export it into the shell automatically.
Success: `200 image/png`, with `X-Frame-Width` and `X-Frame-Height` headers. The PNG keeps the video's decoded
dimensions, without resizing or color filters. Omit `timestamp_ms` (or pass `0`) for the first decoded frame.
The parameter is a non-negative integer in milliseconds and must be less than the video duration; an out-of-range
time returns `422 timestamp_out_of_range`. Chromium seeks while paused to the frame displayed at that time;
millisecond input does not imply that the source video has a frame for every millisecond.

Supported containers are MP4/MOV (with an initial `ftyp` box) and WebM, detected from file contents rather than
the filename. Integration tests cover H.264 in MP4/MOV and VP9 in WebM. Actual codec support depends on the pinned
Chromium build; unsupported codecs and damaged videos return `422`. AVI, WMV and older MOV files without `ftyp`
are not accepted. See [Chromium media support](https://www.chromium.org/audio-video/) for codec details.

| Status | Error codes | Meaning |
| --- | --- | --- |
| 400 | `invalid_content_length`, `invalid_request` | Malformed request or multipart body |
| 401 | `unauthorized` | Missing or incorrect bearer token |
| 408 | `upload_timeout` | Upload deadline exceeded |
| 413 | `upload_too_large`, `frame_too_large` | Input, pixel count, or PNG size limit exceeded |
| 415 | `unsupported_media_type`, `unsupported_video` | Unsupported request type or container |
| 422 | `empty_video`, `invalid_video`, `invalid_request`, `timestamp_out_of_range` | Invalid video, timestamp, or multipart fields |
| 429 | `service_busy` | An upload or render is already in progress; `Retry-After: 1` |
| 503 | `browser_unavailable`, `service_stopping` | Renderer unavailable |
| 504 | `render_timeout` | Hard rendering deadline exceeded |
| 500/502 | `internal_error`, `render_failed`, `invalid_renderer_result` | Unexpected internal failure |

Errors have the form `{"error":{"code":"invalid_video","message":"..."}}`.

- `GET /health/live` checks the HTTP process.
- `GET /health/ready` checks Chromium, independently of the upload slot.
- `/docs` and `/openapi.json` describe the API. Do not expose them if your deployment does not need them.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `FRAMELET_API_TOKEN` | required | Bearer token, at least 16 characters |
| `FRAMELET_MAX_VIDEO_BYTES` | `20971520` | 20 MiB input limit |
| `FRAMELET_MAX_FRAME_PIXELS` | `16777216` | Decoded width × height limit |
| `FRAMELET_MAX_FRAME_BYTES` | `67108864` | PNG output limit |
| `FRAMELET_UPLOAD_TIMEOUT_SECONDS` | `30` | Deadline for receiving the complete multipart body |
| `FRAMELET_RENDER_TIMEOUT_SECONDS` | `15` | Hard deadline for a render, including page cleanup |
| `FRAMELET_STARTUP_TIMEOUT_SECONDS` | `30` | Browser startup/restart deadline |
| `FRAMELET_TEMP_DIRECTORY` | system temporary directory | Per-request temporary files |

The total multipart body limit is the video limit plus 64 KiB of encoding overhead. It is enforced even when
`Content-Length` is missing or inaccurate. Authentication and admission control run before multipart parsing.
At most one upload/render is accepted per HTTP process; excess requests fail immediately. Scale with containers,
not Uvicorn workers, so memory usage and admission limits remain predictable. Slow uploads are stopped after the
upload deadline. Configure connection timeouts and request-rate limits at your reverse proxy as well.

One spawned worker owns Playwright and Chromium for its lifetime. Each render creates a fresh browser context.
The HTTP process enforces the deadline and kills the entire worker process group on timeout or renderer failure.
The renderer restarts on a subsequent request or readiness probe. Temporary files are cleaned up on success and failure.

## Color and first-frame behavior

The video remains paused. With no timestamp it stays at its initial playback position. Framelet waits for `loadeddata` and `requestVideoFrameCallback`, then draws the presented
initial frame into a canvas and exports PNG. For a positive timestamp it waits for both `seeked` and frame presentation before exporting.
It does not seek to zero or start playback, avoiding both a missing
`seeked` event at time zero and accidental selection of a later frame.

Playwright is pinned to **1.57.0** and installs its matching Chromium. This fixes the browser dependency, but does
not guarantee identical colors across operating systems, browser upgrades, HDR inputs, or different downstream
image encoders. Compare representative source videos and their final displayed previews before changing an
existing production pipeline. The integration tests check first-frame and timestamp selection using generated red/blue MP4, MOV and WebM.

## Development

Python 3.13 and uv:

```sh
uv sync --locked
uv run playwright install --only-shell chromium
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests scripts
uv run pytest
FRAMELET_API_TOKEN=development-token-change-me uv run uvicorn framelet.app:create_app --factory --host 127.0.0.1
```

## Releases

Stable releases are available as versioned Docker Hub images and `latest`. The release workflow prepares the
version, tests and builds the image, verifies its startup, publishes it to Docker Hub, then creates the GitHub Release.
See the [GitHub Releases](https://github.com/bubaley/framelet/releases) for release notes.
Release automation and contributor commit conventions are documented in [docs/releasing.md](docs/releasing.md).

MIT licensed. See [LICENSE](LICENSE).
