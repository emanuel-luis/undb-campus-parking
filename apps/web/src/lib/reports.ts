import type { Schemas } from '@undb-campus-parking/api-client'
import type { ChartOption } from '../components/EChart'
import type { Column } from './csv'
import { fmtDate, fmtNum } from './format'

type Daily = Schemas['LotDailyRow']
type Hourly = Schemas['LotHourlyRow']
type Weekday = Schemas['WeekdayRow']
type Dwell = Schemas['DwellBucketRow']
type Quality = Schemas['QualityDailyRow']

export interface ChartSpec<T> {
  rows: T[]
  columns: Column<T>[]
  option: ChartOption
  summary: string
}

interface Series {
  name: string
  type: 'bar' | 'line'
  data: (number | null)[]
  stack?: string
}

export function chartOption(categories: string[], series: Series[]): ChartOption {
  return {
    legend: { top: 0, type: 'scroll' },
    xAxis: { type: 'category', data: categories },
    yAxis: { type: 'value' },
    series: series.map((s) => ({ ...s, showSymbol: false })),
  }
}

const sortedKeys = <K extends string | number>(keys: Iterable<K>): K[] => [...new Set(keys)].sort((a, b) => (a < b ? -1 : 1))
const round1 = (v: number) => Math.round(v * 10) / 10
const mean = (vs: (number | null)[]): number | null => {
  const xs = vs.filter((v): v is number => v !== null)
  return xs.length === 0 ? null : xs.reduce((a, b) => a + b, 0) / xs.length
}

/** Entries, exits and the 7-day mean of entries per day, summed over the lots. */
export function trafficChart(rows: Daily[]): ChartSpec<{ date: string; entries: number; exits: number; ma7: number | null }> {
  const days = new Map<string, { date: string; entries: number; exits: number; ma7: number | null }>()
  for (const r of rows) {
    const d = days.get(r.service_date) ?? { date: r.service_date, entries: 0, exits: 0, ma7: null }
    d.entries += r.entries
    d.exits += r.exits
    if (r.entries_ma7 !== null) d.ma7 = round1((d.ma7 ?? 0) + r.entries_ma7)
    days.set(r.service_date, d)
  }
  const out = sortedKeys(days.keys()).map((k) => days.get(k)!)
  const total = out.reduce((n, d) => n + d.entries, 0)
  return {
    rows: out,
    columns: [
      { label: 'Data', value: (d) => d.date },
      { label: 'Entradas', value: (d) => d.entries },
      { label: 'Saídas', value: (d) => d.exits },
      { label: 'Média de 7 dias (entradas)', value: (d) => d.ma7 },
    ],
    option: chartOption(out.map((d) => fmtDate(d.date)), [
      { name: 'Entradas', type: 'bar', data: out.map((d) => d.entries) },
      { name: 'Saídas', type: 'bar', data: out.map((d) => d.exits) },
      { name: 'Média de 7 dias', type: 'line', data: out.map((d) => d.ma7) },
    ]),
    summary: `${fmtNum(total)} entradas em ${fmtNum(out.length)} dias, média de ${fmtNum(out.length ? total / out.length : 0)} por dia.`,
  }
}

/** Mean entries and exits per hour of the day over the days of the range (lots summed). */
export function hourlyChart(rows: Hourly[]): ChartSpec<{ hour: number; entries: number; exits: number }> {
  const days = new Set(rows.map((r) => r.service_date)).size || 1
  const out = Array.from({ length: 24 }, (_, hour) => ({ hour, entries: 0, exits: 0 }))
  for (const r of rows) {
    const h = out[r.hour]
    if (h) {
      h.entries += r.entries
      h.exits += r.exits
    }
  }
  for (const h of out) {
    h.entries = round1(h.entries / days)
    h.exits = round1(h.exits / days)
  }
  const peak = out.reduce((a, b) => (b.entries > a.entries ? b : a))
  return {
    rows: out,
    columns: [
      { label: 'Hora', value: (h) => `${String(h.hour).padStart(2, '0')}h` },
      { label: 'Entradas (média por dia)', value: (h) => h.entries },
      { label: 'Saídas (média por dia)', value: (h) => h.exits },
    ],
    option: chartOption(out.map((h) => `${String(h.hour).padStart(2, '0')}h`), [
      { name: 'Entradas', type: 'bar', data: out.map((h) => h.entries) },
      { name: 'Saídas', type: 'bar', data: out.map((h) => h.exits) },
    ]),
    summary: `Horário com mais entradas: ${String(peak.hour).padStart(2, '0')}h, média de ${fmtNum(peak.entries)} por dia.`,
  }
}

/** Mean entries and exits per weekday (holidays excluded by the API), lots summed. */
export function weekdayChart(rows: Weekday[]): ChartSpec<{ day: number; name: string; entries: number; exits: number }> {
  const days = new Map<number, { day: number; name: string; entries: number; exits: number }>()
  for (const r of rows) {
    const d = days.get(r.iso_day_of_week) ?? { day: r.iso_day_of_week, name: r.weekday_name, entries: 0, exits: 0 }
    d.entries = round1(d.entries + r.avg_entries)
    d.exits = round1(d.exits + r.avg_exits)
    days.set(r.iso_day_of_week, d)
  }
  const out = sortedKeys(days.keys()).map((k) => days.get(k)!)
  const busiest = out.reduce((a, b) => (b.entries > a.entries ? b : a), out[0] ?? { name: '-', entries: 0 })
  return {
    rows: out,
    columns: [
      { label: 'Dia da semana', value: (d) => d.name },
      { label: 'Entradas (média)', value: (d) => d.entries },
      { label: 'Saídas (média)', value: (d) => d.exits },
    ],
    option: chartOption(out.map((d) => d.name), [
      { name: 'Entradas', type: 'bar', data: out.map((d) => d.entries) },
      { name: 'Saídas', type: 'bar', data: out.map((d) => d.exits) },
    ]),
    summary: `Dia com mais entradas em média: ${busiest.name} (${fmtNum(busiest.entries)}).`,
  }
}

const BUCKET_LABEL: Record<Dwell['duration_bucket'], string> = {
  '<30m': 'Até 30 min',
  '30-60m': '30 a 60 min',
  '1-2h': '1 a 2 h',
  '2-4h': '2 a 4 h',
  '4-6h': '4 a 6 h',
  '>6h': 'Mais de 6 h',
}

/**
 * Closed stays per duration bucket (lots summed), and per lot the means of the daily average,
 * median and p90: percentiles are never pooled or averaged across lots.
 */
export function dwellChart(rows: Dwell[], daily: Daily[], names: Map<string, string>): ChartSpec<{ order: number; bucket: string; stays: number }> {
  const buckets = new Map<number, { order: number; bucket: string; stays: number }>()
  for (const r of rows) {
    const b = buckets.get(r.bucket_order) ?? { order: r.bucket_order, bucket: BUCKET_LABEL[r.duration_bucket], stays: 0 }
    b.stays += r.stays
    buckets.set(r.bucket_order, b)
  }
  const out = sortedKeys(buckets.keys()).map((k) => buckets.get(k)!)
  const stats = sortedKeys(daily.map((d) => d.lot_id)).map((lotId) => {
    const days = daily.filter((d) => d.lot_id === lotId)
    const m = (f: (d: Daily) => number | null) => fmtNum(mean(days.map(f)))
    return `${names.get(lotId) ?? lotId}: permanência média ${m((d) => d.avg_stay_min)} min, mediana ${m((d) => d.median_stay_min)} min, P90 ${m((d) => d.p90_stay_min)} min`
  })
  return {
    rows: out,
    columns: [
      { label: 'Duração', value: (b) => b.bucket },
      { label: 'Permanências', value: (b) => b.stays },
    ],
    option: chartOption(out.map((b) => b.bucket), [{ name: 'Permanências', type: 'bar', data: out.map((b) => b.stays) }]),
    summary: `${stats.join('. ')}${stats.length ? '. ' : ''}Valores por estacionamento são médias dos valores diários.`,
  }
}

/** Turnover (stays with an entry per space) per day, one line per lot. */
export function turnoverChart(rows: Daily[], names: Map<string, string>): ChartSpec<Daily> {
  const dates = sortedKeys(rows.map((r) => r.service_date))
  const lots = sortedKeys(rows.map((r) => r.lot_id))
  const series = lots.map((lotId) => {
    const byDate = new Map(rows.filter((r) => r.lot_id === lotId).map((r) => [r.service_date, r.turnover]))
    return { name: names.get(lotId) ?? lotId, type: 'line' as const, data: dates.map((d) => byDate.get(d) ?? null) }
  })
  return {
    rows,
    columns: [
      { label: 'Data', value: (r) => r.service_date },
      { label: 'Estacionamento', value: (r) => names.get(r.lot_id) ?? r.lot_id },
      { label: 'Rotatividade', value: (r) => r.turnover },
    ],
    option: chartOption(dates.map(fmtDate), series),
    summary: lots.map((l) => `${names.get(l) ?? l}: rotatividade média ${fmtNum(mean(rows.filter((r) => r.lot_id === l).map((r) => r.turnover)))}`).join('. '),
  }
}

export const REASONS = [
  ['schema_invalid', 'Formato inválido'],
  ['unknown_gate', 'Catraca desconhecida'],
  ['unknown_student', 'Aluno desconhecido'],
  ['lot_mismatch', 'Estacionamento divergente'],
  ['direction_mismatch', 'Sentido divergente'],
  ['in_future', 'Horário no futuro'],
  ['too_old', 'Evento antigo demais'],
  ['event_id_conflict', 'Conflito de ID do evento'],
] as const

export type Reason = (typeof REASONS)[number][0]
export const reasonLabel = (r: string): string => REASONS.find(([k]) => k === r)?.[1] ?? r

type QualityDay = { date: string; rejects: Record<Reason, number>; total: number; reconciled: number; exitOnly: number }

/** Rejects by reason, stays closed by reconciliation and exit-only stays per day (lots summed). */
export function qualityChart(rows: Quality[]): ChartSpec<QualityDay> {
  const days = new Map<string, QualityDay>()
  for (const r of rows) {
    const d =
      days.get(r.service_date) ??
      ({ date: r.service_date, rejects: Object.fromEntries(REASONS.map(([k]) => [k, 0])), total: 0, reconciled: 0, exitOnly: 0 } as QualityDay)
    for (const [k] of REASONS) d.rejects[k] += r[`rejects_${k}`]
    d.total += r.rejects_total
    d.reconciled += r.closed_by_reconciliation
    d.exitOnly += r.exit_only
    days.set(r.service_date, d)
  }
  const out = sortedKeys(days.keys()).map((k) => days.get(k)!)
  const sum = (f: (d: QualityDay) => number) => fmtNum(out.reduce((n, d) => n + f(d), 0))
  return {
    rows: out,
    columns: [
      { label: 'Data', value: (d) => d.date },
      ...REASONS.map(([k, label]) => ({ label: `Rejeitados: ${label}`, value: (d: QualityDay) => d.rejects[k] })),
      { label: 'Rejeitados', value: (d) => d.total },
      { label: 'Fechadas na reconciliação', value: (d) => d.reconciled },
      { label: 'Só saída', value: (d) => d.exitOnly },
    ],
    option: chartOption(out.map((d) => fmtDate(d.date)), [
      ...REASONS.map(([k, label]) => ({ name: label, type: 'bar' as const, stack: 'rejects', data: out.map((d) => d.rejects[k]) })),
      { name: 'Fechadas na reconciliação', type: 'line', data: out.map((d) => d.reconciled) },
      { name: 'Só saída', type: 'line', data: out.map((d) => d.exitOnly) },
    ]),
    summary: `No período: ${sum((d) => d.total)} rejeitados, ${sum((d) => d.reconciled)} fechadas na reconciliação, ${sum((d) => d.exitOnly)} só com saída.`,
  }
}
