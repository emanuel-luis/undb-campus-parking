import { QueryClient } from '@tanstack/react-query'
import type { ParkingClient } from '@undb-campus-parking/api-client'
import { createContext, useContext } from 'react'
import { shouldRetry } from './lib/errors'

/** Empty means same origin: nginx (and the Vite dev proxy) forward /api to the backend. */
export function apiBaseUrl(configured: string | undefined, origin: string): string {
  return configured?.trim() || origin
}

export const ApiContext = createContext<ParkingClient | null>(null)

/** The public (anonymous) client, created once in main.tsx and injected by tests. */
export function useClient(): ParkingClient {
  const client = useContext(ApiContext)
  if (client === null) throw new Error('ApiContext is missing')
  return client
}

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } } })
}
