import type { ParkingClient } from '@undb-campus-parking/api-client'
import { useLiveAvailability } from '@undb-campus-parking/api-client/react'
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { AppState, type AppStateStatus } from 'react-native'

type Live = ReturnType<typeof useLiveAvailability>

const ClientContext = createContext<ParkingClient | null>(null)
const LiveContext = createContext<Live | null>(null)

/** False while the app is in the background, so the live socket closes and reopens on return. */
export function useAppActive(): boolean {
  const [state, setState] = useState<AppStateStatus>(AppState.currentState)
  useEffect(() => {
    const sub = AppState.addEventListener('change', setState)
    return () => sub.remove()
  }, [])
  return state !== 'background'
}

/** A clock that ticks every `ms`, for "Atualizado há N s". */
export function useNow(ms = 1000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

/** One client and one live subscription for the whole app. `client` must be created once. */
export function ParkingProvider({ client, children }: { client: ParkingClient; children: ReactNode }) {
  const live = useLiveAvailability(client, { enabled: useAppActive() })
  return (
    <ClientContext.Provider value={client}>
      <LiveContext.Provider value={live}>{children}</LiveContext.Provider>
    </ClientContext.Provider>
  )
}

export function useClient(): ParkingClient {
  const c = useContext(ClientContext)
  if (c === null) throw new Error('useClient outside ParkingProvider')
  return c
}

export function useLive(): Live {
  const l = useContext(LiveContext)
  if (l === null) throw new Error('useLive outside ParkingProvider')
  return l
}
