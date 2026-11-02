import type { Schemas } from '@undb-campus-parking/api-client'

export type LotStatus = 'free' | 'busy' | 'full' | 'unknown'

export const FULL_PCT = 95
export const BUSY_PCT = 75

/** Full when no space is free or occupancy >= 95%, busy from 75%, otherwise free (no capacity: unknown). */
export function lotStatus(free: number | null, occupancyPct: number | null): LotStatus {
  if (free === null || occupancyPct === null) return 'unknown'
  if (free === 0 || occupancyPct >= FULL_PCT) return 'full'
  if (occupancyPct >= BUSY_PCT) return 'busy'
  return 'free'
}

export const STATUS_LABEL: Record<LotStatus, string> = {
  free: 'Livre',
  busy: 'Movimentado',
  full: 'Lotado',
  unknown: 'Sem capacidade cadastrada',
}

export const ANNOUNCE_EVERY_MS = 60_000

/**
 * Screen-reader messages for lots whose free count changed, at most one per lot per minute.
 * `memory` keeps, per lot, the value last announced (or first seen) and when.
 */
export function announcements(
  memory: Map<string, { free: number | null; at: number }>,
  lots: Schemas['LotAvailability'][],
  now: number,
): string[] {
  const out: string[] = []
  for (const lot of lots) {
    const seen = memory.get(lot.lot_id)
    if (seen === undefined) {
      memory.set(lot.lot_id, { free: lot.free, at: Number.NEGATIVE_INFINITY })
      continue
    }
    if (seen.free === lot.free || now - seen.at < ANNOUNCE_EVERY_MS) continue
    memory.set(lot.lot_id, { free: lot.free, at: now })
    out.push(`${lot.name}: ${lot.free ?? 0} vagas livres`)
  }
  return out
}
