import type { ReactNode } from 'react'
import { StyleSheet, View } from 'react-native'
import Svg, { Circle, Line, Path, Rect } from 'react-native-svg'
import { bars, isolatedPoints, linePath, niceMax, yScale } from './chartPaths'
import { formatInt, formatShortDate, formatTime } from './format'
import type { DwellBucketRow, LotDailyRow, OccupancySlot } from './types'
import { colors } from './theme'
import { T } from './ui'

const W = 300
const H = 120

// ---- aggregation (pure) ----

/** Entries and the 7-day mean per date, summed over lots (sums of means stay means of sums). */
export function dailyTotals(rows: LotDailyRow[]): { day: string; entries: number; ma7: number | null }[] {
  // The 7-day line is summed across lots only on days where every lot has one: a partial sum would
  // dip for no reason (the same rule as the web app).
  const byDay = new Map<string, { day: string; entries: number; ma7: number; partial: boolean }>()
  for (const r of rows) {
    const d = byDay.get(r.service_date) ?? { day: r.service_date, entries: 0, ma7: 0, partial: false }
    d.entries += r.entries
    if (r.entries_ma7 === null) d.partial = true
    else d.ma7 += r.entries_ma7
    byDay.set(r.service_date, d)
  }
  return [...byDay.values()]
    .map(({ day, entries, ma7, partial }) => ({ day, entries, ma7: partial ? null : ma7 }))
    .sort((a, b) => (a.day < b.day ? -1 : 1))
}

export function dwellTotals(rows: DwellBucketRow[]): { bucket: string; stays: number }[] {
  const byOrder = new Map<number, { bucket: string; stays: number }>()
  for (const r of rows) {
    const b = byOrder.get(r.bucket_order) ?? { bucket: r.duration_bucket, stays: 0 }
    b.stays += r.stays
    byOrder.set(r.bucket_order, b)
  }
  return [...byOrder.entries()].sort(([a], [b]) => a - b).map(([, v]) => v)
}

/** Lowest free count and whole minutes at zero free over the slots of one lot. */
export function curveStats(slots: OccupancySlot[]): { min: number | null; minutesFull: number } {
  if (slots.length === 0) return { min: null, minutesFull: 0 }
  return {
    min: Math.min(...slots.map((x) => x.free_min)),
    minutesFull: Math.round(slots.reduce((acc, x) => acc + x.seconds_full, 0) / 60),
  }
}

// ---- components ----

function Frame({ summary, children, from, to }: { summary: string; children: ReactNode; from: string; to: string }) {
  return (
    <View style={st.frame} accessible accessibilityRole="image" accessibilityLabel={summary}>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {children}
      </Svg>
      <View style={st.axis}>
        <T variant="muted">{from}</T>
        <T variant="muted">{to}</T>
      </View>
    </View>
  )
}

/** Free spaces per 15-min slot (lowest of each slot) with the capacity as a dashed line. */
export function FreeCurve({ slots, name }: { slots: OccupancySlot[]; name: string }) {
  const first = slots[0]
  const last = slots[slots.length - 1]
  if (!first || !last) return <T variant="muted">Sem dados de ocupação para este dia.</T>
  const cap = Math.max(...slots.map((x) => x.capacity))
  const max = niceMax([cap, ...slots.map((x) => x.free_min)])
  const { min, minutesFull } = curveStats(slots)
  const capY = yScale(cap, max, H)
  return (
    <Frame
      from={formatTime(first.slot_start)}
      to={formatTime(last.slot_start)}
      summary={`${name}: vagas livres por intervalo de 15 minutos. Mínimo de ${formatInt(min)} vagas livres de ${formatInt(cap)}; ${formatInt(minutesFull)} minutos lotado.`}
    >
      <Line x1={0} x2={W} y1={capY} y2={capY} stroke={colors.muted} strokeDasharray="4 4" strokeWidth={1} />
      <Path d={linePath(slots.map((x) => x.free_min), W, H, max)} stroke={colors.primary} strokeWidth={2} fill="none" />
      {isolatedPoints(slots.map((x) => x.free_min), W, H, max).map((p, i) => (
        <Circle key={i} cx={p.x} cy={p.y} r={3} fill={colors.primary} />
      ))}
    </Frame>
  )
}

export function DailyChart({ rows }: { rows: LotDailyRow[] }) {
  const days = dailyTotals(rows)
  const first = days[0]
  const last = days[days.length - 1]
  if (!first || !last) return <T variant="muted">Sem entradas no período.</T>
  const max = niceMax(days.flatMap((d) => [d.entries, d.ma7]))
  const total = days.reduce((a, d) => a + d.entries, 0)
  return (
    <Frame
      from={formatShortDate(first.day)}
      to={formatShortDate(last.day)}
      summary={`Entradas por dia: ${formatInt(total)} no período, em ${days.length} dias. A linha mostra a média de 7 dias.`}
    >
      {bars(days.map((d) => d.entries), W, H, max).map((b, i) => (
        <Rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill={colors.primary} opacity={0.35} />
      ))}
      <Path d={linePath(days.map((d) => d.ma7), W, H, max)} stroke={colors.primaryStrong} strokeWidth={2} fill="none" />
      {isolatedPoints(days.map((d) => d.ma7), W, H, max).map((p, i) => (
        <Circle key={i} cx={p.x} cy={p.y} r={3} fill={colors.primaryStrong} />
      ))}
    </Frame>
  )
}

export function DwellChart({ rows }: { rows: DwellBucketRow[] }) {
  const buckets = dwellTotals(rows)
  const first = buckets[0]
  const last = buckets[buckets.length - 1]
  if (!first || !last) return <T variant="muted">Sem permanências no período.</T>
  const max = niceMax(buckets.map((b) => b.stays))
  return (
    <Frame
      from={first.bucket}
      to={last.bucket}
      summary={`Permanência: ${buckets.map((b) => `${b.bucket}, ${formatInt(b.stays)}`).join('; ')}.`}
    >
      {bars(buckets.map((b) => b.stays), W, H, max, 8).map((b, i) => (
        <Rect key={i} x={b.x} y={b.y} width={b.w} height={b.h} fill={colors.purple} />
      ))}
    </Frame>
  )
}

const st = StyleSheet.create({
  frame: { backgroundColor: colors.surface, borderRadius: 10, padding: 12, gap: 4 },
  axis: { flexDirection: 'row', justifyContent: 'space-between' },
})
