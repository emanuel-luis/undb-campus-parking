import { useQuery } from '@tanstack/react-query'
import type { Schemas } from '@undb-campus-parking/api-client'
import { parkingQueries, useLiveAvailability } from '@undb-campus-parking/api-client/react'
import { Link, useParams } from 'react-router'
import { useClient } from '../api'
import { LiveChip, LotCard } from '../components/live'
import { ChartPanel, Empty, Loading, queryState } from '../components/ui'
import { fmtNum, slotLabel, todayLocal } from '../lib/format'

type Slot = Schemas['OccupancySlot']

/** Lowest free count and minutes at capacity over a day's slots. */
export function daySummary(slots: Slot[]): { minFree: number | null; minutesFull: number } {
  const minFree = slots.length === 0 ? null : Math.min(...slots.map((s) => s.free_min))
  return { minFree, minutesFull: Math.round(slots.reduce((n, s) => n + s.seconds_full, 0) / 60) }
}

export const SLOT_COLUMNS = [
  { label: 'Estacionamento', value: (s: Slot) => s.lot_id },
  { label: 'Data', value: (s: Slot) => s.service_date },
  { label: 'Horário', value: (s: Slot) => slotLabel(s.slot_key) },
  { label: 'Vagas livres', value: (s: Slot) => s.free_end },
  { label: 'Vagas livres (mínimo)', value: (s: Slot) => s.free_min },
  { label: 'Capacidade', value: (s: Slot) => s.capacity },
  { label: 'Minutos lotado', value: (s: Slot) => Math.round(s.seconds_full / 60) },
]

/** Free spaces per 15-minute slot, one line per lot, plus the capacity line when there is one lot. */
export function freeCurveOption(slots: Slot[], names: Map<string, string>) {
  const keys = [...new Set(slots.map((s) => s.slot_key))].sort((a, b) => a - b)
  const lots = [...new Set(slots.map((s) => s.lot_id))]
  const series: object[] = lots.map((lotId) => {
    const byKey = new Map(slots.filter((s) => s.lot_id === lotId).map((s) => [s.slot_key, s.free_end]))
    return { type: 'line', name: `Vagas livres - ${names.get(lotId) ?? lotId}`, showSymbol: false, data: keys.map((k) => byKey.get(k) ?? null) }
  })
  if (lots.length === 1) {
    const cap = new Map(slots.map((s) => [s.slot_key, s.capacity]))
    series.push({ type: 'line', name: 'Capacidade', showSymbol: false, lineStyle: { type: 'dashed' }, data: keys.map((k) => cap.get(k) ?? null) })
  }
  return { legend: {}, xAxis: { type: 'category', data: keys.map(slotLabel) }, yAxis: { type: 'value', min: 0 }, series }
}

export function LotDetailPage() {
  const { lotId = '' } = useParams()
  const client = useClient()
  const { snapshot, status, lastUpdate } = useLiveAvailability(client)
  const lots = useQuery(parkingQueries.lots(client))
  const today = todayLocal()
  const occupancy = useQuery(parkingQueries.occupancy(client, { date: today, lot_id: lotId }))

  const live = snapshot?.lots.find((l) => l.lot_id === lotId)
  const info = lots.data?.find((l) => l.lot_id === lotId)
  const name = info?.name ?? live?.name ?? lotId
  const option = freeCurveOption(occupancy.data ?? [], new Map([[lotId, name]]))

  if (lots.isSuccess && !info && snapshot && !live) {
    return (
      <section>
        <h1>Estacionamento não encontrado</h1>
        <Link to="/">Voltar para Vagas</Link>
      </section>
    )
  }

  const slots = occupancy.data ?? []
  const { minFree, minutesFull } = daySummary(slots)

  return (
    <section>
      <p>
        <Link to="/">Voltar para Vagas</Link>
      </p>
      <h1>{name}</h1>
      <LiveChip status={status} lastUpdate={lastUpdate} hasData={snapshot !== null} />
      {live ? (
        <LotCard lot={live} title="Agora" />
      ) : snapshot ? (
        <Empty>Sem dados ao vivo para este estacionamento.</Empty>
      ) : (
        <Loading>Carregando vagas…</Loading>
      )}

      <h2>Hoje</h2>
      {queryState(occupancy, slots.length === 0, 'Ainda não há dados de ocupação hoje.') ?? (
        <ChartPanel
          title="Vagas livres ao longo do dia"
          summary={`Mínimo de vagas livres hoje: ${fmtNum(minFree)}. Minutos lotado: ${fmtNum(minutesFull)}.`}
          option={option}
          columns={SLOT_COLUMNS}
          rows={slots}
          filename={`vagas-livres-${lotId}-${today}.csv`}
        />
      )}

      <h2>Catracas</h2>
      {queryState(lots, (info?.gates.length ?? 0) === 0, 'Nenhuma catraca cadastrada.') ?? (
        <ul className="gates">
          {info?.gates.map((g) => (
            <li key={g.gate_id}>
              {g.name} <span className="muted">({g.direction === 'entry' ? 'Entrada' : 'Saída'})</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
