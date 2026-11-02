# syntax=docker/dockerfile:1
# One image for the API (serve), the batch worker (worker) and the one-shot migrate. Build context:
# backend/. Base images are pinned by tag and digest; Dependabot or a manual bump moves both.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# Dependencies first (cached until uv.lock changes), then the project itself. The project is
# installed editable from /app/src, so the CLI finds /app/alembic.ini as it does in the repo.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group dbt --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group dbt

FROM python:3.12.16-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent app \
    && mkdir -p /data/lake \
    && chown app:app /data/lake
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY alembic.ini ./
COPY alembic ./alembic
COPY warehouse ./warehouse
RUN mkdir /app/warehouse/target && chown app:app /app/warehouse/target
# dbt writes target/ under the project (the worker reads run_results.json there; compose mounts a
# tmpfs on it) and its logs under DBT_LOG_PATH. The profile reads DBT_PG_* only: no secret here.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PARKING_LAKE_DIR=/data/lake \
    PARKING_DBT_PROJECT_DIR=/app/warehouse \
    PARKING_WORKER_HEARTBEAT_FILE=/tmp/worker-heartbeat \
    DBT_LOG_PATH=/tmp/dbt-logs \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false
USER 10001:10001
EXPOSE 8000
# The API's liveness; compose replaces it for the worker (heartbeat file age) and the migrate job.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
ENTRYPOINT ["campus-parking"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
