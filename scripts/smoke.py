"""Compose smoke test: REST and the live WebSocket through nginx, standard library only.

    python3 scripts/smoke.py [--base-url http://localhost:8080] [--timeout 180]

Run after `make demo`. Checks /healthz, /api/v1/availability, /api/v1/reports/freshness (the last
warehouse run succeeded; waits while one is queued or running), /api/v1/reports/daily (rows for
the last two weeks) and one snapshot from the WebSocket /api/v1/live/availability. Exits non-zero
with the first broken expectation.
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455


def fail(message: str) -> None:
    raise SystemExit(f"SMOKE FAILED: {message}")


def ok(message: str) -> None:
    print(f"ok  {message}")


def get_json(base: str, path: str, params: dict[str, str] | None = None) -> Any:
    url = base + path + (f"?{urlencode(params)}" if params else "")
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        fail(f"GET {url}: {exc}")


def wait_for_warehouse(base: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        fresh = get_json(base, "/api/v1/reports/freshness")
        status = fresh.get("status")
        if status == "succeeded" and fresh.get("last_run_id"):
            ok(f"freshness: last warehouse run succeeded ({fresh.get('mode')})")
            return
        if status == "failed":
            fail(f"freshness: the last warehouse run failed ({fresh})")
        if time.monotonic() > deadline:
            fail(f"freshness: no succeeded warehouse run after {timeout:.0f} s ({fresh})")
        time.sleep(3)


def read_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            fail("websocket: connection closed mid-frame")
        data += chunk
    return data


def ws_first_message(url: str, timeout: float) -> str:
    """Open a WebSocket (RFC 6455 handshake by hand) and return the first text message."""
    parts = urlsplit(url)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {parts.path} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout) as sock:
        sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += read_exact(sock, 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            fail(f"websocket: no upgrade through nginx ({lines[0]})")
        headers = {
            k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:])
        }
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != accept:
            fail("websocket: wrong Sec-WebSocket-Accept")
        for _ in range(10):  # skip control frames (ping) before the first data frame
            first, second = read_exact(sock, 2)
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", read_exact(sock, 2))
            elif length == 127:
                (length,) = struct.unpack("!Q", read_exact(sock, 8))
            payload = read_exact(sock, length)
            opcode = first & 0x0F
            if opcode == 0x1:
                if not first & 0x80:
                    fail("websocket: fragmented snapshot (not expected from the API)")
                return payload.decode()
            if opcode == 0x8:
                fail(f"websocket: closed by the server ({payload[:2].hex()})")
    fail("websocket: no text message")
    return ""  # unreachable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    default_base = f"http://localhost:{os.environ.get('WEB_PORT', '8080')}"
    parser.add_argument("--base-url", default=default_base, help=f"default {default_base}")
    parser.add_argument(
        "--timeout", type=float, default=180, help="seconds to wait for the warehouse run"
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    health = get_json(base, "/healthz")
    ok(f"/healthz through nginx: {health}")

    availability = get_json(base, "/api/v1/availability")
    if not availability.get("lots"):
        fail(f"/api/v1/availability has no lots ({availability})")
    ok(f"/api/v1/availability: {len(availability['lots'])} lots")

    wait_for_warehouse(base, args.timeout)

    today = datetime.now(UTC).date()  # the window is wide enough for any service time zone
    window = {"from": str(today - timedelta(days=14)), "to": str(today)}
    daily = get_json(base, "/api/v1/reports/daily", window)
    if not isinstance(daily, list) or not daily:
        fail(f"/api/v1/reports/daily is empty for {window}")
    ok(f"/api/v1/reports/daily: {len(daily)} lot-days")

    ws_url = "ws://" + urlsplit(base).netloc + "/api/v1/live/availability"
    snapshot = json.loads(ws_first_message(ws_url, timeout=15))
    if not snapshot.get("lots"):
        fail(f"websocket snapshot has no lots ({snapshot})")
    ok(f"websocket snapshot through nginx: {len(snapshot['lots'])} lots")
    print("SMOKE OK")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
