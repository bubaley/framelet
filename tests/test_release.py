import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_release.py'


@pytest.mark.parametrize(('tag', 'valid'), [('v0.1.0', True), ('v0.2.0', False), ('v0.1.0-rc.1', False)])
def test_release_tag_matches_package(tmp_path: Path, tag: str, valid: bool) -> None:
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "0.1.0"\n')
    output = tmp_path / 'outputs'
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={**os.environ, 'RELEASE_TAG': tag, 'GITHUB_OUTPUT': str(output)},
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == valid
    if valid:
        assert output.read_text() == 'version=0.1.0\ntag=v0.1.0\n'
    else:
        assert not output.exists()
