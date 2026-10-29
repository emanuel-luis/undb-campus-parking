import type { components } from './schema'

export type Problem = components['schemas']['Problem']

/**
 * An error response of the API (RFC 9457 problem details). UIs show `detail` and `requestId`;
 * `code` is the stable machine-readable part (for example `warehouse-not-ready` with a
 * `retryAfter`, or `invalid-date-range`).
 */
export class ProblemError extends Error {
  readonly status: number
  readonly type: string
  readonly title: string
  readonly detail: string
  readonly code: string
  readonly requestId: string | null
  /** Seconds from the Retry-After header, when the server sent a numeric one. */
  readonly retryAfter: number | undefined
  readonly errors: Record<string, unknown>[] | null

  constructor(problem: Partial<Problem> & { status: number }, retryAfter?: number) {
    const detail = problem.detail ?? ''
    super(detail || problem.title || `HTTP ${problem.status}`)
    this.name = 'ProblemError'
    this.status = problem.status
    this.type = problem.type ?? 'about:blank'
    this.title = problem.title ?? ''
    this.detail = detail
    this.code = problem.code ?? 'unknown'
    this.requestId = problem.request_id ?? null
    this.retryAfter = retryAfter
    this.errors = problem.errors ?? null
  }
}

/** Build a ProblemError from a failed response; a body that is not a problem document still works. */
export async function problemFromResponse(response: Response): Promise<ProblemError> {
  let body: Partial<Problem> = {}
  try {
    const parsed: unknown = await response.json()
    if (parsed !== null && typeof parsed === 'object') body = parsed as Partial<Problem>
  } catch {
    // not JSON (a proxy error page, an empty body): keep the status line only
  }
  const header = response.headers.get('retry-after')
  const seconds = header === null || header.trim() === '' ? Number.NaN : Number(header)
  return new ProblemError(
    { ...body, status: response.status, title: body.title ?? response.statusText },
    Number.isFinite(seconds) ? seconds : undefined,
  )
}
