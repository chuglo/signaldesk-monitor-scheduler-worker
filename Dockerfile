FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 HOME=/tmp PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=service-kit pyproject.toml README.md /build/signaldesk-service-kit/
COPY --from=service-kit src /build/signaldesk-service-kit/src
COPY pyproject.toml README.md uv.lock /build/signaldesk-monitor-scheduler-worker/
COPY src /build/signaldesk-monitor-scheduler-worker/src
RUN python -m pip install uv==0.11.31 \
    && cd /build/signaldesk-monitor-scheduler-worker \
    && UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --locked --no-dev --no-editable \
    && rm -rf /build
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/signaldesk-monitor-scheduler-worker"]
