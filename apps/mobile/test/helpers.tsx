import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react-native'
import { createParkingClient } from '@undb-campus-parking/api-client'
import type { ReactElement } from 'react'
import { ParkingProvider } from '../src/parking'
import type { LotAvailability } from '../src/types'

export interface Reply {
  status?: number
  body: unknown
}
/** Pathname (without /api/v1) to a reply; an Error rejects like a network failure. */
export type Routes = Record<string, Reply | Error>

export const BASE = 'http://api.test'

export function fakeFetch(routes: Routes) {
  return jest.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    const reply = routes[url.pathname.replace('/api/v1', '')]
    if (reply instanceof Error) throw reply
    if (reply === undefined) return json(404, { title: 'Not Found', status: 404, code: 'not-found', detail: url.pathname })
    return json(reply.status ?? 200, reply.body)
  })
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

/** Minimal WebSocket the live subscriber drives; every created socket is kept for assertions. */
export class FakeSocket {
  static all: FakeSocket[] = []
  onopen: ((ev: unknown) => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onclose: ((ev: unknown) => void) | null = null
  onerror: ((ev: unknown) => void) | null = null
  closed = false
  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }
  close() {
    this.closed = true
  }
  static last(): FakeSocket {
    const s = FakeSocket.all[FakeSocket.all.length - 1]
    if (!s) throw new Error('no socket')
    return s
  }
}

beforeEach(() => {
  FakeSocket.all = []
  ;(globalThis as { WebSocket?: unknown }).WebSocket = FakeSocket
})

export async function renderApp(ui: ReactElement, routes: Routes) {
  const fetch = fakeFetch(routes)
  const client = createParkingClient({ baseUrl: BASE, fetch })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const view = await render(
    <QueryClientProvider client={queryClient}>
      <ParkingProvider client={client}>{ui}</ParkingProvider>
    </QueryClientProvider>,
  )
  return { ...view, fetch, queryClient }
}

export const lotA: LotAvailability = {
  lot_id: 'A',
  name: 'Estacionamento Principal',
  capacity: 200,
  free: 42,
  occupied: 158,
  occupancy_pct: 79,
  over_capacity: false,
  last_event_at: '2026-10-28T12:00:00Z',
  updated_at: '2026-10-28T12:00:00Z',
  version: 3,
}

export const lotB: LotAvailability = { ...lotA, lot_id: 'B', name: 'Bloco B', capacity: 50, free: 0, occupied: 53, occupancy_pct: 106, over_capacity: true }

export const snapshot = (lots: LotAvailability[]) => ({ generated_at: '2026-10-28T12:00:00Z', lots })

export const problem = (status: number, code: string, detail = '') => ({
  status,
  body: { type: 'about:blank', title: 'Erro', status, code, detail, request_id: 'req-1' },
})
