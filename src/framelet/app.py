import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Protocol

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from framelet.browser import Preview
from framelet.config import Settings
from framelet.errors import PreviewError
from framelet.middleware import UploadGuard
from framelet.worker import Renderer

logger = logging.getLogger(__name__)


class PreviewRenderer(Protocol):
    def start(self) -> None: ...
    def extract(self, path: Path) -> Preview: ...
    def ready(self) -> bool: ...
    def close(self) -> None: ...


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {'error': {'code': code, 'message': message}},
        status_code=status,
        headers={'Retry-After': '1'} if status == 429 else None,
    )


def save_upload(video: UploadFile, path: Path, settings: Settings) -> None:
    if not video.filename or Path(video.filename).suffix.lower() != '.mp4':
        raise PreviewError(415, 'unsupported_video', 'An MP4 file is required.')
    if video.size is not None and video.size > settings.max_video_bytes:
        raise PreviewError(413, 'upload_too_large', 'The video exceeds the size limit.')
    total = 0
    with path.open('wb') as destination:
        while chunk := video.file.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_video_bytes:
                raise PreviewError(413, 'upload_too_large', 'The video exceeds the size limit.')
            destination.write(chunk)
    if total == 0:
        raise PreviewError(422, 'empty_video', 'The video is empty.')
    with path.open('rb') as source:
        if source.read(12)[4:8] != b'ftyp':
            raise PreviewError(415, 'unsupported_video', 'The file does not have an MP4 container header.')


def create_app(settings: Settings | None = None, renderer: PreviewRenderer | None = None) -> FastAPI:
    config = settings or Settings()  # type: ignore[call-arg]  # Required token is supplied through the environment.
    service = renderer or Renderer(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Only ASGI lifecycle plumbing is async; routes and rendering are sync.
        service.start()
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title='Framelet', version='0.1.0', lifespan=lifespan)
    app.add_middleware(UploadGuard, settings=config)

    @app.exception_handler(PreviewError)
    def preview_error_handler(request: Request, exc: PreviewError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if getattr(request.state, 'upload_too_large', False):
            return error_response(413, 'upload_too_large', 'The upload exceeds the size limit.')
        return error_response(exc.status_code, 'invalid_request', str(exc.detail))

    @app.exception_handler(RequestValidationError)
    def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, 'invalid_request', 'A video multipart field is required.')

    @app.exception_handler(Exception)
    def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error('Request failed', exc_info=exc)
        return error_response(500, 'internal_error', 'An unexpected error occurred.')

    @app.get('/health/live')
    def live() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready() -> JSONResponse:
        if not service.ready():
            return error_response(503, 'browser_unavailable', 'Chromium is not ready.')
        return JSONResponse({'status': 'ready'})

    @app.post(
        '/v1/preview',
        response_class=Response,
        responses={200: {'content': {'image/png': {}}, 'description': 'The first decoded frame as PNG'}},
        openapi_extra={'security': [{'BearerAuth': []}]},
    )
    def preview(video: Annotated[UploadFile, File(description='MP4 video')]) -> Response:
        with TemporaryDirectory(prefix='framelet-', dir=config.temp_directory) as directory:
            path = Path(directory) / 'input.mp4'
            save_upload(video, path, config)
            frame = service.extract(path)
        return Response(
            frame.png,
            media_type='image/png',
            headers={
                'Cache-Control': 'no-store',
                'X-Frame-Width': str(frame.width),
                'X-Frame-Height': str(frame.height),
                'X-Content-Type-Options': 'nosniff',
            },
        )

    schema = app.openapi()
    schema.setdefault('components', {}).setdefault('securitySchemes', {})['BearerAuth'] = {
        'type': 'http',
        'scheme': 'bearer',
    }
    return app
