# Running the stack

Everything runs in Docker: Postgres, the API, the batch worker, the turnstile generator and the web
app behind nginx. One command builds the images, starts the services, backfills synthetic history
and builds the warehouse.

## Prerequisites

- Docker Engine 24 or newer with Compose v2.24 or newer (`docker compose version`), or Docker
  Desktop. About 3 GB of free memory for the containers and 4 GB of disk for the images.
- GNU Make, Bash and Python 3 on the host (`make demo` uses Python only to generate the keys).
  On Windows, run the commands from WSL.
- Node 24 (`.nvmrc`) only for Expo Go on a phone (see below).

## Start

```bash
make demo                         # 90 days of history
```

Or, for a faster first run:

```bash
make demo DEMO_BACKFILL_DAYS=7
```

A 30-day run took about 74 s for the backfill and about 43 s for the daily close and warehouse
build; the default 90 days takes longer.

`make demo` does four things (a rerun repeats them safely):

1. Creates `.env` from `.env.example` when it is missing and adds any missing API key
   (`PARKING_INGEST_API_KEY`, `PARKING_ADMIN_API_KEY`, `PARKING_OPERATOR_API_KEY`), each a fresh
   `secrets.token_urlsafe(32)`. The keys are never printed and `.env` is never committed.
2. `docker compose up -d --build --wait` for everything but the worker: builds the images and waits
   until the services are healthy. `migrate` applies the migrations and exits; the API starts
   after it, then the generator (registry bootstrap, then live events) and the web app.
3. `docker compose run --rm --no-deps generator backfill --days N`: posts N past days of events.
4. `docker compose run --rm --no-deps worker worker --once daily`: closes yesterday, exports it
   to the Parquet lake and runs a full warehouse refresh, so the reports have data. Only then does
   the long-running worker start, so its scheduler never races this run.

Then check it end to end through nginx, REST and the live WebSocket:

```bash
make smoke
```

## Addresses and keys

| What | Where |
| --- | --- |
| Web app | http://localhost:8080 (`WEB_PORT`) |
| API and its docs | http://localhost:8000/docs (`API_PORT`) |
| Postgres | localhost:5432 (`POSTGRES_PORT`), user, password and database `parking` |

Every port is bound to 127.0.0.1. The web app calls the API on its own origin: nginx proxies
`/api` (REST and the WebSocket `/api/v1/live/availability`) to the API container.

The operator pages of the web app ask for the operator key. Read it from `.env`:

```bash
grep PARKING_OPERATOR_API_KEY .env
```

The backend runs with `PARKING_ENV=prod` in Docker, so it refuses to start without the three keys
or with placeholder or repeated keys. The generator uses the same ingest and admin keys.

## Expo Go on a phone (LAN)

The mobile app talks to the API directly, so the phone needs to reach the API port on your computer.

1. Put the phone and the computer on the same network (same Wi-Fi, no guest isolation).
2. Expose the API on the LAN and recreate it (nginx finds the new container within 10 s):

   ```bash
   echo "API_BIND=0.0.0.0" >> .env
   docker compose up -d --no-deps api
   ```

3. Find the computer's LAN address (`ipconfig` on Windows, `ip addr` on Linux, `ipconfig getifaddr en0`
   on macOS) and allow inbound TCP 8000 in its firewall. On Windows with Docker Desktop, the
   Windows firewall is the one that matters, even when you run `make` from WSL.
4. Start Expo with that address and scan the QR code with Expo Go:

   ```bash
   npm ci
   EXPO_PUBLIC_API_BASE_URL=http://<LAN-IP>:8000 make mobile-start
   ```

`API_BIND=0.0.0.0` makes the public routes reachable by anyone on that network. Remove the line
from `.env` and run `docker compose up -d --no-deps api` again when you are done.

## Logs, stop and clean up

```bash
make logs          # follow every service
make demo-down     # stop everything and delete the volumes (database and Parquet lake)
```

`make demo-down` and `make down` are the same command. `.env` stays, with its keys; delete it to
get new ones on the next `make demo`. `make up-db` still starts Postgres alone for the tests and
for running the backend on the host.
