import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createParkingClient, type Schemas } from '@undb-campus-parking/api-client'
import { MemoryRouter, useLocation } from 'react-router'
import { vi } from 'vitest'
import { ApiContext } from '../src/api'
import { AppRoutes } from '../src/App'

export const BASE = 'http://api.test'

type Handler = unknown | ((url: URL, req: Request) => unknown)
export type Routes = Record<string, Handler>

export function problem(status: number, code: string, detail: string, requestId: string | null = null): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title: 'Erro', status, code, detail, instance: '/', request_id: requestId }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  })
}

/** A fetch that answers by pathname (JSON body, or a Response as given); 404 problem otherwise. */
export function fakeFetch(routes: Routes) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = input instanceof Request ? input : new Request(String(input), init)
    const url = new URL(req.url)
    const h = routes[url.pathname]
    if (h === undefined) return problem(404, 'not-found', `no route for ${url.pathname}`)
    const v = typeof h === 'function' ? await (h as (u: URL, r: Request) => unknown)(url, req) : h
    return v instanceof Response ? v : Response.json(v)
  })
}

export class FakeSocket {
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
  static latest(): FakeSocket {
    const s = FakeSocket.instances.at(-1)
    if (!s) throw new Error('no socket opened')
    return s
  }
}

/** Open the latest socket and push one snapshot through it. */
export function pushSnapshot(lots: Schemas['LotAvailability'][]): void {
  const s = FakeSocket.latest()
  s.onopen?.({})
  s.onmessage?.({ data: JSON.stringify({ generated_at: '2026-10-28T12:00:00Z', lots }) })
}

export function lotAvailability(over: Partial<Schemas['LotAvailability']> = {}): Schemas['LotAvailability'] {
  return {
    lot_id: 'L1',
    name: 'Estacionamento Principal',
    capacity: 200,
    occupied: 158,
    free: 42,
    occupancy_pct: 79,
    over_capacity: false,
    version: 1,
    updated_at: '2026-10-28T12:00:00Z',
    last_event_at: '2026-10-28T11:59:00Z',
    ...over,
  }
}

export const LOTS: Schemas['LotOut'][] = [
  {
    lot_id: 'L1',
    name: 'Estacionamento Principal',
    capacity: 200,
    capacity_history: [{ capacity: 200, valid_from: '2024-01-01' }],
    gates: [
      { gate_id: 'G1', lot_id: 'L1', name: 'Portão Norte', direction: 'entry' },
      { gate_id: 'G2', lot_id: 'L1', name: 'Portão Sul', direction: 'exit' },
    ],
  },
  { lot_id: 'L2', name: 'Estacionamento Anexo', capacity: 80, capacity_history: [], gates: [] },
]

function LocationProbe() {
  const l = useLocation()
  return <output data-testid="location">{l.pathname + l.search}</output>
}

export function renderApp(path: string, routes: Routes = {}) {
  const fetch = fakeFetch(routes)
  const client = createParkingClient({ baseUrl: BASE, fetch: fetch as unknown as typeof globalThis.fetch })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  const view = render(
    <ApiContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>
    </ApiContext.Provider>,
  )
  return { ...view, fetch, user }
}

/** URLs the fake fetch was called with. */
export function calledUrls(fetch: ReturnType<typeof fakeFetch>): string[] {
  return fetch.mock.calls.map(([input]) => (input instanceof Request ? input.url : String(input)))
}
