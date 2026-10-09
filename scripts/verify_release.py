"""Validate a release tag against project metadata and expose CI outputs."""

import os
import re
import tomllib
from pathlib import Path


def main() -> None:
    tag = os.environ.get('RELEASE_TAG', '')
    version = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
    if not isinstance(version, str) or not re.fullmatch(r'v\d+\.\d+\.\d+', tag) or tag != f'v{version}':
        raise SystemExit('Release tag must match the stable project version')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with Path(output).open('a') as destination:
            destination.write(f'version={version}\ntag={tag}\n')


if __name__ == '__main__':
    main()
