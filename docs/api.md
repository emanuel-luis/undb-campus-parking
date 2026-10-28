# API notes

The machine-readable contract is `backend/openapi.json` (regenerate with `make openapi`). This
page covers what OpenAPI cannot express.

## Live availability (WebSocket)

URL: `ws://<host>/api/v1/live/availability` (`wss://` behind TLS). Anonymous and read-only, like
`GET /api/v1/availability`.

### Messages

Every message is one JSON text frame holding a full `AvailabilitySnapshot`, the same shape
`GET /api/v1/availability` returns: `generated_at` and `lots`, each lot with `lot_id`, `name`,
`capacity`, `occupied`, `free`, `occupancy_pct`, `over_capacity`, `version`, `updated_at` and
`last_event_at`. Frames sent by the client are read and ignored.

The server sends a snapshot:

- immediately after the connection opens;
- whenever any lot's `version` changes, or a lot appears or disappears. Each API process polls
  the lot counters once a second (`PARKING_LIVE_POLL_INTERVAL_S`), so a change made by any
  replica or by the worker shows up within about a second;
- every 15 seconds (`PARKING_LIVE_HEARTBEAT_S`) even when nothing changed. This is the heartbeat
  and also repairs a client that missed something.

Because each message is complete, a slow client may lose older queued messages (the server keeps
4 per connection and drops the oldest) without losing information.

### Close codes

- 1001 (going away): the server is shutting down. Reconnect with backoff. The `campus-parking
  serve` command sends this itself before uvicorn stops. A server stopped some other way (for
  example plain `uvicorn --factory`) is closed by uvicorn with 1012 (service restart), so clients
  may see 1012 as well; the reconnect rule below applies to any close code.
- 1013 (try again later): the process is at `PARKING_LIVE_MAX_CONNECTIONS`. Reconnect with
  backoff, preferably after a longer pause.

The server also sends protocol pings every 20 seconds (uvicorn default).

### Client rules

1. Reconnect after any close code or error with exponential backoff from 1 s to 30 s, with random
   jitter. Reset the backoff after a message arrives.
2. If no message arrives for 45 seconds (three missed heartbeats), close the socket and
   reconnect.
3. Keep, per lot, the highest `version` seen and ignore a message for that lot with a lower one.
4. While the socket is down, poll `GET /api/v1/availability` every 10 seconds.
5. Close the socket when the page is hidden or the app is in the background; open it again, and
   take a fresh snapshot, when it returns.

The shared client in `packages/api-client` (`live.ts`, added in a later PR) will implement these rules for web and mobile.
