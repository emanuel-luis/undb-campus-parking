"""The live availability socket (documented in the OpenAPI document by core/openapi.py)."""

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from campus_parking.core import metrics
from campus_parking.realtime.hub import Hub, Message
from campus_parking.realtime.poller import SnapshotSource, snapshot_json

router = APIRouter(tags=["availability"])

GOING_AWAY = 1001
TRY_AGAIN_LATER = 1013


async def _pump(websocket: WebSocket, queue: asyncio.Queue[Message]) -> bool:
    """Forward hub messages. True when the hub asked to close, False when the socket died."""
    try:
        while (message := await queue.get()) is not None:
            await websocket.send_text(message)
    except (WebSocketDisconnect, RuntimeError):
        return False
    return True


async def _discard(websocket: WebSocket) -> None:
    """Read and ignore client frames, so a disconnect is noticed."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass


@router.websocket("/live/availability")
async def live_availability(websocket: WebSocket) -> None:
    hub: Hub = websocket.app.state.hub
    source: SnapshotSource = websocket.app.state.live_source
    await websocket.accept()
    queue = hub.subscribe()
    if queue is None:
        await websocket.close(GOING_AWAY if hub.closed else TRY_AGAIN_LATER)
        return
    metrics.live_connections.inc()
    tasks: list[asyncio.Task[Any]] = []
    try:
        await websocket.send_text(snapshot_json(await source.snapshot()))
        sender = asyncio.create_task(_pump(websocket, queue))
        tasks = [sender, asyncio.create_task(_discard(websocket))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if sender.done() and sender.result():
            await websocket.close(GOING_AWAY)
    except (WebSocketDisconnect, RuntimeError):
        pass  # the client left while the first snapshot was being sent
    finally:
        for task in tasks:  # also when this handler itself is cancelled
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hub.unsubscribe(queue)
        metrics.live_connections.dec()
