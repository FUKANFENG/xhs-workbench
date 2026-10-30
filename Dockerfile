# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS node
FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd AS base
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --create-home app
COPY --from=node /usr/local/ /usr/local/
RUN pip install --no-cache-dir uv==0.11.2
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers UV_HTTP_TIMEOUT=120 PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000
RUN uv pip install --system patchright==1.58.2 pyee==13.0.1 greenlet==3.3.2 typing-extensions==4.15.0 \
    && python -c "from pathlib import Path; p=Path('/etc/apt/sources.list.d/debian.sources'); p.write_text(p.read_text().replace('http://deb.debian.org', 'https://deb.debian.org'))"
RUN python -m patchright install-deps chromium && rm -rf /var/lib/apt/lists/*
RUN python -m patchright install chromium \
    && chmod -R a+rX /opt/browsers
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 UV_PYTHON_DOWNLOADS=never

FROM base AS builder
COPY requirements-lock.txt /build/requirements-lock.txt
COPY requirements-publish.lock /build/requirements-publish.lock
COPY docker/build_runtimes.py /build/build_runtimes.py
RUN python /build/build_runtimes.py

FROM base AS runtime
LABEL org.opencontainers.image.title="XHS Workbench" \
      org.opencontainers.image.source="https://github.com/FUKANFENG/xhs-workbench"
WORKDIR /app
COPY --from=builder --chown=10001:10001 /opt/runtimes /opt/runtimes
COPY --chown=10001:10001 webapp /app/webapp
COPY --chown=10001:10001 integration /app/integration
COPY --chown=10001:10001 skills /app/skills
COPY --chown=10001:10001 docker/entrypoint.py /app/docker/entrypoint.py
COPY --chown=10001:10001 docker/browser_smoke.py /app/docker/browser_smoke.py
COPY --chown=10001:10001 LICENSE THIRD_PARTY_NOTICES.md /app/
RUN mkdir /data && chown 10001:10001 /data /app
ENV XHS_LOCAL_DATA=/data \
    XHS_RUNTIME_ROOT=/opt/runtimes/Spider_XHS \
    XHS_MEDIA_ROOT=/opt/runtimes/MediaCrawler \
    XHS_PUBLISH_ROOT=/opt/runtimes/SocialAutoUpload \
    XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/config MPLCONFIGDIR=/tmp/matplotlib
USER 10001:10001
EXPOSE 8777
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/opt/runtimes/Spider_XHS/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8777/api/health', timeout=3).read()"]
ENTRYPOINT ["/opt/runtimes/Spider_XHS/.venv/bin/python", "/app/docker/entrypoint.py"]

FROM runtime AS test
COPY --chown=10001:10001 tests /app/tests
RUN ["/opt/runtimes/Spider_XHS/.venv/bin/python", "-m", "pytest", "-q", "/app/tests"]
