import createClient from 'openapi-fetch'
import { problemFromResponse, ProblemError } from './problem'
import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type RunRequest = Schemas['RunRequest']

type Ok<P extends keyof paths> = paths[P] extends {
  get: { responses: { 200: { content: { 'application/json': infer T } } } }
}
  ? T
  : never

/** Date range and lot filter shared by the report routes (dates are local, YYYY-MM-DD). */
export interface ReportRange {
  from?: string | undefined
  to?: string | undefined
  lot_id?: string | undefined
}
export interface OccupancyParams {
  date?: string | undefined
  lot_id?: string | undefined
}
export interface RejectsParams {
  since?: string | undefined
  reason?: NonNullable<NonNullable<paths['/api/v1/operator/rejects']['get']['parameters']['query']>['reason']> | undefined
  limit?: number | undefined
  cursor?: string | undefined
}
export interface RunsParams {
  job?: RunRequest['job'] | undefined
  status?: 'queued' | 'running' | 'succeeded' | 'failed' | undefined
  limit?: number | undefined
}

export interface ParkingClientOptions {
  /** API origin, for example `http://localhost:8000` (no `/api/v1`). */
  baseUrl: string
  fetch?: typeof globalThis.fetch
  /** Sent as X-API-Key on the operator routes only, never on public ones. */
  operatorKey?: string
}

const OPERATOR_PREFIX = '/api/v1/operator/'
const REPORT_PREFIX = '/api/v1/reports/'

/** Drop undefined entries so optional filters satisfy exactOptionalPropertyTypes. */
function clean(params: object | undefined): Record<string, unknown> {
  return Object.fromEntries(Object.entries(params ?? {}).filter(([, v]) => v !== undefined))
}

export function createParkingClient(options: ParkingClientOptions) {
  const baseUrl = options.baseUrl.replace(/\/+$/, '')
  const doFetch = options.fetch ?? ((...args: Parameters<typeof fetch>) => globalThis.fetch(...args))
  const raw = createClient<paths>({ baseUrl, fetch: doFetch })
  // Last 200 body per report URL. A browser revalidates through its HTTP cache and never shows a
  // 304 to script; a runtime without one (React Native, Node) can, so a 304 is answered from here.
  const reportBodies = new Map<string, string>()

  raw.use({
    onRequest({ request }) {
      if (options.operatorKey !== undefined && new URL(request.url).pathname.startsWith(OPERATOR_PREFIX)) {
        request.headers.set('X-API-Key', options.operatorKey)
      }
      return request
    },
    async onResponse({ request, response }) {
      const isReport = new URL(request.url).pathname.startsWith(REPORT_PREFIX)
      if (response.status === 304) {
        const body = reportBodies.get(request.url)
        if (body === undefined) {
          throw new ProblemError({
            status: 304,
            title: 'Not Modified',
            detail: 'The server answered 304 but no earlier body is cached for this request.',
            code: 'not-modified-without-cache',
          })
        }
        return new Response(body, { status: 200, headers: { 'content-type': 'application/json' } })
      }
      if (!response.ok) throw await problemFromResponse(response)
      if (isReport) reportBodies.set(request.url, await response.clone().text())
      return undefined
    },
  })

  // The paths are typed at the public signatures below; here the query is loosely typed.
  async function get<T>(path: string, query?: object, pathParams?: Record<string, string>): Promise<T> {
    const { data } = await (raw.GET as (p: string, init: unknown) => Promise<{ data?: unknown }>)(path, {
      params: { query: clean(query), path: pathParams ?? {} },
    })
    return data as T
  }

  return {
    baseUrl,
    fetch: doFetch,
    raw,
    lots: () => get<Ok<'/api/v1/lots'>>('/api/v1/lots'),
    availability: () => get<Ok<'/api/v1/availability'>>('/api/v1/availability'),
    lotAvailability: (lotId: string) =>
      get<Ok<'/api/v1/lots/{lot_id}/availability'>>('/api/v1/lots/{lot_id}/availability', undefined, {
        lot_id: lotId,
      }),
    reports: {
      summary: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/summary'>>('/api/v1/reports/summary', p),
      daily: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/daily'>>('/api/v1/reports/daily', p),
      hourly: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/hourly'>>('/api/v1/reports/hourly', p),
      weekday: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/weekday'>>('/api/v1/reports/weekday', p),
      dwell: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/dwell'>>('/api/v1/reports/dwell', p),
      quality: (p: ReportRange = {}) => get<Ok<'/api/v1/reports/quality'>>('/api/v1/reports/quality', p),
      occupancy: (p: OccupancyParams = {}) =>
        get<Ok<'/api/v1/reports/occupancy'>>('/api/v1/reports/occupancy', p),
      freshness: () => get<Ok<'/api/v1/reports/freshness'>>('/api/v1/reports/freshness'),
    },
    operator: {
      ingestionHealth: (p: { window_min?: number } = {}) =>
        get<Ok<'/api/v1/operator/ingestion/health'>>('/api/v1/operator/ingestion/health', p),
      rejects: (p: RejectsParams = {}) =>
        get<Ok<'/api/v1/operator/rejects'>>('/api/v1/operator/rejects', p),
      runs: (p: RunsParams = {}) => get<Ok<'/api/v1/operator/runs'>>('/api/v1/operator/runs', p),
      run: (runId: number) =>
        get<Ok<'/api/v1/operator/runs/{run_id}'>>('/api/v1/operator/runs/{run_id}', undefined, {
          run_id: String(runId),
        }),
      enqueueRun: async (body: RunRequest) => {
        const { data } = await raw.POST('/api/v1/operator/runs', { body })
        return data as Schemas['PipelineRunOut']
      },
    },
  }
}

export type ParkingClient = ReturnType<typeof createParkingClient>
