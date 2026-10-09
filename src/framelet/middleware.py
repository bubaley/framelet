import asyncio
import secrets
import threading
import time

from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from framelet.config import Settings


class UploadGuard:
    """Authenticate and reserve capacity before multipart parsing or spooling."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._slot = threading.BoundedSemaphore(1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope['path'].rstrip('/') != '/v1/preview' or scope['method'] != 'POST':
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        expected = f'Bearer {self.settings.api_token.get_secret_value()}'
        if not secrets.compare_digest(headers.get('authorization', '').encode(), expected.encode()):
            await self._error(scope, receive, send, 401, 'unauthorized', 'A valid bearer token is required.')
            return
        if headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'multipart/form-data':
            await self._error(scope, receive, send, 415, 'unsupported_media_type', 'Use multipart/form-data.')
            return
        length = headers.get('content-length')
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError
            except ValueError:
                await self._error(scope, receive, send, 400, 'invalid_content_length', 'Invalid Content-Length.')
                return
            if size > self.settings.max_request_bytes:
                await self._error(scope, receive, send, 413, 'upload_too_large', 'The upload exceeds the size limit.')
                return
        if not self._slot.acquire(blocking=False):
            await self._error(scope, receive, send, 429, 'service_busy', 'Another upload is being processed.')
            return
        received = 0
        deadline = time.monotonic() + self.settings.upload_timeout_seconds

        async def bounded_receive() -> Message:
            nonlocal received
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                scope.setdefault('state', {})['upload_timeout'] = True
                raise MultiPartException('Uploading exceeded the time limit.') from exc
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > self.settings.max_request_bytes:
                    scope.setdefault('state', {})['upload_too_large'] = True
                    # Starlette's multipart parser closes already-spooled files
                    # when this exception is raised, including chunked uploads.
                    raise MultiPartException('The upload exceeds the size limit.')
            return message

        try:
            await self.app(scope, bounded_receive, send)
        finally:
            self._slot.release()

    @staticmethod
    async def _error(scope: Scope, receive: Receive, send: Send, status: int, code: str, message: str) -> None:
        response = JSONResponse(
            {'error': {'code': code, 'message': message}},
            status_code=status,
            headers={'Retry-After': '1'}
            if status == 429
            else {'WWW-Authenticate': 'Bearer'}
            if status == 401
            else None,
        )
        await response(scope, receive, send)
