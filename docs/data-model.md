# Data model

One Postgres database, three schemas. Alembic owns `public` (the OLTP tables the API and the
worker write); dbt owns `warehouse_staging` (views) and `warehouse` (seed, dimensions, facts and
marts). The reports API reads only `warehouse`. Alembic's autogenerate ignores the two dbt
schemas, so `alembic check` stays clean after a dbt build.

## Sources of truth

| Fact | Source of truth | Derived copies |
| --- | --- | --- |
| A gate event happened | `gate_events` (append-only, trigger blocks UPDATE and DELETE) | Parquet archive `raw/gate_events/`, `fct_gate_event` |
| A rejected item | `ingest_rejects` | archive `raw/ingest_rejects/`, `mart_data_quality_daily` |
| Who is parked now | `stays` (re-derived from the events per lot, student and day) and its counter `lot_occupancy` | `fct_stay`, `fct_occupancy_snapshot` |
| Capacity on a date | `lot_capacity_history` (effective dated) | `dim_lot` (SCD2) |
| Registry | `lots`, `gates`, `students` | `dim_lot`, `dim_gate`, `dim_student` |
| Job runs and closed days | `pipeline_runs`, `service_days` | report freshness |

## OLTP (`public`, Alembic)

| Table | Grain and key |
| --- | --- |
| `lots` | one lot, `lot_id` (slug) |
| `lot_capacity_history` | one capacity version, (`lot_id`, `valid_from`) |
| `gates` | one gate, `gate_id`; lot and direction never change |
| `students` | one pseudonymous student, `student_ref` (`stu_` + 12 hex) |
| `ingest_batches` | one POST /events call, `batch_id` |
| `gate_events` | one accepted event, `event_id` |
| `ingest_rejects` | one rejected item, `reject_id` |
| `stays` | one stay, `stay_id` (= entry or exit event id); status open, closed, missing_exit, exit_only, closed_by_reconciliation |
| `lot_occupancy` | one lot, `lot_id`: open stays and a version for the live stream |
| `pipeline_runs` | one job run, `run_id` (the queue and the run log) |
| `service_days` | one closed local date, `service_date` |

Column-level detail is in [REFACTOR_PLAN.md](REFACTOR_PLAN.md#data-model) and the migrations.

## Warehouse (dbt, `backend/warehouse`)

The service time zone (`PARKING_TIMEZONE`, passed to dbt as var `timezone`) defines local dates,
hours and 15-minute slots. Student refs never leave staging: everything downstream carries
`student_key = md5(student_ref)`.

| Model | Schema | Grain | Key | Materialization |
| --- | --- | --- | --- | --- |
| `stg_parking__*` (gate_events, stays, lots, lot_capacity_history, gates, students, ingest_rejects) | warehouse_staging | source row | source key | view |
| `int_stays_classified` | warehouse_staging | stay; adds duration bucket and capacity on the date | `stay_id` | view |
| `holidays` | warehouse | holiday date (seed, same dates as the generator) | `holiday_date` | seed |
| `dim_date` | warehouse | day, 2024-01-01 to 2030-12-31 | `date_key` (YYYYMMDD) | table |
| `dim_time` | warehouse | local 15-minute slot, 96 rows | `slot_key` (0 to 95) | table |
| `dim_lot` | warehouse | capacity version (SCD2: `valid_from`, `valid_to`, `is_current`) plus unknown member `-1` | `lot_key` = md5(lot_id, valid_from) | table |
| `dim_gate` | warehouse | gate | `gate_key` = md5(gate_id) | table |
| `dim_student` | warehouse | student, first and last seen | `student_key` | table |
| `fct_gate_event` | warehouse | accepted event | `event_id` | incremental |
| `fct_stay` | warehouse | stay | `stay_id` | incremental |
| `fct_occupancy_snapshot` | warehouse | lot x day x 15-minute slot | (`lot_id`, `service_date`, `slot_key`) | incremental |
| `mart_lot_daily` | warehouse | lot x day | (`lot_id`, `service_date`) | table, contract |
| `mart_lot_hourly` | warehouse | lot x day x hour | (`lot_id`, `service_date`, `hour`) | table, contract |
| `mart_lot_dwell_daily` | warehouse | lot x day x duration bucket | (`lot_id`, `service_date`, `duration_bucket`) | table, contract |
| `mart_data_quality_daily` | warehouse | day x lot (`-1` = unknown lot) | (`service_date`, `lot_id`) | table, contract |

Facts reference the dimensions by `date_key`, `slot_key`, `lot_key` (the version in force on
the service date), `gate_key` and `student_key`; dbt `relationships` tests hold each one.

### Incremental strategy

All three facts use delete+insert and re-read `lookback_minutes` (10) before their watermark,
for transactions that committed after a later one.

- `fct_gate_event`: rows with `received_at` after max(`received_at`) in the table, keyed by
  `event_id`. `gate_events` is append-only, so nothing else can change.
- `fct_stay`: every (`lot_id`, `service_date`) partition holding a stay with `updated_at` after
  the watermark is rebuilt whole. Stays of a student-day are deleted and re-inserted when one of
  its events arrives, so a stay id can disappear; replacing the partition leaves no orphans.
- `fct_occupancy_snapshot`: the same changed partitions (watermark: max `built_at`), the day after
  each (stays that cross midnight), and yesterday and today for every lot (the clock moves).

The worker runs `dbt build` every 15 minutes and `dbt source freshness` plus
`dbt build --full-refresh` at the end of the daily run, so a missed partition lives at most a day.

### Occupancy step function

Per lot, every stay with an entry adds +1 at `entered_at` and -1 at `ended_at` (never while it is
open); exit-only stays never held a space we saw. Slot boundaries are points of the same series,
so each constant segment falls inside one slot. Per slot: `occupied_end` is the level at the slot
end, `occupied_max` the highest level, `occupied_avg` the time-weighted mean, `seconds_full` the
time with level >= capacity, and `free_*` capacity minus those, never below 0. Slots that have
not started are not built; the current one ends now. A dbt unit test pins the arithmetic.

### Marts and the reports API

| Route | Reads |
| --- | --- |
| `GET /reports/summary` | `mart_lot_daily`, aggregated per lot (stay figures are means of daily values) |
| `GET /reports/daily` | `mart_lot_daily` |
| `GET /reports/hourly` | `mart_lot_hourly` (31 days at most) |
| `GET /reports/weekday` | `mart_lot_daily` joined to `dim_date`, holidays excluded |
| `GET /reports/occupancy` | `fct_occupancy_snapshot` of one day |
| `GET /reports/dwell` | `mart_lot_dwell_daily`, summed over the range |
| `GET /reports/quality` | `mart_data_quality_daily` |
| `GET /reports/freshness` | `pipeline_runs` (job `warehouse`) |

Medians and p90 are computed per lot and day in dbt; the API never computes a percentile and
never adds percentiles across lots.

### Tests

Keys (`unique`, `not_null`, `unique_combination`), `relationships` from facts and marts to the
dimensions, `accepted_values` on status, direction, hour and bucket; singular tests: occupancy
never negative and free never above capacity, mart entries and exits equal the fact per
lot-day, and every stays partition of the source equals the fact's (catches a missed
incremental partition); the `occupancy_step_function` unit test.
