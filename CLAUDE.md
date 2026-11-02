# CLAUDE.md

Campus parking availability from turnstile events: a FastAPI backend (ingestion, live occupancy,
worker, dbt warehouse), a separate event generator, and web and Expo mobile clients sharing a
generated TypeScript client. Read `README.md` first; decisions are in `docs/DECISIONS.md`, the
design contract in `docs/REFACTOR_PLAN.md`, the end-to-end order in `docs/flow.md`.

## Commands

```bash
make help                                # every target
make install | make js-install           # uv envs (backend with the dbt group, generator) | npm ci (Node 24)
make lint                                # ruff, mypy, eslint, tsc + openapi, generator-models, schema.ts drift (= CI)
make test                                # pytest (85% gates), Vitest, Jest, web build, Expo export
make up-db && make test-integration      # Postgres-only tests (compose postgres, database parking_test)
make test-fast                           # backend pytest -x -q, no coverage
make openapi                             # regenerate backend/openapi.json after an API change
make gen-generator-models                # then regenerate generator/src/turnstile_generator/api_models.py
make api-client                          # and packages/api-client/src/schema.ts
make warehouse | make warehouse-full     # dbt build as a recorded run (needs PARKING_DATABASE_URL)
make demo | make smoke | make demo-down  # whole stack in Docker, end-to-end smoke, tear down
make web-dev | make mobile-start         # Vite on :5173 | Expo (set EXPO_PUBLIC_API_BASE_URL first)
make d2-install                          # once: pinned D2 v0.9.0 (Linux x86_64, sha256 checked)
make diagrams | make diagrams-check      # render docs/diagrams/*.d2; check = render + fail on diff (= CI)
make diagrams-watch NAME=<name>          # live preview of one diagram in the browser
uv run --project backend campus-parking --help     # serve | worker | migrate | openapi | close-day | export | warehouse
uv run --project generator turnstile-gen --help    # bootstrap | backfill | live | run | healthcheck
```

On Windows, run make, D2 and anything that needs Bash in WSL: CI renders the diagrams with the
Linux binary and the committed SVGs must match it byte for byte.

## Repo map

- `backend/src/campus_parking/`: `core` (settings, db, logging, problems, security, clock),
  `registry`, `ingestion` (`contract.py` is the GateEvent v1 contract, `validation.py` is pure),
  `occupancy` (`pairing.py` is pure), `realtime` (hub, poller, WebSocket), `jobs` (queue,
  scheduler, worker, close_day, export, archive contract, warehouse), `reports`; `main.py`
  (app factory), `cli.py`. Router → service → repository inside each module.
- `backend/alembic/versions/`: 0001 registry, 0002 ingestion and occupancy, 0003 jobs.
- `backend/warehouse/`: dbt project (staging views in `warehouse_staging`; seed, dims, facts,
  marts in `warehouse`), singular tests in `tests/`.
- `backend/openapi.json`: the committed API contract.
- `generator/src/turnstile_generator/`: bootstrap, backfill, live, sender; `api_models.py` is
  generated.
- `packages/api-client/src/`: `schema.ts` (generated), `client.ts`, `live.ts`
  (`createLiveAvailability`), `react.ts` (query keys and options).
- `apps/web/` (React + Vite, nginx image), `apps/mobile/` (Expo Router).
- `scripts/`: `smoke.py`, `e2e_check.py`, `warehouse_check.py` (CI checks).
- `docs/diagrams/`: D2 sources (`_style.d2` is imported by all) and the rendered SVGs.

## Invariants

- Events enter only through `POST /api/v1/events`, for the generator and any real gate alike.
- `gate_events` is append-only (a trigger blocks UPDATE and DELETE). Never add an update or
  delete path; registry rows referenced by events are never deleted, and a gate's lot and
  direction never change.
- Ingestion is idempotent: `event_id` plus the payload hash decides duplicate or
  `event_id_conflict`. Stays are re-derived per (lot, student, service day) from all their events,
  so arrival order and resends must never change the result; keep the Hypothesis properties
  green.
- `lot_occupancy.occupied` is the exact count of open stays; its `version` rises for every lot
  with a newly accepted event in a batch. The live poller relies on both.
- `backend/openapi.json` is the contract. It has two generated consumers,
  `generator/src/turnstile_generator/api_models.py` and `packages/api-client/src/schema.ts`.
  After an API change run `make openapi`, `make gen-generator-models` and `make api-client` and
  commit all three; never edit generated files by hand. CI fails on drift.
- The generator never touches the database or imports backend code.
- The reports API reads only the `warehouse` marts (and `pipeline_runs` for freshness). Every job
  runs as a `pipeline_runs` row; keep jobs idempotent.
- No secrets in the repo. API keys live only in `.env` (`make demo` writes random ones and never
  prints them) or as masked CI values; never log keys, payloads or student refs.
- Student refs are pseudonymous (`stu_` + 12 hex) and never leave warehouse staging
  (`student_key = md5(student_ref)` downstream).
- The UNDB notice stays wherever the brand appears: pt-BR in every app footer and the operator
  login, English in the README ("Unofficial academic project, not affiliated with or endorsed by
  UNDB.").
- Diagrams change through their `.d2` source, then `make diagrams` (WSL); commit both. D2 is the only
  diagram format. When a table, flow or service changes, update the diagram that draws it: CI only
  checks that the SVGs match the sources. Edit `.d2` files with an editor or the Edit tool, not
  sed or heredocs (they mangle `\n` inside D2 strings).

## Conventions

- English for code, comments, logs, API `detail` and docs; pt-BR for UI text.
- Errors are RFC 9457 problem details with a `code`; clients show `detail` and the request id.
- Unit and API tests on SQLite; Postgres-only behavior under `@pytest.mark.integration`.
- Service dates are local to `PARKING_TIMEZONE` (America/Fortaleza); stored times are UTC.
- Conventional Commits in English; branches `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
- New decisions go to `docs/DECISIONS.md` as a numbered row.
