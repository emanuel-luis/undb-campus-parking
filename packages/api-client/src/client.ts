import createClient from 'openapi-fetch'
import { problemFromResponse, ProblemError } from './problem'
import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type RunRequest = Schemas['RunRequest']

/** Query parameters of a GET route, taken from the generated schema so a backend rename breaks the build. */
type Query<P extends keyof paths> = paths[P] extends { get: { parameters: { query?: infer Q } } }
  ? NonNullable<Q>
  : never

/** Date range and lot filter shared by the report routes (dates are local, YYYY-MM-DD). */
export type ReportRange = Query<'/api/v1/reports/daily'>
export type OccupancyParams = Query<'/api/v1/reports/occupancy'>
export type RejectsParams = Query<'/api/v1/operator/rejects'>
export type RunsParams = Query<'/api/v1/operator/runs'>
export type IngestionHealthParams = Query<'/api/v1/operator/ingestion/health'>

export interface ParkingClientOptions {
  /** API origin, for example `http://localhost:8000` (no `/api/v1`). */
  baseUrl: string
  fetch?: typeof globalThis.fetch
  /** Sent as X-API-Key on the operator routes only, never on public ones. */
  operatorKey?: string
}

const OPERATOR_PREFIX = '/api/v1/operator/'
const REPORT_PREFIX = '/api/v1/reports/'
const MAX_CACHED_REPORTS = 32

/**
 * Typed client for the parking API. Every non-2xx response throws a `ProblemError`; a network
 * failure (offline, DNS, CORS) is the runtime's own fetch `TypeError`, not a `ProblemError`, so
 * callers handle both.
 */
export function createParkingClient(options: ParkingClientOptions) {
  const baseUrl = options.baseUrl.replace(/\/+$/, '')
  const doFetch = options.fetch ?? ((...args: Parameters<typeof fetch>) => globalThis.fetch(...args))
  const raw = createClient<paths>({ baseUrl, fetch: doFetch })
  // Last 200 body per report URL, at most MAX_CACHED_REPORTS (oldest dropped). A browser
  // revalidates through its HTTP cache and never shows a 304 to script; a runtime without one
  // (React Native, Node) can, so a 304 is answered from here.
  const reportBodies = new Map<string, string>()

  raw.use({
    onRequest({ request }) {
      if (options.operatorKey !== undefined && new URL(request.url).pathname.startsWith(OPERATOR_PREFIX)) {
        request.headers.set('X-API-Key', options.operatorKey)
      }
      return request
    },
    async onResponse({ request, response }) {
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
      if (new URL(request.url).pathname.startsWith(REPORT_PREFIX)) {
        reportBodies.delete(request.url) // re-insert as the newest
        reportBodies.set(request.url, await response.clone().text())
        if (reportBodies.size > MAX_CACHED_REPORTS) {
          const oldest = reportBodies.keys().next().value
          if (oldest !== undefined) reportBodies.delete(oldest)
        }
      }
      return undefined
    },
  })

  // The middleware throws on any error response, so `data` is set whenever a call returns.
  const ok = <T>(result: { data?: T }): T => result.data as T

  return {
    baseUrl,
    fetch: doFetch,
    raw,
    lots: async () => ok(await raw.GET('/api/v1/lots')),
    availability: async () => ok(await raw.GET('/api/v1/availability')),
    lotAvailability: async (lotId: string) =>
      ok(await raw.GET('/api/v1/lots/{lot_id}/availability', { params: { path: { lot_id: lotId } } })),
    reports: {
      summary: async (query: ReportRange = {}) =>
        ok(await raw.GET('/api/v1/reports/summary', { params: { query } })),
      daily: async (query: ReportRange = {}) => ok(await raw.GET('/api/v1/reports/daily', { params: { query } })),
      hourly: async (query: ReportRange = {}) =>
        ok(await raw.GET('/api/v1/reports/hourly', { params: { query } })),
      weekday: async (query: ReportRange = {}) =>
        ok(await raw.GET('/api/v1/reports/weekday', { params: { query } })),
      dwell: async (query: ReportRange = {}) => ok(await raw.GET('/api/v1/reports/dwell', { params: { query } })),
      quality: async (query: ReportRange = {}) =>
        ok(await raw.GET('/api/v1/reports/quality', { params: { query } })),
      occupancy: async (query: OccupancyParams = {}) =>
        ok(await raw.GET('/api/v1/reports/occupancy', { params: { query } })),
      freshness: async () => ok(await raw.GET('/api/v1/reports/freshness')),
    },
    operator: {
      ingestionHealth: async (query: IngestionHealthParams = {}) =>
        ok(await raw.GET('/api/v1/operator/ingestion/health', { params: { query } })),
      rejects: async (query: RejectsParams = {}) =>
        ok(await raw.GET('/api/v1/operator/rejects', { params: { query } })),
      runs: async (query: RunsParams = {}) => ok(await raw.GET('/api/v1/operator/runs', { params: { query } })),
      run: async (runId: string) =>
        ok(await raw.GET('/api/v1/operator/runs/{run_id}', { params: { path: { run_id: runId } } })),
      enqueueRun: async (body: RunRequest) => ok(await raw.POST('/api/v1/operator/runs', { body })),
    },
  }
}

export type ParkingClient = ReturnType<typeof createParkingClient>
