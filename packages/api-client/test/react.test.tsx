// @vitest-environment jsdom
import { act, cleanup, render } from '@testing-library/react'
import { StrictMode, useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createParkingClient } from '../src'
import { useLiveAvailability } from '../src/react'

class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }
  close(): void {
    this.closed = true
  }
}
const openSockets = () => FakeSocket.instances.filter((s) => !s.closed)

function setVisibility(state: 'hidden' | 'visible'): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

// One stable client for the whole file, as the hook requires.
const client = createParkingClient({
  baseUrl: 'http://api.test',
  fetch: (async () => new Response('{}', { status: 503 })) as unknown as typeof fetch,
})

const seen: { latest?: ReturnType<typeof useLiveAvailability> } = {}
function Probe({ enabled }: { enabled?: boolean }) {
  const value = useLiveAvailability(client, enabled === undefined ? {} : { enabled })
  useEffect(() => {
    seen.latest = value
  })
  return null
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.instances = []
  delete seen.latest
  vi.stubGlobal('WebSocket', FakeSocket)
  setVisibility('visible')
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useLiveAvailability', () => {
  it('opens one socket on mount and delivers snapshots and status', () => {
    render(<Probe />)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(FakeSocket.instances[0]?.url).toBe('ws://api.test/api/v1/live/availability')
    expect(seen.latest?.status).toBe('connecting')
    const socket = FakeSocket.instances[0]!
    act(() => socket.onopen?.({}))
    expect(seen.latest?.status).toBe('open')
    const snapshot = { generated_at: '2026-10-28T12:00:00Z', lots: [] }
    act(() => socket.onmessage?.({ data: JSON.stringify(snapshot) }))
    expect(seen.latest?.snapshot).toEqual(snapshot)
    expect(seen.latest?.lastUpdate).toBeInstanceOf(Date)
  })

  it('leaves exactly one open socket under StrictMode', () => {
    render(
      <StrictMode>
        <Probe />
      </StrictMode>,
    )
    expect(openSockets()).toHaveLength(1)
  })

  it('pauses when the page is hidden and resumes when it is visible again', () => {
    render(<Probe />)
    act(() => setVisibility('hidden'))
    expect(openSockets()).toHaveLength(0)
    expect(seen.latest?.status).toBe('paused')
    act(() => setVisibility('visible'))
    expect(openSockets()).toHaveLength(1)
    expect(seen.latest?.status).toBe('connecting')
  })

  it('does not connect when mounted hidden, and connects when shown', () => {
    setVisibility('hidden')
    render(<Probe />)
    expect(FakeSocket.instances).toHaveLength(0)
    act(() => setVisibility('visible'))
    expect(openSockets()).toHaveLength(1)
  })

  it('opens no socket when enabled is false, and follows the flag', () => {
    const { rerender } = render(<Probe enabled={false} />)
    expect(FakeSocket.instances).toHaveLength(0)
    rerender(<Probe enabled />)
    expect(openSockets()).toHaveLength(1)
    rerender(<Probe enabled={false} />)
    expect(openSockets()).toHaveLength(0)
  })

  it('leaves no socket and no timer after unmount', async () => {
    const { unmount } = render(<Probe />)
    const socket = FakeSocket.instances[0]!
    act(() => socket.onclose?.({})) // down: polling and a retry timer are now pending
    await act(() => vi.advanceTimersByTimeAsync(0))
    unmount()
    expect(openSockets()).toHaveLength(0)
    expect(vi.getTimerCount()).toBe(0)
    document.dispatchEvent(new Event('visibilitychange')) // the listener is gone
    expect(FakeSocket.instances).toHaveLength(1)
  })
})
