import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
from pydantic import SecretStr

from framelet.config import Settings
from framelet.errors import PreviewError
from framelet.worker import Renderer, Reply


def stalled_worker(connection: Connection, settings: Settings) -> None:
    import os

    if hasattr(os, 'setsid'):
        os.setsid()
    connection.send(Reply())
    connection.recv()
    time.sleep(60)


def test_hard_timeout_kills_process_and_can_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr('framelet.worker.run_worker', stalled_worker)
    renderer = Renderer(Settings(api_token=SecretStr('test-token-with-enough-length'), render_timeout_seconds=0.2))
    renderer.start()
    process = renderer._process
    assert process is not None
    pid = process.pid
    start = time.monotonic()
    with pytest.raises(PreviewError) as error:
        renderer.extract(tmp_path / 'unused.mp4')
    assert error.value.status == 504
    assert time.monotonic() - start < 3
    assert renderer._process is None
    renderer.start()
    assert renderer._process is not None
    assert renderer._process.pid != pid
    renderer.close()
    assert not renderer.ready()
    with pytest.raises(PreviewError) as stopped:
        renderer.extract(tmp_path / 'unused.mp4')
    assert stopped.value.code == 'service_stopping'
