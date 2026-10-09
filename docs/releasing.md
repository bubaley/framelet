# Maintaining releases

GitHub Actions runs lint, type checks, unit/browser tests, a Linux Docker build, and a runtime smoke test.
After successful checks on `main`, Python Semantic Release determines the next version from Conventional Commits,
updates `pyproject.toml`, `uv.lock`, and `CHANGELOG.md`, pushes a version commit/tag, and creates a GitHub Release.
The same workflow builds the tagged runtime image and publishes it only to Docker Hub.

## Commit conventions

- `fix:` and `perf:` publish a patch.
- `feat:` publishes a minor release.
- Breaking changes publish a major release after 1.0; before 1.0 they publish a minor release.
- Documentation, tests, and maintenance commits alone do not publish a new release.

The release commit uses `[skip ci]`. Publishing runs in the existing workflow rather than relying on another
workflow being triggered by `GITHUB_TOKEN`. Outdated workflow commits are not released, and release jobs are serialized.

## Repository configuration

- `DOCKERHUB_PASSWORD` secret: Docker Hub access token with write permission for the image repository.
- `DOCKERHUB_USERNAME` secret: optional; defaults to the GitHub repository owner.
- `DOCKERHUB_IMAGE` variable: optional; defaults to `<GitHub owner>/framelet`.
- GitHub Actions needs `contents: write` to push version commits/tags and create releases.
- If branch protection is enabled later, configure an appropriate release-bot exception or token before enabling releases.

The image repository should be public. Release images receive `<version>` and `latest` tags, with OCI source/version
labels. Only tested Linux amd64 images are published. No images are published to GHCR or packages to PyPI.

## Recovery

To retry publishing an existing release, run **CI and releases** manually and set `release_tag` to the existing tag
(for example, `v0.1.0`). The workflow checks out and tests that exact tag, verifies that the tag matches the package
version, and publishes its image without creating a second release. A manual run with no tag retries the normal
`main` release path. Do not change an existing release tag to point to a different commit.

References: [Python Semantic Release](https://python-semantic-release.readthedocs.io/en/stable/configuration/automatic-releases/github-actions.html),
[uv lock synchronization](https://python-semantic-release.readthedocs.io/en/stable/configuration/configuration-guides/uv_integration.html).
