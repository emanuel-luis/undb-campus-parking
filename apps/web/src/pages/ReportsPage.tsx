import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import type { ReportRange, Schemas } from '@undb-campus-parking/api-client'
import { parkingQueries } from '@undb-campus-parking/api-client/react'
import { useMemo, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { useClient } from '../api'
import { ChartPanel, ErrorBox, Kpi, queryState } from '../components/ui'
import { isWarehouseNotReady } from '../lib/errors'
import { addDays, daysInRange, fmtAge, fmtDate, fmtDateTime, fmtNum, todayLocal } from '../lib/format'
import { dwellChart, hourlyChart, qualityChart, trafficChart, turnoverChart, weekdayChart, type ChartSpec } from '../lib/reports'
import { daySummary, freeCurveOption, SLOT_COLUMNS } from './LotDetailPage'

export const STALE_AFTER_S = 30 * 60
export const HOURLY_MAX_DAYS = 31
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function Freshness({ q }: { q: UseQueryResult<Schemas['ReportFreshness']> }) {
  if (q.isPending) return null
  if (q.isError) {
    return isWarehouseNotReady(q.error) ? (
      <div className="banner banner-warn" role="status">
        Os relatórios ainda estão sendo preparados. Tente de novo em alguns minutos.
      </div>
    ) : (
      <ErrorBox error={q.error} />
    )
  }
  const f = q.data
  if (f.finished_at === null || f.age_s === null) {
    return (
      <div className="banner banner-warn" role="status">
        Os relatórios ainda não foram gerados.
      </div>
    )
  }
  const stale = f.age_s > STALE_AFTER_S
  return (
    <div className={`banner ${stale ? 'banner-warn' : 'banner-info'}`} role="status">
      {stale
        ? `Dados desatualizados: última atualização há ${fmtAge(f.age_s)} (${fmtDateTime(f.finished_at)}).`
        : `Dados atualizados em ${fmtDateTime(f.finished_at)}.`}
      {f.status === 'failed' && ' A última atualização falhou; os números são da anterior.'}
    </div>
  )
}

/** A report chart: the query's loading, error or empty state, else the panel built from its rows. */
function Report<R, T>({ q, title, filename, build }: { q: UseQueryResult<R[]>; title: string; filename: string; build: (rows: R[]) => ChartSpec<T> }) {
  const rows = q.data
  const spec = useMemo(() => (rows && rows.length > 0 ? build(rows) : null), [rows, build])
  const state = queryState(q, !spec, 'Sem dados no período.')
  if (state || !spec) {
    return (
      <section className="panel">
        <h3>{title}</h3>
        {state}
      </section>
    )
  }
  return <ChartPanel title={title} filename={filename} {...spec} />
}

export function ReportsPage() {
  const client = useClient()
  const [params, setParams] = useSearchParams()
  const today = todayLocal()
  const to = params.get('to') ?? today
  const from = params.get('from') ?? addDays(to, -29)
  const lot = params.get('lot') ?? ''
  const day = params.get('day') ?? to
  const valid = DATE_RE.test(from) && DATE_RE.test(to) && from <= to
  const range: ReportRange = lot ? { from, to, lot_id: lot } : { from, to }
  const hourlyOk = valid && daysInRange(from, to) <= HOURLY_MAX_DAYS
  const suffix = `${lot || 'todos'}-${from}-${to}`

  const set = (key: string, value: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )

  const lots = useQuery(parkingQueries.lots(client))
  const freshness = useQuery(parkingQueries.freshness(client))
  const summary = useQuery({ ...parkingQueries.summary(client, range), enabled: valid })
  const daily = useQuery({ ...parkingQueries.daily(client, range), enabled: valid })
  const hourly = useQuery({ ...parkingQueries.hourly(client, range), enabled: hourlyOk })
  const weekday = useQuery({ ...parkingQueries.weekday(client, range), enabled: valid })
  const dwell = useQuery({ ...parkingQueries.dwell(client, range), enabled: valid })
  const quality = useQuery({ ...parkingQueries.quality(client, range), enabled: valid })
  const occupancy = useQuery(parkingQueries.occupancy(client, lot ? { date: day, lot_id: lot } : { date: day }))

  const names = useMemo(() => new Map((lots.data ?? []).map((l) => [l.lot_id, l.name])), [lots.data])
  const dailyRows = daily.data
  const buildDwell = useMemo(() => (rows: Schemas['DwellBucketRow'][]) => dwellChart(rows, dailyRows ?? [], names), [dailyRows, names])
  const buildTurnover = useMemo(() => (rows: Schemas['LotDailyRow'][]) => turnoverChart(rows, names), [names])
  const curve = useMemo(() => freeCurveOption(occupancy.data ?? [], names), [occupancy.data, names])

  let content: ReactNode
  if (!valid) {
    content = (
      <p className="state state-error" role="alert">
        Escolha um período válido: a data inicial deve ser igual ou anterior à final.
      </p>
    )
  } else {
    const slots = occupancy.data ?? []
    const perLot = [...new Set(slots.map((s) => s.lot_id))].map((id) => {
      const s = daySummary(slots.filter((x) => x.lot_id === id))
      return `${names.get(id) ?? id}: mínimo de ${fmtNum(s.minFree)} vagas livres, ${fmtNum(s.minutesFull)} minutos lotado`
    })
    content = (
      <>
        <section aria-labelledby="kpis">
          <h2 id="kpis">Resumo do período</h2>
          {queryState(summary, (summary.data ?? []).length === 0, 'Sem dados no período.') ?? (
            <div className="grid">
              {summary.data?.map((s) => (
                <article key={s.lot_id} className="card">
                  <h3>{names.get(s.lot_id) ?? s.lot_id}</h3>
                  <dl className="kpis">
                    <Kpi label="Entradas" value={fmtNum(s.entries)} />
                    <Kpi label="Saídas" value={fmtNum(s.exits)} />
                    <Kpi label="Entradas por dia (média)" value={fmtNum(s.avg_daily_entries)} />
                    <Kpi label="Permanência média" value={`${fmtNum(s.avg_stay_min)} min`} />
                    <Kpi label="P90 (média diária)" value={`${fmtNum(s.p90_stay_min)} min`} />
                    <Kpi label="Rotatividade" value={fmtNum(s.avg_turnover)} />
                    <Kpi label="Vagas livres (mínimo)" value={fmtNum(s.min_free)} />
                    <Kpi label="Minutos lotado" value={fmtNum(s.minutes_full)} />
                    <Kpi label="Fechadas na reconciliação" value={fmtNum(s.closed_by_reconciliation)} />
                  </dl>
                </article>
              ))}
            </div>
          )}
        </section>

        <h2>Movimento</h2>
        <Report q={daily} title="Entradas e saídas por dia" filename={`entradas-saidas-${suffix}.csv`} build={trafficChart} />
        {hourlyOk ? (
          <Report q={hourly} title="Perfil por hora" filename={`perfil-hora-${suffix}.csv`} build={hourlyChart} />
        ) : (
          <section className="panel">
            <h3>Perfil por hora</h3>
            <p className="state">Escolha um período de até {HOURLY_MAX_DAYS} dias para ver o perfil por hora.</p>
          </section>
        )}
        <Report q={weekday} title="Comparação por dia da semana" filename={`dia-semana-${suffix}.csv`} build={weekdayChart} />

        <h2>Vagas livres</h2>
        <section className="panel" aria-labelledby="curve">
          <h3 id="curve">Vagas livres em {fmtDate(day)}</h3>
          <label>
            Dia{' '}
            <input type="date" value={day} max={today} onChange={(e) => set('day', e.target.value)} />
          </label>
          {queryState(occupancy, slots.length === 0, 'Sem dados de ocupação neste dia.') ?? (
            <ChartPanel
              title={`Curva de vagas livres (${fmtDate(day)})`}
              summary={`${perLot.join('. ')}.`}
              option={curve}
              columns={SLOT_COLUMNS}
              rows={slots}
              filename={`vagas-livres-${lot || 'todos'}-${day}.csv`}
            />
          )}
        </section>

        <h2>Permanência e rotatividade</h2>
        <Report q={dwell} title="Permanência" filename={`permanencia-${suffix}.csv`} build={buildDwell} />
        <Report q={daily} title="Rotatividade por dia" filename={`rotatividade-${suffix}.csv`} build={buildTurnover} />

        <h2>Qualidade dos dados</h2>
        <Report q={quality} title="Qualidade dos dados por dia" filename={`qualidade-${suffix}.csv`} build={qualityChart} />
      </>
    )
  }

  return (
    <section>
      <h1>Relatórios</h1>
      <form className="filters" onSubmit={(e) => e.preventDefault()} aria-label="Filtros">
        <label>
          De <input type="date" value={from} max={today} onChange={(e) => set('from', e.target.value)} />
        </label>
        <label>
          Até <input type="date" value={to} max={today} onChange={(e) => set('to', e.target.value)} />
        </label>
        <label>
          Estacionamento{' '}
          <select value={lot} onChange={(e) => set('lot', e.target.value)}>
            <option value="">Todos</option>
            {lots.data?.map((l) => (
              <option key={l.lot_id} value={l.lot_id}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      </form>
      <Freshness q={freshness} />
      {content}
    </section>
  )
}
