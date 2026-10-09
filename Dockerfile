FROM ghcr.io/astral-sh/uv:0.9.2@sha256:6dbd7c42a9088083fa79e41431a579196a189bcee3ae68ba904ac2bf77765867 AS uv
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
RUN .venv/bin/python -m playwright install --with-deps --only-shell chromium \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* /root/.cache
COPY src ./src
COPY README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable \
    && groupadd --gid 10001 framelet \
    && useradd --uid 10001 --gid framelet --create-home framelet

FROM base AS test
RUN uv sync --locked
COPY tests ./tests
COPY scripts ./scripts
RUN ruff check . && ruff format --check . && mypy src tests scripts && pytest

FROM base AS runtime
RUN rm /usr/local/bin/uv
USER framelet
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"
CMD ["uvicorn", "framelet.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
