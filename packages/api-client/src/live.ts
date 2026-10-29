import type { components } from './schema'

export type AvailabilitySnapshot = components['schemas']['AvailabilitySnapshot']
type LotAvailability = components['schemas']['LotAvailability']

export type LiveStatus = 'connecting' | 'open' | 'polling' | 'paused' | 'closed'

export const BACKOFF_BASE_MS = 1_000
export const BACKOFF_MAX_MS = 30_000
export const SILENCE_MS = 45_000
export const POLL_MS = 10_000

/** The part of the WebSocket API the subscriber uses (browsers and React Native both have it). */
export interface SocketLike {
  onopen: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
  onclose: ((ev: unknown) => void) | null
  onerror: ((ev: unknown) => void) | null
  close(): void
}

export interface LiveOptions {
  /** API origin; the socket URL is derived from it (http becomes ws, https becomes wss). */
  baseUrl: string
  onSnapshot: (snapshot: AvailabilitySnapshot) => void
  onStatus?: (status: LiveStatus) => void
  WebSocket?: new (url: string) => SocketLike
  fetch?: typeof globalThis.fetch
  setTimeout?: (fn: () => void, ms: number) => unknown
  clearTimeout?: (handle: unknown) => void
  /** Returns [0, 1); injectable for tests. */
  random?: () => number
}

export interface LiveAvailability {
  readonly status: LiveStatus
  /** Connect; only from the initial closed state. */
  start(): void
  /** Close the socket and stop polling, for a hidden page or a backgrounded app. */
  pause(): void
  /** Open again after pause(); the server sends a fresh snapshot on connect. */
  resume(): void
  /** Stop for good. */
  close(): void
}

export function liveUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, '').replace(/^http/, 'ws')}/api/v1/live/availability`
}

/** Full-jitter backoff: a random delay in [0, min(30 s, 1 s * 2^attempt)). */
export function backoffDelay(attempt: number, random: () => number): number {
  return random() * Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt)
}

/**
 * Live availability as specified in docs/api.md: WebSocket with reconnect and jitter, a 45 s silence
 * watchdog, the highest version kept per lot, REST polling every 10 s while the socket is down.
 */
export function createLiveAvailability(options: LiveOptions): LiveAvailability {
  const WS = options.WebSocket ?? (globalThis.WebSocket as unknown as new (url: string) => SocketLike)
  const doFetch = options.fetch ?? ((...a: Parameters<typeof fetch>) => globalThis.fetch(...a))
  const setT = options.setTimeout ?? ((fn: () => void, ms: number) => globalThis.setTimeout(fn, ms))
  const clearT = options.clearTimeout ?? ((h: unknown) => globalThis.clearTimeout(h as number))
  const random = options.random ?? Math.random
  const base = options.baseUrl.replace(/\/+$/, '')

  let status: LiveStatus = 'closed'
  let socket: SocketLike | undefined
  let attempt = 0
  let retryTimer: unknown
  let silenceTimer: unknown
  let pollTimer: unknown
  let polling = false
  let pollGen = 0
  const lots = new Map<string, LotAvailability>()

  function setStatus(next: LiveStatus): void {
    if (next === status) return
    status = next
    options.onStatus?.(next)
  }

  function clearTimers(): void {
    clearT(retryTimer)
    clearT(silenceTimer)
    stopPolling()
  }

  function dropSocket(): void {
    const s = socket
    socket = undefined
    if (!s) return
    s.onopen = s.onmessage = s.onclose = s.onerror = null
    try {
      s.close()
    } catch {
      // already closed
    }
  }

  /** Keep the highest version per lot, then emit the merged snapshot. */
  function merge(incoming: AvailabilitySnapshot): void {
    const seen = new Set<string>()
    const merged = incoming.lots.map((lot) => {
      seen.add(lot.lot_id)
      const known = lots.get(lot.lot_id)
      const keep = known !== undefined && known.version > lot.version ? known : lot
      lots.set(lot.lot_id, keep)
      return keep
    })
    for (const id of [...lots.keys()]) if (!seen.has(id)) lots.delete(id)
    options.onSnapshot({ ...incoming, lots: merged })
  }

  function armSilence(): void {
    clearT(silenceTimer)
    silenceTimer = setT(() => down(socket), SILENCE_MS)
  }

  // A fetch in flight cannot be cancelled, so each poll chain carries a generation; stopPolling()
  // bumps it and an older chain neither merges its result nor schedules another tick.
  async function pollOnce(gen: number): Promise<void> {
    try {
      const response = await doFetch(`${base}/api/v1/availability`)
      if (!response.ok || gen !== pollGen) return
      const body = (await response.json()) as AvailabilitySnapshot
      if (gen === pollGen) merge(body)
    } catch {
      // keep polling; the next tick retries
    }
  }

  function startPolling(): void {
    if (polling) return
    polling = true
    const gen = ++pollGen
    const tick = async (): Promise<void> => {
      await pollOnce(gen)
      if (polling && gen === pollGen) pollTimer = setT(() => void tick(), POLL_MS)
    }
    void tick()
  }

  function stopPolling(): void {
    polling = false
    pollGen += 1
    clearT(pollTimer)
  }

  /** The socket failed, closed or went silent: poll meanwhile and retry with backoff. */
  function down(from: SocketLike | undefined): void {
    if (from !== socket || status === 'paused' || status === 'closed') return
    dropSocket()
    clearT(silenceTimer)
    setStatus('polling')
    startPolling()
    retryTimer = setT(connect, backoffDelay(attempt, random))
    attempt += 1
  }

  function connect(): void {
    if (status === 'paused' || status === 'closed') return
    let s: SocketLike
    try {
      s = new WS(liveUrl(base))
    } catch {
      socket = undefined
      down(undefined)
      return
    }
    socket = s
    armSilence() // a handshake that never completes is as silent as a dead socket
    s.onopen = () => {
      if (s !== socket) return
      stopPolling()
      setStatus('open')
      armSilence()
    }
    s.onmessage = (ev) => {
      if (s !== socket) return
      attempt = 0
      armSilence()
      try {
        const parsed = JSON.parse(String(ev.data)) as AvailabilitySnapshot
        if (Array.isArray(parsed.lots)) merge(parsed)
      } catch {
        // ignore a frame that is not a snapshot
      }
    }
    s.onclose = s.onerror = () => down(s)
  }

  return {
    get status() {
      return status
    },
    start() {
      if (status !== 'closed') return
      attempt = 0
      setStatus('connecting')
      connect()
    },
    pause() {
      if (status === 'paused' || status === 'closed') return
      clearTimers()
      dropSocket()
      setStatus('paused')
    },
    resume() {
      if (status !== 'paused') return
      attempt = 0
      setStatus('connecting')
      connect()
    },
    close() {
      clearTimers()
      dropSocket()
      setStatus('closed')
    },
  }
}
