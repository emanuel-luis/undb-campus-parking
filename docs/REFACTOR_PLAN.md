# Refactor plan

Design for rebuilding the 2024 UNDB course project (a turnstile simulator plus a parking API, both
FastAPI over Parquet) into a parking-availability data system. Produced by an `architect` review on
2026-10-28; choices are logged in [DECISIONS.md](DECISIONS.md). This file is the implementation
contract: when code and plan disagree, fix one of them in the same PR.

## Shape

- **Backend** (`backend/`, uv project `smart-parking`, package `smart_parking`): one FastAPI app,
  one `/api/v1` surface, one Alembic history, one image, run as two processes: `api` (uvicorn) and
  `worker` (reconciliation, Parquet export, dbt refresh; every run in `pipeline_runs`).
- **Generator** (`generator/`, uv project `turnstile-generator`, package `turnstile_generator`): the
  stand-in for the face-ID turnstile system. Separate image, no database access, no backend imports.
  An API client only, with Pydantic models generated from `backend/openapi.json`. Creates the
  registry through admin endpoints, then posts events through `POST /api/v1/events`.
- **Clients** (npm workspaces): `packages/api-client` (types generated from `backend/openapi.json`,
  `openapi-fetch` client, live socket helper), `apps/web` (React + Vite + TS), `apps/mobile`
  (Expo SDK 57 + TS, runs in Expo Go). UNDB visual identity (see `docs/BRAND.md`).
- **Warehouse** (`backend/warehouse/`, dbt-postgres): star schema in schema `warehouse` of the same
  Postgres; the reports API reads only from there.

## Layout

```
backend/
  pyproject.toml uv.lock alembic.ini Dockerfile .dockerignore
  openapi.json                 committed contract (source for generator models and TS client)
  alembic/versions/            0001_registry_ingestion_occupancy, 0002_jobs
  warehouse/                   dbt project
  src/smart_parking/
    main.py        create_app(settings) + lifespan (engine, hub, poller, draining flag)
    cli.py         smart-parking serve | worker [--once JOB] | migrate | openapi | close-day | export | warehouse
    core/          settings, db (async engine, UTC type, insert_ignore), logging (JSON),
                   middleware (request id, HTTP metrics), problems (RFC 9457), security (3 API-key
                   deps), clock (get_now, service_date, cutoff), health router, metrics, openapi.py
    registry/      Lot, LotCapacityHistory, Gate, Student; upserts; public GET /lots; admin PUTs
    ingestion/     contract.py (GateEvent v1), validation.py (pure), GateEventRow, IngestBatch,
                   IngestReject; IngestionService (+ concurrency gate); POST /events; rejects and
                   ingestion health (operator)
    occupancy/     Stay, LotOccupancy; pairing.py (pure); apply_touched, availability, reconcile_day
    realtime/      hub (per-process fan-out), poller (version poll, heartbeat, gauges), WS router
    jobs/          PipelineRun, ServiceDay; queue (enqueue, claim), scheduler, worker, close_day,
                   export + archive_contract, warehouse (dbt subprocess + run_results); operator runs
    reports/       Core Tables over schema warehouse; cache per warehouse run; report routes
  tests/unit tests/api tests/integration
generator/
  pyproject.toml uv.lock Dockerfile .dockerignore
  src/turnstile_generator/
    api_models.py  GENERATED from ../backend/openapi.json (datamodel-code-generator; never edited)
    client.py sender.py registry.py demand.py scenarios.py faults.py stream.py
    bootstrap.py backfill.py live.py heartbeat.py settings.py logging.py cli.py
  tests/
apps/web apps/mobile packages/api-client
docs/ DECISIONS.md REFACTOR_PLAN.md BRAND.md flow.md api.md data-model.md diagrams/
Makefile compose.yaml .env.example scripts/smoke.py .github/ README.md CLAUDE.md CHANGELOG.md LICENSE
```

Inside each backend module: `router` (HTTP only) → `service` (domain logic) → `repository`
(SQLAlchemy 2 async), plus `models`, `schemas` and a pure `domain`/`pairing`/`validation` file
where logic is testable without a database. Dependencies are injected with `Depends`; domain
errors are mapped to problem details once, in `core/problems.py`.

The old `undb_faceid/` and `api/` folders are deleted in PR 3, when backend and generator cover
them.

## Data model

Alembic owns schema `public`; dbt owns `warehouse` and `warehouse_staging`.

### 0001_registry_ingestion_occupancy

| Table | Columns, keys, constraints, indexes |
| --- | --- |
| `lots` | `lot_id text PK` (slug, e.g. `principal`), `name`, `created_at`, `updated_at` |
| `lot_capacity_history` | PK (`lot_id`, `valid_from date`), `capacity int CHECK > 0`; current capacity = latest `valid_from` <= today (local) |
| `gates` | `gate_id text PK`, `lot_id FK`, `direction CHECK IN (entry, exit)`, `name`, `last_event_at`, `last_received_at`, `created_at`, `updated_at` |
| `students` | `student_ref text PK CHECK (~ '^stu_[0-9a-f]{12}$')`, `created_at`, `updated_at`; pseudonymous, no names or emails |
| `ingest_batches` | `batch_id uuid PK`, `received_at`, `items`, `accepted`, `duplicates`, `rejected`, `avg_lag_s`, `max_lag_s`, `duration_ms`; index (`received_at`) |
| `gate_events` | `event_id uuid PK`, `schema_version`, `gate_id FK`, `lot_id FK`, `direction`, `student_ref FK`, `occurred_at`, `service_date` (local date), `received_at`, `batch_id FK`, `payload_sha256`; indexes (`lot_id`, `student_ref`, `service_date`), (`service_date`, `lot_id`), (`received_at`); trigger blocks UPDATE and DELETE (append-only) |
| `ingest_rejects` | `reject_id bigserial PK`, `batch_id FK`, `received_at`, `event_id`, `service_date`, `lot_id`, `gate_id` (nullable), `reason CHECK IN (schema_invalid, unknown_gate, unknown_student, lot_mismatch, direction_mismatch, in_future, too_old, event_id_conflict)`, `detail`, `payload jsonb` (capped at 4 KB); index (`received_at` DESC) |
| `stays` | `stay_id uuid PK` (= coalesce(entry, exit event id)), `lot_id`, `student_ref`, `service_date`, `entry_event_id UNIQUE NULL`, `exit_event_id UNIQUE NULL`, `entered_at`, `exited_at`, `ended_at`, `status CHECK IN (open, closed, missing_exit, exit_only, closed_by_reconciliation)`, `duration_s`, `updated_at`; indexes (`lot_id`, `student_ref`, `service_date`), (`updated_at`), partial (`lot_id`) WHERE status = 'open' |
| `lot_occupancy` | `lot_id PK FK`, `occupied CHECK >= 0`, `version bigint`, `updated_at`, `last_event_at`; created with the lot |

### 0002_jobs

- `pipeline_runs`: `run_id uuid PK`, `parent_run_id`, `job CHECK IN (daily, close_day, warehouse)`,
  `mode`, `trigger CHECK IN (schedule, operator, cli)`, `service_date NOT NULL`,
  `status CHECK IN (queued, running, succeeded, failed)`, `requested_at`, `started_at`,
  `finished_at`, `rows_read`, `rows_written`, `rows_rejected`, `metrics jsonb`, `error`; partial
  UNIQUE (`job`, `service_date`) WHERE status IN (queued, running); index (`requested_at`) WHERE
  status = 'queued'.
- `service_days`: `service_date PK`, `closed_at`, `run_id`.

### Registry rules

Lot and gate ids are stable natural keys; no deletes (append-only events reference them). A gate's
`lot_id` and `direction` cannot change once it has events (409 `gate-immutable`). A lot PUT
replaces its name and its whole capacity history (same body, same state).

## Ingestion

One transaction per batch, about ten statements whatever the batch size:

1. `SET LOCAL lock_timeout`.
2. Load referenced gates and students (`= ANY(:ids)`).
3. Validate each item (pure `validation.py`, in this order): schema → `unknown_gate` →
   `unknown_student` → `lot_mismatch` → `direction_mismatch` → `occurred_at > received_at +
   PARKING_MAX_CLOCK_SKEW_S` (300 s) is `in_future` → `occurred_at < received_at −
   PARKING_MAX_EVENT_AGE_DAYS` (120 days: the 90-day backfill plus 30 days) is `too_old`.
4. Lock the touched lots, ordered by `lot_id` (no deadlocks).
5. Insert events `ON CONFLICT DO NOTHING RETURNING`; same id and same payload hash is a duplicate,
   same id with a different hash is rejected `event_id_conflict`.
6. Rebuild the stays of every touched (lot, student, service day).
7. `UPDATE lot_occupancy SET occupied = occupied + Δopen, version = version + 1`.
8. Update `gates.last_event_at`, `gates.last_received_at`.
9. Insert rejects and the `ingest_batches` row.
10. Commit.

Backpressure: 429 `ingest-busy` with `Retry-After: 1` when the per-process semaphore
(`PARKING_INGEST_MAX_CONCURRENCY` = 4, non-blocking) is full or `lock_timeout` (2 s) fires; 503 with
`Retry-After: 5` while draining or when the database is down; 422 only for whole-batch errors (not
an array, more than 1000 items). Item errors become rejects in a 202 response.

### Pairing (pure `pair_stays(events, day_closed, cutoff)`)

Sort a student-day's events by (`occurred_at`, `event_id`):

- entry with nothing open opens a stay;
- entry while open: the previous stay becomes `missing_exit`, ended at the new entry;
- exit while open closes it (`closed`, duration);
- exit with nothing open is `exit_only` (anomaly, no occupancy effect);
- at the end a still-open stay is `open`, or `closed_by_reconciliation` at the cutoff if the day is
  closed.

Each touched student-day is re-derived from all its events, so arrival order cannot matter: an entry
that arrives after its exit turns `exit_only` into `closed`. `occupied` stores the exact count of
open stays (never clamped); responses return `free = max(capacity − occupied, 0)` and
`over_capacity`. Known limit: pairing is per service day, so an overnight stay splits into a
reconciled stay and an `exit_only` (the campus closes at night).

### Reconciliation

`close_day(d)` for d < today (local), run by the worker: lock lots; set d's open stays to
`closed_by_reconciliation` with `ended_at` = d+1 00:00 local; decrement occupancy; write
`service_days`; recount open stays per lot and fix and record drift; export. Idempotent. A date
that receives events after its close is processed again by the next `daily` (bounded by the
120-day age limit). Run metrics: events read, stays by status, closed by reconciliation, rejects,
late events, drift, exported rows, sha256.

### Property tests (hypothesis)

1. Any permutation, duplicates and batch split give the same stays and occupancy.
2. Σ Δ = open count = last-write-wins count (latest event per student-day is an entry).
3. `occupied >= 0` after every prefix.
4. Each event in exactly one stay; entries = closed + missing_exit + open +
   closed_by_reconciliation; exits = closed + exit_only.
5. Stays of a student-day do not overlap; durations >= 0.
6. Occupancy from stay intervals at time t equals the LWW count over events <= t (oracle for the
   dbt step function).
7. `close_day` is idempotent.
8. Re-sending any subset changes nothing.
9. 0 <= free <= capacity.
10. An item's validation outcome does not depend on its position in the batch.

## API surface

Auth header `X-API-Key`. `-` anonymous, `I` ingest key, `A` admin key, `O` operator key.

| Method and path | Auth | Request → response | Codes |
| --- | --- | --- | --- |
| GET /healthz, /readyz | - | `HealthStatus`, `Readiness` | 200 / 503 (database check; 503 while draining) |
| GET /metrics | - | Prometheus text | 200 |
| GET /api/v1/lots | - | `list[LotOut]` (gates, capacity history, current capacity) | 200 |
| GET /api/v1/availability | - | `AvailabilitySnapshot` | 200 |
| GET /api/v1/lots/{lot_id}/availability | - | `LotAvailability` | 200, 404 |
| WS /api/v1/live/availability | - | `AvailabilitySnapshot` messages | close 1001 / 1013 |
| GET /api/v1/reports/{freshness,summary,daily,hourly,weekday,occupancy,dwell,quality} | - | `ReportFreshness`, `list[LotReportSummary]`, `list[LotDailyRow]`, `list[LotHourlyRow]`, `list[WeekdayRow]`, `list[OccupancySlot]`, `list[DwellBucketRow]`, `list[QualityDailyRow]` | 200, 304, 404, 422, 503 |
| POST /api/v1/events | I | array of `GateEvent` (1..1000) → `IngestResult{batch_id, received, accepted, duplicates, rejected, rejects[]}` | 202, 401, 422, 429, 503 |
| PUT /api/v1/admin/lots/{lot_id} | A | `LotUpsert{name, capacity_history[]}` → `LotOut` | 201 / 200, 401, 422 |
| PUT /api/v1/admin/gates/{gate_id} | A | `GateUpsert{lot_id, direction, name}` → `GateOut` | 201 / 200, 401, 404, 409, 422 |
| POST /api/v1/admin/students/bulk | A | `StudentBulkUpsert{student_refs: 1..1000}` → `BulkUpsertResult{created, unchanged}` | 200, 401, 422 |
| GET /api/v1/operator/ingestion/health | O | `window_min` (<= 240) → `IngestionHealth` | 200, 401 |
| GET /api/v1/operator/rejects | O | `since?`, `reason?`, `limit`, `cursor` → `RejectPage` | 200, 401 |
| GET /api/v1/operator/runs, /runs/{run_id} | O | `list[PipelineRunOut]`, `PipelineRunOut` | 200, 401, 404 |
| POST /api/v1/operator/runs | O | `RunRequest{job, service_date?, mode?}` → `PipelineRunOut` (queued) | 202, 401, 409, 422 |

`IngestionHealth`: `per_minute[{minute, accepted, duplicates, rejected}]` (from `ingest_batches`),
`rejects_by_reason`, `lag{avg_s, max_s}`, `gates[{gate_id, lot_id, last_event_at,
last_received_at}]`, `last_batch_at`.

Reports: default range the last 30 days, max 366 (hourly max 31); without `lot_id` one series per
lot (percentiles are never summed across lots); `ETag` from the warehouse run id and the query,
`Cache-Control: public, max-age=60`, 304 on match; 503 `warehouse-not-ready` when marts are
missing.

Event contract (`ingestion/contract.py`, frozen, `extra="forbid"`; `core/openapi.py` registers it
in the OpenAPI components and points the request body at it):

```json
{"schema_version": 1, "event_id": "<uuid>", "gate_id": "principal-entrada", "lot_id": "principal",
 "direction": "entry", "student_ref": "stu_8f3a2c1d9e04", "occurred_at": "2026-10-28T11:02:13.412Z"}
```

Problems: RFC 9457 `application/problem+json` with `type` (`/problems/<code>`), `title`,
`status`, `detail`, `instance`, `code`, `request_id`, `errors?`. Codes: `lot-not-found`,
`gate-immutable`, `run-already-active`, `day-not-closed`, `invalid-date-range`, `ingest-busy`,
`service-draining`, `database-unavailable`, `warehouse-not-ready`, `unauthorized`,
`validation-error`, `internal-error`.

## Real-time

WebSocket `/api/v1/live/availability`; every message is a full `AvailabilitySnapshot{generated_at,
lots: [LotAvailability{lot_id, name, capacity, occupied, free, occupancy_pct, over_capacity,
version, updated_at, last_event_at}]}`, the same model as `GET /availability`. Each API process
polls `lot_occupancy` versions every second (2 rows) and broadcasts on change, plus a snapshot
every 15 s as heartbeat and repair. This sees writes from every replica and from the worker
(close_day) without LISTEN/NOTIFY. Hub: per-connection queue of 4, drop oldest (lossless, since
messages are full snapshots); snapshot on connect; uvicorn pings every 20 s; close 1001 on
shutdown, 1013 over `PARKING_LIVE_MAX_CONNECTIONS`. Client (`packages/api-client/src/live.ts`,
shared by web and mobile): backoff 1 s to 30 s with jitter, reconnect after 45 s of silence,
ignore older versions, REST fallback every 10 s while down, close when hidden or in background.

## Worker

`smart-parking worker`: one asyncio process, one job at a time.

- Claim loop every `PARKING_WORKER_POLL_S` (2 s): `UPDATE pipeline_runs SET status = 'running' …
  WHERE run_id = (SELECT … WHERE status = 'queued' ORDER BY requested_at FOR UPDATE SKIP LOCKED
  LIMIT 1) RETURNING`.
- Scheduler every 30 s, enqueueing with `ON CONFLICT DO NOTHING` on the partial unique index:
  `warehouse/incremental` every `PARKING_WAREHOUSE_INTERVAL_MIN` (15); after
  `PARKING_DAILY_RUN_AT` (00:30 local), `daily` for yesterday = `close_day` (with export) for every
  pending date as child runs, then `warehouse/full` (`dbt source freshness` + `dbt build
  --full-refresh`).
- Operator endpoints only enqueue (`POST /operator/runs`).
- dbt runs through `asyncio.create_subprocess_exec`; `run_results.json` metrics go into the run
  row. pyarrow work runs in `asyncio.to_thread`.
- At startup, leftover `running` rows become `failed` ("abandoned"). SIGTERM terminates dbt,
  marks the run failed ("interrupted"), exits within 30 s. Heartbeat file
  `/tmp/worker-heartbeat` for the Docker HEALTHCHECK; Prometheus on
  `PARKING_WORKER_METRICS_PORT` (9100).

## Archive and warehouse

- Archive: `PARKING_LAKE_DIR/raw/{gate_events,ingest_rejects}/service_date=YYYY-MM-DD/part-00000.parquet`
  plus `_manifest.json` (dataset, schema_version, run_id, row_count, source_row_count, sha256,
  columns). Contract check before publishing: every row validates against `GateEventArchiveRow` v1,
  the arrow schema equals the contract's, `event_id` unique, every `service_date` = d, row count =
  source. Written to a temporary directory, then swapped in with two renames; reruns compare a
  row-level hash. No DuckDB.
- dbt (`backend/warehouse/`, `dbt` dependency group, `profiles.yml` from `DBT_PG*` env derived
  from `PARKING_DATABASE_URL`):

| Model | Grain | Materialization |
| --- | --- | --- |
| `stg_parking__{gate_events,stays,lots,lot_capacity_history,gates,students,ingest_rejects}` | source row | view in `warehouse_staging`; freshness on `received_at` (warn 30 min, error 2 h) |
| `int_stays_classified` | stay | view |
| `dim_date` (2024 to 2030, holidays seed, pt-BR weekday), `dim_time` (96 slots) | day / 15-min slot | table |
| `dim_lot` | lot version (SCD2 from `lot_capacity_history`) plus a −1 unknown member | table |
| `dim_gate`, `dim_student` (md5 key only, first/last seen) | gate / student | table |
| `fct_gate_event` | accepted event | incremental delete+insert, `unique_key = event_id`, watermark `received_at` − 10 min |
| `fct_stay` | stay (duration, closed-by-reconciliation flag, status) | incremental delete+insert on (`lot_id`, `service_date`) partitions picked by `stays.updated_at` |
| `fct_occupancy_snapshot` | lot × day × 15-min slot (occupied end/max/avg, capacity, free end/min, seconds full) | incremental, same partitions plus today; step function over stays |
| `mart_lot_daily` | lot × day: entries, exits, unique students, entries 7-day MA, avg/median/p90 stay, peak and peak slot, min free, minutes full, turnover, quality counts | table, contract enforced |
| `mart_lot_hourly` | lot × day × hour: entries, exits, avg free, avg/max occupied | table, contract enforced |
| `mart_lot_dwell_daily` | lot × day × duration bucket | table, contract enforced |
| `mart_data_quality_daily` | day × lot: rejects by reason, closed by reconciliation, exit-only | table, contract enforced |

Tests: keys, relationships, accepted values; singular tests (occupancy >= 0, mart entries = fact
entries, stays complete); a dbt unit test for the step function. Reports are cached per successful
warehouse run; freshness comes from `pipeline_runs`.

## Generator

- Settings (`GENERATOR_`): `API_URL`, `INGEST_API_KEY`, `ADMIN_API_KEY`, `SEED` (42), `STUDENTS`
  (10000), `SCENARIO`, `RATE_MULTIPLIER`, `MIN_RATE_PER_MIN`, `BATCH_SIZE` (50), 
  `BACKFILL_BATCH_SIZE` (1000), `BACKFILL_DAYS`, `FLUSH_INTERVAL_S`, `OUTBOX_MAX`, `TIMEZONE`,
  `FAULT_{DUPLICATE,OUT_OF_ORDER,MISSING_EXIT,MISSING_ENTRY,UNKNOWN_STUDENT,UNKNOWN_GATE}_RATE`,
  `LOG_LEVEL`.
- `bootstrap`: GET lots; PUT "Estacionamento Principal" (200) and "Estacionamento Anexo" (120, then
  150 from a date fixed on first bootstrap as today − 45 days and read back afterwards); PUT 4
  gates; upsert 10,000 students in 10 bulk calls (`stu_` + sha256(seed:i)[:12]). All idempotent.
- Event id: `uuid5(NS, f"{student_ref}|{gate_id}|{occurred_at.isoformat()}")`.
- `backfill --days 90 [--end-date]`: hour profile (07:15, 13:00, 18:15 peaks), weekday factors
  (Sat 0.35, Sun 0.05), holidays (0.1), quiet week before semester start, cars turned away at
  historical capacity, seeded faults; batches of 1000 (about 190k events in 1 to 2 min); a rerun is
  all duplicates.
- `live [--scenario --rate --duration]`: real local time, rush-hour windows, minimum rate, flush
  every 5 s, out-of-order events held back for a random delay.
- Sender: full-jitter backoff (0.5 s to 30 s) on network errors and 5xx; `Retry-After` on 429/503;
  401/403/422 fatal (exit non-zero); bounded outbox in live mode.
- `run` = bootstrap, backfill if `GENERATOR_BACKFILL_DAYS > 0`, then live (the compose service).
  Heartbeat file plus `turnstile-gen healthcheck`; JSON logs.
- Models: `datamodel-codegen --input backend/openapi.json --input-file-type openapi
  --output-model-type pydantic_v2.BaseModel --use-annotated --field-constraints
  --target-python-version 3.12 --output generator/src/turnstile_generator/api_models.py`; CI fails
  on drift.

## Ops

- Settings (`PARKING_`): `ENV` (dev/test/prod), `DATABASE_URL`, `CORS_ORIGINS` (`*` only when
  `ENV=dev`), `INGEST_API_KEY`, `ADMIN_API_KEY`, `OPERATOR_API_KEY` (SecretStr, >= 32 chars),
  `TIMEZONE` (America/Fortaleza), `INGEST_MAX_BATCH` (1000), `INGEST_MAX_CONCURRENCY` (4),
  `INGEST_LOCK_TIMEOUT_MS` (2000), `MAX_CLOCK_SKEW_S` (300), `MAX_EVENT_AGE_DAYS` (120),
  `LIVE_POLL_INTERVAL_S` (1), `LIVE_HEARTBEAT_S` (15), `LIVE_MAX_CONNECTIONS` (500), `LAKE_DIR`,
  `DBT_PROJECT_DIR`, `WAREHOUSE_INTERVAL_MIN`, `DAILY_RUN_AT`, `WORKER_POLL_S`,
  `WORKER_METRICS_PORT`, `LOG_LEVEL`, `LOG_FORMAT`. Tests only: `TEST_DATABASE_URL`.
- Logs: stdlib JSON (ts UTC, level, logger, message, service, request_id, extras); request id
  from `X-Request-ID` or a new uuid4, echoed and exposed through CORS; one access line per request
  (uvicorn access log off), one line per ingest batch; never log keys, payloads or student refs.
- Metrics: `http_request_duration_seconds{method,route,status}`,
  `parking_events_ingested_total{lot}`, `parking_events_duplicate_total{lot}`,
  `parking_events_rejected_total{reason}`, `parking_ingest_throttled_total{cause}`,
  `parking_event_lag_seconds`, `parking_lot_occupied{lot}`, `parking_lot_capacity{lot}`,
  `parking_live_connections`, `parking_pipeline_runs_total{job,status}`.
- CORS: methods GET, POST, PUT; headers Content-Type, X-API-Key, X-Request-ID; no credentials.
- Shutdown: draining flag (readyz 503), sockets closed with 1001, poller cancelled, engine disposed;
  uvicorn `--timeout-graceful-shutdown 15`.

## Tests

| Layer | Content | Runs on |
| --- | --- | --- |
| Backend unit | pairing and the 10 properties; validation (every reason, both time bounds); clock; archive contract; scheduler due logic with an injected clock | no database |
| Backend API | httpx `AsyncClient` on `create_app(test_settings)` with lifespan: ingest counts, duplicates, conflicts, rejects; 429 and 503; three-key matrix; registry upserts; ingestion health; reports with an attached `warehouse`; ETag/304; WebSocket via TestClient; `GateEvent` in OpenAPI components | SQLite (aiosqlite) |
| Backend worker | claim, run, mark; abandoned rows; `daily` over pending and reopened dates | SQLite |
| Backend integration (`-m integration`) | migrations up/down; append-only trigger; concurrent overlapping batches; occupancy = recount; lock timeout → 429; SKIP LOCKED with two workers; export swap; e2e: uvicorn + `turnstile-gen bootstrap` + `backfill --days 7` + `worker --once daily` + dbt twice with unchanged marts + reports | Postgres (CI service container; locally the compose `postgres` under the heavy lock) |
| Generator unit | deterministic streams per seed; fault rates; uuid5 stability; capacity cap; sender over `httpx.MockTransport` (429, 503, network errors, 401/422 fatal); bootstrap idempotence against a fake API; models drift | no network |
| Compose smoke | images, `make demo` path with a 7-day backfill, REST and WebSocket through nginx | CI |

Coverage gate: 85% per Python project on the default suite (Postgres-only branches and the dbt
subprocess are covered by integration).

## PR phasing

Each PR leaves CI green and gets a fresh-context `reviewer` before merge.

1. **Backend baseline:** `backend/` project, `core/`, app factory, CLI skeleton, Alembic setup;
   Makefile (`install`, `lint`, `test`, `test-fast`); CI backend lint and test jobs;
   `security.yml` (pip-audit, zizmor); `.env.example`; untrack `__pycache__` and Parquet.
2. **Registry, ingestion, occupancy:** 0001; admin, public, ingest, rejects and health routes;
   pairing with hypothesis; `openapi.json` with a drift check; compose with postgres only; CI
   integration job.
3. **Generator:** project, generated models with drift check, bootstrap, backfill, live, sender, CI
   job. Delete `undb_faceid/` and `api/`.
4. **Real-time:** poller, hub, WebSocket, gauges.
5. **Worker and batch:** 0002, claim loop, scheduler, close_day, export with contract, operator
   runs.
6. **Warehouse:** dbt project, warehouse jobs, reports and freshness; dbt in CI (built twice,
   marts unchanged).
7. **api-client:** npm workspaces, generated types, `live.ts`, hooks, drift check.
8. **Web and mobile** (disjoint, in parallel): Vagas, lot detail, Relatórios; Operador on web
   (ingestion health, rejects, runs, enqueue); UNDB identity and the unofficial-project notice.
9. **Docker:** backend image (api, worker, migrate one-shot) and generator image, multi-stage and
   non-root; nginx web with WebSocket upgrade; compose (postgres, migrate, api, worker, generator,
   web); `make demo`; CI compose smoke.
10. **Docs:** README (origin, what was rebuilt, the backend/generator split), D2 diagrams with the
    `diagrams` job, `flow.md`, CHANGELOG, Dependabot. Then branch protection.

## Risks

- `GateEvent` reaches the OpenAPI components only through the `core/openapi.py` hook (the body is
  validated per item); an API test asserts it.
- FastAPI and dbt in one lock may conflict; fallback: `uv tool install` dbt inside the image.
- Backfill time over HTTP depends on the cost of a 1000-item batch; the PR 6 e2e times a 7-day
  load.
- `make demo` must generate random local API keys; nothing secret is committed.
- Expo Go runs only the latest store SDK; both apps pin the React version Expo SDK 57 requires.
- WebSocket through nginx needs the upgrade headers; the compose smoke tests the socket.
