import logging
import multiprocessing
import os
import signal
import threading
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import sync_playwright

from framelet.browser import Preview, capture
from framelet.config import Settings
from framelet.errors import PreviewError, busy_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Command:
    path: Path | None = None


@dataclass(frozen=True)
class Reply:
    preview: Preview | None = None
    error_status: int | None = None
    error_code: str = ''
    error_message: str = ''


def run_worker(connection: Connection, settings: Settings) -> None:
    if hasattr(os, 'setsid'):
        os.setsid()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            connection.send(Reply())
            while True:
                command: object = connection.recv()
                if not isinstance(command, Command):
                    break
                try:
                    if command.path is None:
                        context = browser.new_context()
                        context.close()
                        connection.send(Reply())
                    else:
                        connection.send(Reply(preview=capture(browser, command.path, settings)))
                except PreviewError as exc:
                    connection.send(Reply(error_status=exc.status, error_code=exc.code, error_message=exc.message))
                except BrowserTimeout:
                    connection.send(
                        Reply(error_status=504, error_code='render_timeout', error_message='Rendering timed out.')
                    )
                except BrowserError:
                    connection.send(
                        Reply(
                            error_status=503, error_code='browser_unavailable', error_message='Chromium is unavailable.'
                        )
                    )
                    break
                except Exception:
                    logger.exception('Unexpected renderer failure')
                    connection.send(
                        Reply(error_status=500, error_code='render_failed', error_message='Rendering failed.')
                    )
                    break
            browser.close()
    except EOFError:
        pass
    except Exception:
        logger.exception('Renderer process failed')
    finally:
        connection.close()


class Renderer:
    """Own sync Playwright in one process; enforce a hard timeout outside it."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._connection: Connection | None = None
        self._process: BaseProcess | None = None
        self._closed = False

    def start(self) -> None:
        with self._lock:
            self._start()

    def _start(self) -> None:
        if self._closed:
            raise PreviewError(503, 'service_stopping', 'The service is stopping.')
        self._stop()
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=run_worker, args=(child, self.settings), name='framelet-chromium')
        self._connection, self._process = parent, process
        try:
            process.start()
            child.close()
            self._read(self.settings.startup_timeout_seconds)
        except Exception:
            child.close()
            self._stop()
            raise

    def _read(self, timeout: float) -> Reply:
        connection = self._connection
        if connection is None:
            raise PreviewError(503, 'browser_unavailable', 'Chromium is unavailable.')
        try:
            if not connection.poll(timeout):
                raise PreviewError(504, 'render_timeout', 'Rendering exceeded the time limit.')
            reply: object = connection.recv()
        except (EOFError, OSError) as exc:
            raise PreviewError(503, 'browser_unavailable', 'The renderer process disconnected.') from exc
        if not isinstance(reply, Reply):
            raise PreviewError(502, 'invalid_renderer_result', 'The renderer returned an invalid result.')
        if reply.error_status is not None:
            raise PreviewError(reply.error_status, reply.error_code, reply.error_message)
        return reply

    def extract(self, path: Path) -> Preview:
        if not self._lock.acquire(blocking=False):
            raise busy_error()
        try:
            if self._closed:
                raise PreviewError(503, 'service_stopping', 'The service is stopping.')
            if self._process is None or not self._process.is_alive():
                self._start()
            assert self._connection is not None
            self._connection.send(Command(path))
            reply = self._read(self.settings.render_timeout_seconds)
            if reply.preview is None:
                raise PreviewError(502, 'invalid_renderer_result', 'The renderer returned no image.')
            return reply.preview
        except PreviewError as exc:
            if exc.status >= 500:
                self._stop()
            raise
        except (OSError, EOFError) as exc:
            self._stop()
            raise PreviewError(503, 'browser_unavailable', 'The renderer process disconnected.') from exc
        finally:
            self._lock.release()

    def ready(self) -> bool:
        if self._closed:
            return False
        if not self._lock.acquire(blocking=False):
            return self._process is not None
        try:
            if self._process is None or not self._process.is_alive():
                self._start()
            if self._connection is None:
                return False
            self._connection.send(Command())
            self._read(2)
            return True
        except (PreviewError, OSError, EOFError):
            self._stop()
            return False
        finally:
            self._lock.release()

    def _stop(self) -> None:
        process, self._process = self._process, None
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()
        if process is not None and process.pid is not None:
            # Chromium and Playwright children share the worker's process group.
            # Kill the group even if the worker has already exited.
            if hasattr(os, 'killpg'):
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            if process.is_alive():
                process.kill()
            process.join(timeout=3)
            process.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop()
