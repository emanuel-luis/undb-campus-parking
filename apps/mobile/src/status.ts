import { colors } from './theme'

export type LotStatus = 'free' | 'busy' | 'full' | 'unknown'

/** Full when nothing is free or occupancy >= 95%, busy from 75%, otherwise free (same rule as the web). */
export function lotStatus(free: number | null, occupancyPct: number | null): LotStatus {
  if (free === null || occupancyPct === null) return 'unknown'
  if (free <= 0 || occupancyPct >= 95) return 'full'
  if (occupancyPct >= 75) return 'busy'
  return 'free'
}

export const statusColor: Record<LotStatus, string> = {
  free: colors.free,
  busy: colors.busy,
  full: colors.full,
  unknown: colors.muted,
}

export const statusLabel: Record<LotStatus, string> = {
  free: 'Com vagas',
  busy: 'Quase cheio',
  full: 'Lotado',
  unknown: 'Sem capacidade definida',
}
