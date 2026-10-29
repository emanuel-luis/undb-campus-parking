import { queryOptions } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { OccupancyParams, ParkingClient, RejectsParams, ReportRange, RunsParams } from './client'
import { createLiveAvailability, type AvailabilitySnapshot, type LiveStatus } from './live'

/** Sort keys and drop undefined so equal filters always give equal query keys. */
function norm(params: object | undefined): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(params ?? {})
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : 1)),
  )
}

export const parkingKeys = {
  all: ['parking'] as const,
  lots: () => ['parking', 'lots'] as const,
  availability: () => ['parking', 'availability'] as const,
  lotAvailability: (lotId: string) => ['parking', 'availability', lotId] as const,
  report: (name: string, params?: object) => ['parking', 'reports', name, norm(params)] as const,
  operator: (name: string, params?: object) => ['parking', 'operator', name, norm(params)] as const,
}

export const parkingQueries = {
  lots: (c: ParkingClient) => queryOptions({ queryKey: parkingKeys.lots(), queryFn: () => c.lots() }),
  availability: (c: ParkingClient) =>
    queryOptions({ queryKey: parkingKeys.availability(), queryFn: () => c.availability() }),
  lotAvailability: (c: ParkingClient, lotId: string) =>
    queryOptions({ queryKey: parkingKeys.lotAvailability(lotId), queryFn: () => c.lotAvailability(lotId) }),
  summary: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('summary', p), queryFn: () => c.reports.summary(p) }),
  daily: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('daily', p), queryFn: () => c.reports.daily(p) }),
  hourly: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('hourly', p), queryFn: () => c.reports.hourly(p) }),
  weekday: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('weekday', p), queryFn: () => c.reports.weekday(p) }),
  dwell: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('dwell', p), queryFn: () => c.reports.dwell(p) }),
  quality: (c: ParkingClient, p: ReportRange = {}) =>
    queryOptions({ queryKey: parkingKeys.report('quality', p), queryFn: () => c.reports.quality(p) }),
  occupancy: (c: ParkingClient, p: OccupancyParams = {}) =>
    queryOptions({ queryKey: parkingKeys.report('occupancy', p), queryFn: () => c.reports.occupancy(p) }),
  freshness: (c: ParkingClient) =>
    queryOptions({ queryKey: parkingKeys.report('freshness'), queryFn: () => c.reports.freshness() }),
  ingestionHealth: (c: ParkingClient, p: { window_min?: number } = {}) =>
    queryOptions({
      queryKey: parkingKeys.operator('ingestion-health', p),
      queryFn: () => c.operator.ingestionHealth(p),
    }),
  rejects: (c: ParkingClient, p: RejectsParams = {}) =>
    queryOptions({ queryKey: parkingKeys.operator('rejects', p), queryFn: () => c.operator.rejects(p) }),
  runs: (c: ParkingClient, p: RunsParams = {}) =>
    queryOptions({ queryKey: parkingKeys.operator('runs', p), queryFn: () => c.operator.runs(p) }),
  run: (c: ParkingClient, runId: number) =>
    queryOptions({ queryKey: parkingKeys.operator('run', { runId }), queryFn: () => c.operator.run(runId) }),
}

export interface UseLiveAvailabilityOptions {
  /** Set false to close the socket (a backgrounded app); defaults to true. */
  enabled?: boolean
}

/** Live availability for react-dom and react-native. The page-hidden case on the web is handled here. */
export function useLiveAvailability(client: ParkingClient, options: UseLiveAvailabilityOptions = {}) {
  const enabled = options.enabled ?? true
  const [snapshot, setSnapshot] = useState<AvailabilitySnapshot | null>(null)
  const [status, setStatus] = useState<LiveStatus>('closed')
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)

  useEffect(() => {
    if (!enabled) return undefined
    const live = createLiveAvailability({
      baseUrl: client.baseUrl,
      fetch: client.fetch,
      onSnapshot: (s) => {
        setSnapshot(s)
        setLastUpdate(new Date())
      },
      onStatus: setStatus,
    })
    const hasDocument = typeof document !== 'undefined'
    const onVisibility = () => (document.visibilityState === 'hidden' ? live.pause() : live.resume())
    if (hasDocument) document.addEventListener('visibilitychange', onVisibility)
    live.start()
    return () => {
      if (hasDocument) document.removeEventListener('visibilitychange', onVisibility)
      live.close()
    }
  }, [client, enabled])

  return { snapshot, status, lastUpdate }
}
