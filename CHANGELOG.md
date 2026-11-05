# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

The 2026 rebuild of the course project into a parking-availability data system. Design in
`docs/REFACTOR_PLAN.md`, decisions in `docs/DECISIONS.md`.

### Added

- Backend (`backend/`, package `campus_parking`): one FastAPI app with a `/api/v1` surface, app
  factory and lifespan, pydantic-settings, JSON logs with request ids, Prometheus metrics,
  `/healthz` and `/readyz`, RFC 9457 problem details, three API keys (ingest, admin, operator)
  and graceful draining. Alembic migrations 0001 to 0003.
- Registry: admin upserts for lots (with an effective-dated capacity history), gates and
  students; public `GET /lots`.
- Ingestion: `POST /api/v1/events` with the GateEvent v1 contract, batches of 1 to 1000,
  idempotency by event id and payload hash, rejects with a reason, event-time bounds,
  per-batch `ingest_batches` metadata and backpressure (429 and 503 with `Retry-After`).
- Occupancy: stays re-derived per lot, student and day; an exact per-lot counter with a version;
  Hypothesis property tests for order, duplicates and batch splits.
- Real time: WebSocket `/api/v1/live/availability` with full snapshots, a per-process version
  poller and a 15 s heartbeat.
- Worker: a Postgres run queue (`pipeline_runs`, `FOR UPDATE SKIP LOCKED`), a scheduler, the
  `daily` job with `close_day` children (reconciliation at local midnight, drift recount,
  `service_days`) and a Parquet export per service date with an archive contract, manifest and
  atomic swap.
- Warehouse: dbt-postgres project with staging views, an SCD2 lot dimension, three incremental
  facts, four marts with enforced contracts, tests and source freshness; `/api/v1/reports/*`
  served from the marts with an ETag cache per warehouse run.
- Generator (`generator/`, `turnstile-gen`): the stand-in for the face-ID turnstiles, an API
  client with models generated from the OpenAPI contract; `bootstrap`, `backfill`, `live` and
  `run`, with demand patterns, holidays and injected faults.
- Clients: npm workspace with `packages/api-client` (types generated from `backend/openapi.json`,
  a shared live-availability client, React Query keys), `apps/web` (React + Vite: Vagas, lot
  detail, Relatórios, Operador) and `apps/mobile` (Expo SDK 58 for Expo Go: Vagas, lot detail,
  Relatórios), both with the UNDB visual identity and the unofficial-project notice.
- Docker: multi-stage non-root images for the backend, generator and web (nginx); `compose.yaml`
  with postgres, migrate, api, worker, generator and web; `make demo` and `make smoke`.
- CI: backend lint and tests, Postgres integration tests, JS checks and builds, generator lint
  and tests, an end-to-end job, a compose smoke and the `diagrams` check; `security.yml` with
  pip-audit, npm audit and zizmor; Dependabot with monthly minor and patch groups and a workflow
  that auto-merges them once the required checks pass.
- Docs: README with screenshots, D2 diagrams (architecture, data flow, three user flows, three
  ERDs) rendered to committed SVGs, `docs/flow.md`, API notes, data model, running guide, brand
  notes, decision log and this changelog. MIT license.

### Changed

- The two 2024 services (turnstile simulator and parking API) became one backend and a separate
  generator that integrates only through the API.
- Postgres replaced the Parquet files as the store; Parquet is now the daily archive.
- Free spaces come from an incremental counter instead of re-reading and re-pairing every event
  on each request.
- Python 3.12 with uv, ruff and mypy strict replaced `requirements.txt`.
- The mobile app moved from Expo SDK 57 to SDK 58: React Native 0.88.1, React 19.3.0 across the
  npm workspace, Jest 30. The npm audit allowlist is empty again, since both Expo advisories
  (zod 3 in the Expo CLI, decode-uri-component in expo-router) left the dependency tree.

### Removed

- `undb_faceid/` and `api/` (the 2024 services), tracked `__pycache__` files and the generated
  Parquet data.

## [0.1.0] - 2024-06

The original UNDB mobile-programming course project.

### Added

- `undb_faceid`: a FastAPI simulator of the face-ID turnstile system that wrote Faker data
  (students, two lots, turnstiles, entry records) to Parquet files and served them over GET
  endpoints.
- `api`: a FastAPI parking API whose `/vagas/{estacionamento_id}` pulled the simulator's data
  over HTTP, paired entries and exits per student with pandas and returned the free spaces.
- A mobile front-end, written by a teammate, lived outside this repository.
