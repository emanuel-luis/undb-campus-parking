import { describe, expect, it, vi } from 'vitest'
import { createParkingClient, ProblemError } from '../src'
import { parkingKeys, parkingQueries } from '../src/react'

type Handler = (req: Request) => Response | Promise<Response>

function make(handler: Handler, operatorKey?: string) {
  const calls: Request[] = []
  const fetchMock = vi.fn(async (input: Request) => {
    calls.push(input)
    return handler(input)
  })
  const client = createParkingClient({
    baseUrl: 'http://api.test/',
    fetch: fetchMock as unknown as typeof fetch,
    ...(operatorKey === undefined ? {} : { operatorKey }),
  })
  return { client, calls }
}

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init })
const problem = (status: number, body: Record<string, unknown>, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify({ type: 'about:blank', title: 'T', instance: '/x', ...body, status }), {
    status,
    headers: { 'content-type': 'application/problem+json', ...headers },
  })

describe('requests', () => {
  it('builds the public URLs', async () => {
    const { client, calls } = make(() => json([]))
    await client.lots()
    await client.availability()
    await client.lotAvailability('A 1')
    await client.reports.summary({ from: '2026-10-01', to: '2026-10-07', })
    await client.reports.occupancy({ date: '2026-10-07', lot_id: 'a' })
    await client.reports.freshness()
    const urls = calls.map((c) => c.url)
    expect(urls[0]).toBe('http://api.test/api/v1/lots')
    expect(urls[1]).toBe('http://api.test/api/v1/availability')
    expect(urls[2]).toBe('http://api.test/api/v1/lots/A%201/availability')
    expect(urls[3]).toBe('http://api.test/api/v1/reports/summary?from=2026-10-01&to=2026-10-07')
    expect(urls[4]).toBe('http://api.test/api/v1/reports/occupancy?date=2026-10-07&lot_id=a')
    expect(urls[5]).toBe('http://api.test/api/v1/reports/freshness')
    for (const name of ['daily', 'hourly', 'weekday', 'dwell', 'quality'] as const) {
      await client.reports[name]()
    }
    expect(calls.at(-1)?.url).toBe('http://api.test/api/v1/reports/quality')
  })

  it('sends X-API-Key on operator routes only, and only when a key is given', async () => {
    const withKey = make(() => json({ items: [], next_cursor: null }), 'k-1')
    await withKey.client.operator.rejects({ limit: 5, cursor: 'c', reason: 'too_old', since: 's' })
    await withKey.client.operator.runs({ job: 'warehouse', status: 'failed', limit: 3 })
    await withKey.client.operator.ingestionHealth({ window_min: 30 })
    await withKey.client.operator.run('7')
    await withKey.client.lots()
    await withKey.client.reports.daily()
    const headers = withKey.calls.map((c) => c.headers.get('x-api-key'))
    expect(headers).toEqual(['k-1', 'k-1', 'k-1', 'k-1', null, null])
    expect(withKey.calls[0]?.url).toBe(
      'http://api.test/api/v1/operator/rejects?limit=5&cursor=c&reason=too_old&since=s',
    )
    expect(withKey.calls[3]?.url).toBe('http://api.test/api/v1/operator/runs/7')

    const noKey = make(() => json([]))
    await noKey.client.operator.runs()
    expect(noKey.calls[0]?.headers.has('x-api-key')).toBe(false)
  })

  it('enqueues a run with the typed body', async () => {
    const { client, calls } = make(() => json({ id: 1 }))
    await client.operator.enqueueRun({ job: 'warehouse', mode: 'full', service_date: '2026-10-28' })
    expect(calls[0]?.method).toBe('POST')
    expect(await calls[0]?.json()).toEqual({ job: 'warehouse', mode: 'full', service_date: '2026-10-28' })
  })

  it('uses the global fetch when none is injected', async () => {
    const f = vi.fn(async () => json([]))
    vi.stubGlobal('fetch', f)
    await createParkingClient({ baseUrl: 'http://api.test' }).lots()
    expect(f).toHaveBeenCalledOnce()
    vi.unstubAllGlobals()
  })
})

describe('ProblemError', () => {
  it('parses an RFC 9457 body', async () => {
    const { client } = make(() =>
      problem(422, { detail: 'from is after to', code: 'invalid-date-range', request_id: 'req-9' }),
    )
    const err = await client.reports.daily({ from: '2026-10-09', to: '2026-10-01' }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ProblemError)
    const p = err as ProblemError
    expect([p.status, p.code, p.detail, p.requestId, p.title]).toEqual([
      422,
      'invalid-date-range',
      'from is after to',
      'req-9',
      'T',
    ])
    expect(p.message).toBe('from is after to')
    expect(p.retryAfter).toBeUndefined()
    expect(p.errors).toBeNull()
  })

  it('exposes Retry-After on warehouse-not-ready', async () => {
    const { client } = make(() =>
      problem(503, { detail: 'warehouse not ready', code: 'warehouse-not-ready' }, { 'retry-after': '30' }),
    )
    const p = (await client.reports.summary().catch((e: unknown) => e)) as ProblemError
    expect([p.status, p.code, p.retryAfter]).toEqual([503, 'warehouse-not-ready', 30])
  })

  it('copes with a body that is not a problem document', async () => {
    for (const body of ['<html>bad gateway</html>', 'null']) {
      const { client } = make(
        () => new Response(body, { status: 502, statusText: 'Bad Gateway', headers: { 'retry-after': 'soon' } }),
      )
      const p = (await client.lots().catch((e: unknown) => e)) as ProblemError
      expect([p.status, p.code, p.type, p.title, p.retryAfter, p.requestId]).toEqual([
        502,
        'unknown',
        'about:blank',
        'Bad Gateway',
        undefined,
        null,
      ])
    }
    const bare = new ProblemError({ status: 500 })
    expect(bare.message).toBe('HTTP 500')
  })
})

describe('304 Not Modified on reports', () => {
  it('answers from the last 200 body and never sends If-None-Match itself', async () => {
    let n = 0
    const { client, calls } = make(() => (n++ === 0 ? json([{ lot_id: 'a' }], { headers: { etag: '"v1"' } }) : new Response(null, { status: 304 })))
    const first = await client.reports.summary({ lot_id: 'a' })
    const again = await client.reports.summary({ lot_id: 'a' })
    expect(again).toEqual(first)
    expect(again).toEqual([{ lot_id: 'a' }])
    expect(calls.every((c) => !c.headers.has('if-none-match'))).toBe(true)
  })

  it('fails with a typed error when a 304 arrives with nothing cached', async () => {
    const { client } = make(() => new Response(null, { status: 304 }))
    const p = (await client.reports.daily().catch((e: unknown) => e)) as ProblemError
    expect(p).toBeInstanceOf(ProblemError)
    expect([p.status, p.code]).toEqual([304, 'not-modified-without-cache'])
  })
})

describe('query keys and options', () => {
  it('gives equal keys for equal filters whatever the property order', () => {
    expect(parkingKeys.report('daily', { to: 'b', from: 'a', lot_id: undefined })).toEqual(
      parkingKeys.report('daily', { from: 'a', to: 'b' }),
    )
    expect(parkingKeys.report('daily', { from: 'a' })).not.toEqual(parkingKeys.report('hourly', { from: 'a' }))
    expect(parkingKeys.report('freshness')).toEqual(['parking', 'reports', 'freshness', {}])
    expect(parkingKeys.all).toEqual(['parking'])
    expect(parkingKeys.lotAvailability('a')).toEqual(['parking', 'availability', 'a'])
  })

  it('builds options whose queryFn calls the client', async () => {
    const { client, calls } = make(() => json([]))
    const opts = [
      parkingQueries.lots(client),
      parkingQueries.availability(client),
      parkingQueries.lotAvailability(client, 'a'),
      parkingQueries.summary(client),
      parkingQueries.daily(client),
      parkingQueries.hourly(client),
      parkingQueries.weekday(client),
      parkingQueries.dwell(client),
      parkingQueries.quality(client),
      parkingQueries.occupancy(client, { date: '2026-10-07' }),
      parkingQueries.freshness(client),
      parkingQueries.ingestionHealth(client),
      parkingQueries.rejects(client),
      parkingQueries.runs(client),
      parkingQueries.run(client, '3'),
    ]
    const keys = new Set(opts.map((o) => JSON.stringify(o.queryKey)))
    expect(keys.size).toBe(opts.length)
    for (const o of opts) await (o.queryFn as () => Promise<unknown>)()
    expect(calls).toHaveLength(opts.length)
  })
})
