"""Hub semantics: drop-oldest queues, the connection cap and shutdown."""

import asyncio

from campus_parking.realtime.hub import QUEUE_SIZE, Hub, Message


def drain(queue: asyncio.Queue[Message]) -> list[Message]:
    out: list[Message] = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_publish_reaches_every_subscriber() -> None:
    hub = Hub(10)
    a, b = hub.subscribe(), hub.subscribe()
    assert a is not None and b is not None
    hub.publish("one")
    assert drain(a) == ["one"] and drain(b) == ["one"]


def test_full_queue_drops_its_oldest_message_and_never_blocks() -> None:
    hub = Hub(10)
    slow, fast = hub.subscribe(), hub.subscribe()
    assert slow is not None and fast is not None
    for n in range(QUEUE_SIZE + 3):
        hub.publish(str(n))
        drain(fast)
    assert drain(slow) == [str(n) for n in range(3, QUEUE_SIZE + 3)]


def test_cap_and_unsubscribe() -> None:
    hub = Hub(2)
    first, second = hub.subscribe(), hub.subscribe()
    assert first is not None and second is not None
    assert hub.subscribe() is None
    assert hub.connections == 2
    hub.unsubscribe(first)
    hub.unsubscribe(first)  # idempotent
    assert hub.connections == 1
    assert hub.subscribe() is not None


async def test_close_signals_everyone_and_refuses_newcomers() -> None:
    hub = Hub(10)
    queue = hub.subscribe()
    assert queue is not None
    await hub.close(timeout_s=0.05)  # the connection never leaves, so this times out
    assert drain(queue) == [None]
    assert hub.subscribe() is None
    hub.publish("after close")
    assert queue.empty()
