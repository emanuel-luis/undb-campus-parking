const TZ = 'America/Fortaleza'
const DAY_MS = 86_400_000

const intFmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 })
const decFmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 })
const dateFmt = new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric' })
const timeFmt = new Intl.DateTimeFormat('pt-BR', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const isoFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' })

export const formatInt = (n: number | null): string => (n === null ? '–' : intFmt.format(n))
export const formatDec = (n: number | null): string => (n === null ? '–' : decFmt.format(n))
export const formatMinutes = (n: number | null): string => (n === null ? '–' : `${decFmt.format(n)} min`)

/** dd/MM/yyyy, HH:mm in America/Fortaleza. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return `${dateFmt.format(d)}, ${timeFmt.format(d)}`
}

export const formatTime = (iso: string): string => timeFmt.format(new Date(iso))

/** YYYY-MM-DD → dd/MM (chart labels; no time zone math on a plain date). */
export function formatShortDate(day: string): string {
  const [, m, d] = day.split('-')
  return `${d}/${m}`
}

/** Local date (YYYY-MM-DD in America/Fortaleza) `daysAgo` days before `now`. Fortaleza has no DST. */
export function localDate(daysAgo = 0, now: Date = new Date()): string {
  return isoFmt.format(new Date(now.getTime() - daysAgo * DAY_MS))
}

/** "Atualizado há 12 s" / "há 3 min". */
export function updatedAgo(last: Date | null, now: Date): string {
  if (last === null) return 'Aguardando dados'
  const s = Math.max(0, Math.round((now.getTime() - last.getTime()) / 1000))
  return s < 60 ? `Atualizado há ${s} s` : `Atualizado há ${Math.floor(s / 60)} min`
}
