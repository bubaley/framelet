# Maintaining releases

GitHub Actions runs lint and type checks, then prepares the next version from Conventional Commits locally.
Python Semantic Release updates `pyproject.toml`, `uv.lock`, and `CHANGELOG.md` and creates a local version commit/tag.
Nothing is pushed at this stage. The workflow then tests the versioned source in a Linux container, builds the
runtime image once, and smoke-tests that exact image with production container restrictions.
Only after successful tests are the version commit/tag pushed atomically, followed by the same image to
Docker Hub. The GitHub Release is created last. A failed build or container test therefore does not announce a release.

## Build cache

BuildKit uses separate GitHub Actions cache scopes for test and runtime stages, both with `mode=max`; cache export failure does not block a successful build.
Both builds import both scopes, and share one Buildx builder within the job. Test dependencies cannot overwrite
the runtime cache. Locked dependencies are exported to a stable requirements file before the heavy dependency
and Chromium layers; version-only and application-code changes do not invalidate those layers. The browser layer
changes when locked runtime dependencies, the Dockerfile, or pinned base images change. uv also uses a local
BuildKit cache mount, and `setup-uv` caches host tooling. Cache mounts are a local speedup; cross-run reuse relies
on exported layer caches, not on cache-mount persistence. Cold builds still work without any cache.
Runtime testing and publishing reuse the same locally loaded image; publication does not trigger a second build.

## Commit conventions

- `fix:` and `perf:` publish a patch.
- `feat:` publishes a minor release.
- Breaking changes publish a major release after 1.0; before 1.0 they publish a minor release.
- Documentation, tests, and maintenance commits alone do not publish a new release.

The release commit uses `[skip ci]`. Image publication and release creation run in the existing workflow rather than relying on another
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
version, and publishes its image, then creates or refreshes the matching GitHub Release without bumping the version. A manual run with no tag retries the normal
`main` release path. Retrying an older tag does not overwrite `latest`. Do not change an existing release tag to point to a different commit.

References: [Python Semantic Release](https://python-semantic-release.readthedocs.io/en/stable/configuration/automatic-releases/github-actions.html),
[uv lock synchronization](https://python-semantic-release.readthedocs.io/en/stable/configuration/configuration-guides/uv_integration.html).

If the final Git push fails (for example, because `main` advanced), no image or GitHub Release is published.
Retry the current `main` workflow; do not force-push a version commit or move an existing tag. If a tag was pushed
but Docker publication or release creation failed, recover using `release_tag`.
