import { ProblemError } from '@undb-campus-parking/api-client'

export const isProblem = (e: unknown, status?: number): e is ProblemError =>
  e instanceof ProblemError && (status === undefined || e.status === status)

export const isWarehouseNotReady = (e: unknown): boolean => isProblem(e) && e.code === 'warehouse-not-ready'

/** pt-BR message for an API error (ProblemError) or a network failure (fetch TypeError). */
export function describeError(e: unknown): string {
  if (isWarehouseNotReady(e)) return 'Os relatórios ainda estão sendo preparados. Tente de novo em alguns minutos.'
  if (isProblem(e)) return e.detail || e.title || `Erro ${e.status}`
  return 'Sem conexão com o servidor. Verifique a rede e tente de novo.'
}

/** Retry server and network failures twice; a 4xx will not change on retry. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  return failureCount < 2 && !(isProblem(error) && error.status < 500)
}
