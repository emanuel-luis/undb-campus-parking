/** Every date and time on screen is shown in the service time zone, whatever the browser's. */
export const TIME_ZONE = 'America/Fortaleza'

const nf = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
const parts = new Intl.DateTimeFormat('pt-BR', {
  timeZone: TIME_ZONE,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
const isoDay = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE })

/** Number with pt-BR separators (1.234,5); a missing value shows as a dash. */
export function fmtNum(v: number | null | undefined): string {
  return v === null || v === undefined ? '-' : nf.format(v)
}

function pick(iso: string): Record<string, string> {
  return Object.fromEntries(parts.formatToParts(new Date(iso)).map((p) => [p.type, p.value]))
}

/** dd/MM/yyyy HH:mm in America/Fortaleza. */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const p = pick(iso)
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`
}

/** HH:mm in America/Fortaleza. */
export function fmtTime(iso: string): string {
  const p = pick(iso)
  return `${p.hour}:${p.minute}`
}

/** A local date (YYYY-MM-DD) as dd/MM/yyyy, with no time zone shift. */
export function fmtDate(day: string): string {
  const [y, m, d] = day.split('-')
  return y && m && d ? `${d}/${m}/${y}` : day
}

/** Today's local date (YYYY-MM-DD) in the service time zone. */
export function todayLocal(now: Date = new Date()): string {
  return isoDay.format(now)
}

/** Calendar arithmetic on a local date (YYYY-MM-DD). */
export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

/** Whole days from `from` to `to`, inclusive of both. */
export function daysInRange(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1
}

/** "12 s", "3 min" or "2 h" for an age in seconds. */
export function fmtAge(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))} s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`
  return `${Math.floor(seconds / 3600)} h`
}

/** 15-minute slot number (0 = 00:00) as HH:mm. */
export function slotLabel(slotKey: number): string {
  const minutes = slotKey * 15
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}
