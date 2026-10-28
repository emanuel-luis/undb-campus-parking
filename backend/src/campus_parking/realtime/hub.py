"""Per-process fan-out. Publishing never blocks and never raises.

Every message is a full snapshot, so a slow client that loses its oldest queued message loses
nothing: the next one supersedes it.
"""

import asyncio
import time

QUEUE_SIZE = 4

# What a connection reads from its queue: a JSON text frame, or None meaning "close with 1001".
Message = str | None


class Hub:
    def __init__(self, max_connections: int) -> None:
        self.max_connections = max_connections
        self.closed = False
        self._queues: set[asyncio.Queue[Message]] = set()

    @property
    def connections(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue[Message] | None:
        """A new queue, or None when the hub is closed or at its connection cap."""
        if self.closed or len(self._queues) >= self.max_connections:
            return None
        queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Message]) -> None:
        self._queues.discard(queue)

    def publish(self, message: Message) -> None:
        if self.closed:
            return
        for queue in self._queues:
            if queue.full():
                queue.get_nowait()  # drop the oldest
            queue.put_nowait(message)

    async def close(self, timeout_s: float = 2.0) -> None:
        """Tell every connection to close with 1001, then wait (bounded) for them to leave."""
        self.publish(None)
        self.closed = True
        deadline = time.monotonic() + timeout_s
        while self._queues and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
