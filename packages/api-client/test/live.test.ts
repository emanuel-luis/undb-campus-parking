import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  backoffDelay,
  createLiveAvailability,
  liveUrl,
  type AvailabilitySnapshot,
  type LiveStatus,
  type SocketLike,
} from '../src/live'

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = []
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  closed = false
  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }
  close(): void {
    this.closed = true
  }
  open(): void {
    this.onopen?.({})
  }
  send(data: unknown): void {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }
  drop(): void {
    this.onclose?.({})
  }
}

function snap(lots: Record<string, number>, generatedAt = '2026-10-28T12:00:00Z'): AvailabilitySnapshot {
  return {
    generated_at: generatedAt,
    lots: Object.entries(lots).map(([lot_id, version]) => ({
      lot_id,
      name: lot_id,
      capacity: 100,
      occupied: version,
      free: 100 - version,
      occupancy_pct: version,
      over_capacity: false,
      version,
      updated_at: generatedAt,
      last_event_at: generatedAt,
    })),
  }
}

function setup(over: { random?: () => number; fetch?: typeof fetch; WebSocket?: never } = {}) {
  const snapshots: AvailabilitySnapshot[] = []
  const statuses: LiveStatus[] = []
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(snap({ a: 5 })), { status: 200 }))
  const live = createLiveAvailability({
    baseUrl: 'http://api.test/',
    WebSocket: FakeSocket,
    fetch: over.fetch ?? fetchMock,
    random: over.random ?? (() => 0.5),
    onSnapshot: (s) => snapshots.push(s),
    onStatus: (s) => statuses.push(s),
  })
  return { live, snapshots, statuses, fetchMock }
}

const last = () => FakeSocket.instances[FakeSocket.instances.length - 1]!

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.instances = []
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('liveUrl and backoff', () => {
  it('derives ws and wss from the base URL', () => {
    expect(liveUrl('http://h:8000')).toBe('ws://h:8000/api/v1/live/availability')
    expect(liveUrl('https://h/')).toBe('wss://h/api/v1/live/availability')
  })

  it('stays inside [0, min(30 s, 1 s * 2^attempt)) and spreads with the random source', () => {
    expect(backoffDelay(0, () => 0)).toBe(0)
    expect(backoffDelay(0, () => 0.999)).toBeLessThan(1_000)
    expect(backoffDelay(3, () => 0.999)).toBeLessThan(8_000)
    expect(backoffDelay(3, () => 0.5)).toBe(4_000)
    expect(backoffDelay(20, () => 0.999999)).toBeLessThan(30_000)
    expect(backoffDelay(20, () => 0.5)).toBe(15_000)
    expect(backoffDelay(1, () => 0.25)).not.toBe(backoffDelay(1, () => 0.75))
  })
})

describe('connection and status', () => {
  it('goes connecting, open, and emits each snapshot', () => {
    const { live, snapshots, statuses } = setup()
    live.start()
    live.start() // a second start is ignored
    expect(FakeSocket.instances).toHaveLength(1)
    expect(last().url).toBe('ws://api.test/api/v1/live/availability')
    last().open()
    last().send(snap({ a: 1 }))
    expect(statuses).toEqual(['connecting', 'open'])
    expect(live.status).toBe('open')
    expect(snapshots).toHaveLength(1)
  })

  it('ignores frames that are not snapshots', () => {
    const { live, snapshots } = setup()
    live.start()
    last().open()
    last().send('not json')
    last().send({ hello: 'world' })
    expect(snapshots).toHaveLength(0)
  })

  it('keeps the highest version per lot and drops lots that disappear', () => {
    const { live, snapshots } = setup()
    live.start()
    last().open()
    last().send(snap({ a: 5, b: 2 }))
    last().send(snap({ a: 4, b: 3 })) // a is older: ignored for a, b moves on
    const second = snapshots[1]!
    expect(second.lots.map((l) => [l.lot_id, l.version])).toEqual([
      ['a', 5],
      ['b', 3],
    ])
    last().send(snap({ b: 3 })) // a disappears
    last().send(snap({ a: 1, b: 3 })) // and comes back with a low version: accepted
    expect(snapshots[3]!.lots[0]!.version).toBe(1)
  })
})

describe('reconnect, polling and silence', () => {
  it('polls while down every 10 s, reconnects with backoff, and stops polling once open', async () => {
    const { live, snapshots, statuses, fetchMock } = setup()
    live.start()
    last().open()
    last().drop()
    expect(live.status).toBe('polling')
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(snapshots).toHaveLength(1) // the polled snapshot is emitted
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe('http://api.test/api/v1/availability')

    // random 0.5 and attempt 0: the retry fires after 500 ms
    await vi.advanceTimersByTimeAsync(499)
    expect(FakeSocket.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(2)
    last().drop() // the second attempt fails too: delay 1000 ms (attempt 1)
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(3)

    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2) // still one poll per 10 s

    last().open()
    expect(statuses.at(-1)).toBe('open')
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(44_000) // under the silence limit
    expect(fetchMock.mock.calls.length).toBe(calls)
  })

  it('resets the backoff after a message', async () => {
    const { live } = setup({ random: () => 0.999 })
    live.start()
    last().drop() // attempt 0
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeSocket.instances).toHaveLength(2)
    last().open()
    last().send(snap({ a: 1 }))
    last().drop() // back to attempt 0: under 1 s
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeSocket.instances).toHaveLength(3)
  })

  it('reconnects after 45 s without a message, and a message restarts the clock', async () => {
    const { live } = setup()
    live.start()
    last().open()
    await vi.advanceTimersByTimeAsync(30_000)
    last().send(snap({ a: 1 }))
    await vi.advanceTimersByTimeAsync(44_999)
    expect(live.status).toBe('open')
    const first = last()
    await vi.advanceTimersByTimeAsync(1)
    expect(live.status).toBe('polling')
    expect(first.closed).toBe(true)
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('survives a failing poll and a constructor that throws', async () => {
    const throwing = vi.fn(async () => {
      throw new Error('offline')
    })
    const notOk = vi.fn(async () => new Response('x', { status: 503 }))
    for (const f of [throwing, notOk]) {
      const { live, snapshots } = setup({ fetch: f as unknown as typeof fetch })
      live.start()
      last().drop()
      await vi.advanceTimersByTimeAsync(10_000)
      expect(f.mock.calls.length).toBeGreaterThanOrEqual(2)
      expect(snapshots).toHaveLength(0)
      live.close()
    }
    const snapshots: AvailabilitySnapshot[] = []
    let attempts = 0
    const live = createLiveAvailability({
      baseUrl: 'http://api.test',
      WebSocket: class {
        constructor() {
          attempts += 1
          throw new Error('bad url')
        }
      } as never,
      fetch: (async () => new Response(JSON.stringify(snap({ a: 1 })))) as unknown as typeof fetch,
      random: () => 0,
      onSnapshot: (s) => snapshots.push(s),
    })
    live.start()
    await vi.advanceTimersByTimeAsync(5)
    expect(live.status).toBe('polling')
    expect(attempts).toBeGreaterThan(1)
    expect(snapshots.length).toBeGreaterThan(0)
    live.close()
  })
})

describe('pause, resume and close', () => {
  it('pause closes the socket and stops everything; resume connects again', async () => {
    const { live, fetchMock, statuses } = setup()
    live.start()
    const first = last()
    first.open()
    live.pause()
    live.pause()
    expect(first.closed).toBe(true)
    expect(live.status).toBe('paused')
    await vi.advanceTimersByTimeAsync(120_000)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(fetchMock).not.toHaveBeenCalled()
    live.resume()
    live.resume()
    expect(FakeSocket.instances).toHaveLength(2)
    expect(live.status).toBe('connecting')
    last().open()
    expect(statuses).toEqual(['connecting', 'open', 'paused', 'connecting', 'open'])
  })

  it('pause while polling stops the poll; close is final until start', async () => {
    const { live, fetchMock } = setup()
    live.start()
    last().drop()
    await vi.advanceTimersByTimeAsync(0)
    live.pause()
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock.mock.calls.length).toBe(calls)
    live.close()
    expect(live.status).toBe('closed')
    live.resume() // not paused: no effect
    expect(live.status).toBe('closed')
  })

  it('uses the global WebSocket, fetch and timers by default', async () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    const f = vi.fn(async () => new Response(JSON.stringify(snap({ a: 1 }))))
    vi.stubGlobal('fetch', f)
    const got: AvailabilitySnapshot[] = []
    const live = createLiveAvailability({ baseUrl: 'https://x.test', onSnapshot: (s) => got.push(s) })
    live.start()
    expect(last().url).toBe('wss://x.test/api/v1/live/availability')
    last().drop()
    await vi.advanceTimersByTimeAsync(0)
    expect(got).toHaveLength(1)
    live.close()
  })
})
