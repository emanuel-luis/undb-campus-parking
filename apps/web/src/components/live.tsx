import type { LiveStatus, Schemas } from '@undb-campus-parking/api-client'
import { useEffect, useState, type ReactNode } from 'react'
import { fmtAge, fmtNum } from '../lib/format'
import { lotStatus, STATUS_LABEL } from '../lib/status'

/** The current time, re-read every `ms`, so "Atualizado há N s" ticks. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

export function liveLabel(status: LiveStatus, hasData: boolean): string {
  switch (status) {
    case 'open':
      return 'Ao vivo'
    case 'polling':
      return 'Sem conexão: atualizando a cada 10 s'
    case 'paused':
      return 'Pausado'
    default:
      return hasData ? 'Reconectando' : 'Conectando'
  }
}

export function LiveChip({ status, lastUpdate, hasData }: { status: LiveStatus; lastUpdate: Date | null; hasData: boolean }) {
  const now = useNow()
  return (
    <p className="live-line">
      <span className={`chip chip-${status}`}>{liveLabel(status, hasData)}</span>
      {lastUpdate && <span className="muted">Atualizado há {fmtAge((now - lastUpdate.getTime()) / 1000)}</span>}
    </p>
  )
}

/** Free spaces, capacity bar colored by status (with a text label, never color alone). */
export function LotCard({ lot, title }: { lot: Schemas['LotAvailability']; title: ReactNode }) {
  const status = lotStatus(lot.free, lot.occupancy_pct)
  const pct = Math.min(lot.occupancy_pct ?? 0, 100)
  return (
    <article className={`card lot lot-${status}`}>
      <h2 className="lot-title">{title}</h2>
      {lot.capacity === null ? (
        <p className="muted">{STATUS_LABEL.unknown}</p>
      ) : (
        <>
          <p className="lot-free">
            <strong>{fmtNum(lot.free)}</strong> vagas livres de {fmtNum(lot.capacity)}
          </p>
          <div
            className="bar"
            role="meter"
            aria-label="Ocupação"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-valuetext={`${fmtNum(lot.occupancy_pct)}% ocupado`}
          >
            <span style={{ width: `${pct}%` }} />
          </div>
          <p className="lot-meta">
            <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>
            <span className="muted">
              {fmtNum(lot.occupied)} ocupadas ({fmtNum(lot.occupancy_pct)}%)
            </span>
            {lot.over_capacity && <span className="badge badge-full">Acima da capacidade</span>}
          </p>
        </>
      )}
    </article>
  )
}
