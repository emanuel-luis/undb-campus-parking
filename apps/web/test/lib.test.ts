import { ProblemError } from '@undb-campus-parking/api-client'
import { describe, expect, it } from 'vitest'
import { apiBaseUrl } from '../src/api'
import { liveLabel } from '../src/components/live'
import { BOM, toCsv } from '../src/lib/csv'
import { describeError, shouldRetry } from '../src/lib/errors'
import { addDays, daysInRange, fmtAge, fmtDate, fmtDateTime, fmtNum, fmtTime, slotLabel, todayLocal } from '../src/lib/format'
import { dwellChart, hourlyChart, qualityChart, trafficChart, turnoverChart, weekdayChart } from '../src/lib/reports'
import { announcements, lotStatus } from '../src/lib/status'
import { lotAvailability } from './helpers'

describe('lotStatus', () => {
  it.each([
    [0, 50, 'full'],
    [1, 95, 'full'],
    [10, 94.9, 'busy'],
    [10, 75, 'busy'],
    [100, 74.9, 'free'],
    [null, null, 'unknown'],
  ] as const)('free %s at %s%% is %s', (free, pct, status) => {
    expect(lotStatus(free, pct)).toBe(status)
  })
})

describe('announcements', () => {
  it('stays quiet on first sight and speaks at most once a minute per lot', () => {
    const memory = new Map<string, { free: number | null; at: number }>()
    expect(announcements(memory, [lotAvailability({ free: 42 })], 0)).toEqual([])
    expect(announcements(memory, [lotAvailability({ free: 41 })], 1_000)).toEqual(['Estacionamento Principal: 41 vagas livres'])
    expect(announcements(memory, [lotAvailability({ free: 40 })], 30_000)).toEqual([])
    expect(announcements(memory, [lotAvailability({ free: 40 })], 61_000)).toEqual(['Estacionamento Principal: 40 vagas livres'])
    expect(announcements(memory, [lotAvailability({ free: 40 })], 200_000)).toEqual([])
  })
})

describe('toCsv', () => {
  it('uses a BOM, the ; separator, decimal commas, CRLF and quotes what needs it', () => {
    const csv = toCsv(
      [
        { label: 'Nome', value: (r: { n: string; v: number | null }) => r.n },
        { label: 'Valor', value: (r) => r.v },
      ],
      [
        { n: 'Portão "Norte"; bloco A', v: 1234.5 },
        { n: 'linha\nnova', v: null },
      ],
    )
    expect(csv.startsWith(BOM)).toBe(true)
    expect(csv.slice(1)).toBe('Nome;Valor\r\n"Portão ""Norte""; bloco A";1234,5\r\n"linha\nnova";\r\n')
  })
})

describe('format', () => {
  it('formats in pt-BR and America/Fortaleza', () => {
    expect(fmtNum(1234.56)).toBe('1.234,6')
    expect(fmtNum(null)).toBe('-')
    expect(fmtDateTime('2026-10-28T15:05:00Z')).toBe('28/10/2026 12:05')
    expect(fmtDateTime(null)).toBe('-')
    expect(fmtTime('2026-10-28T03:00:00Z')).toBe('00:00')
    expect(fmtDate('2026-01-02')).toBe('02/01/2026')
    expect(fmtDate('x')).toBe('x')
    expect(todayLocal(new Date('2026-10-29T02:00:00Z'))).toBe('2026-10-28')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
    expect(daysInRange('2026-10-01', '2026-10-31')).toBe(31)
    expect([fmtAge(12), fmtAge(180), fmtAge(7200)]).toEqual(['12 s', '3 min', '2 h'])
    expect(slotLabel(53)).toBe('13:15')
  })

  it('falls back to the page origin when no API base URL is configured', () => {
    expect(apiBaseUrl(undefined, 'http://localhost:5173')).toBe('http://localhost:5173')
    expect(apiBaseUrl(' ', 'http://x')).toBe('http://x')
    expect(apiBaseUrl('http://api:8000', 'http://x')).toBe('http://api:8000')
  })

  it('labels each live status', () => {
    expect(liveLabel('open', true)).toBe('Ao vivo')
    expect(liveLabel('polling', true)).toBe('Sem conexão: atualizando a cada 10 s')
    expect(liveLabel('connecting', true)).toBe('Reconectando')
    expect(liveLabel('connecting', false)).toBe('Conectando')
    expect(liveLabel('paused', true)).toBe('Pausado')
  })
})

describe('errors', () => {
  it('describes problems, warehouse not ready and network failures', () => {
    expect(describeError(new ProblemError({ status: 503, code: 'warehouse-not-ready' }))).toMatch(/sendo preparados/)
    expect(describeError(new ProblemError({ status: 422, detail: 'Data inválida' }))).toBe('Data inválida')
    expect(describeError(new ProblemError({ status: 500 }))).toBe('Erro 500')
    expect(describeError(new TypeError('Failed to fetch'))).toMatch(/Sem conexão/)
    expect(shouldRetry(0, new ProblemError({ status: 404 }))).toBe(false)
    expect(shouldRetry(0, new ProblemError({ status: 503 }))).toBe(true)
    expect(shouldRetry(2, new TypeError('x'))).toBe(false)
  })
})

const daily = (over: Partial<Parameters<typeof trafficChart>[0][number]>) => ({
  lot_id: 'L1',
  service_date: '2026-10-01',
  entries: 10,
  exits: 9,
  entries_ma7: 8,
  avg_stay_min: 60,
  median_stay_min: 50,
  p90_stay_min: 120,
  capacity: 200,
  closed_by_reconciliation: 1,
  exit_only: 0,
  missing_exit: 0,
  min_free: 5,
  minutes_full: 0,
  peak_occupied: 195,
  peak_slot: '10:00',
  stays: 10,
  turnover: 0.05,
  unique_students: 9,
  ...over,
})

describe('report builders', () => {
  it('sums traffic over lots per day, in date order', () => {
    const spec = trafficChart([daily({ service_date: '2026-10-02' }), daily({}), daily({ lot_id: 'L2', entries_ma7: null })])
    expect(spec.rows).toEqual([
      { date: '2026-10-01', entries: 20, exits: 18, ma7: 8 },
      { date: '2026-10-02', entries: 10, exits: 9, ma7: 8 },
    ])
    expect(spec.summary).toBe('30 entradas em 2 dias, média de 15 por dia.')
  })

  it('averages the hourly profile over the days of the range', () => {
    const h = (d: string, hour: number, entries: number) => ({
      lot_id: 'L1', service_date: d, hour, entries, exits: 1, avg_free: null, avg_occupied: null, capacity: 200, max_occupied: null,
    })
    const spec = hourlyChart([h('2026-10-01', 8, 10), h('2026-10-02', 8, 20), h('2026-10-02', 9, 4)])
    expect(spec.rows[8]).toEqual({ hour: 8, entries: 15, exits: 1 })
    expect(spec.summary).toMatch(/08h, média de 15/)
  })

  it('orders weekdays and never pools dwell percentiles across lots', () => {
    const w = (iso: number, name: string, e: number) => ({
      lot_id: 'L1', iso_day_of_week: iso, weekday_name: name, avg_entries: e, avg_exits: e, avg_peak_occupied: null, avg_unique_students: 1, days: 4,
    })
    expect(weekdayChart([w(2, 'terça', 5), w(1, 'segunda', 9)]).summary).toBe('Dia com mais entradas em média: segunda (9).')
    const dwell = dwellChart(
      [
        { lot_id: 'L1', bucket_order: 2, duration_bucket: '30-60m', stays: 3 },
        { lot_id: 'L2', bucket_order: 2, duration_bucket: '30-60m', stays: 2 },
        { lot_id: 'L1', bucket_order: 1, duration_bucket: '<30m', stays: 1 },
      ],
      [daily({ p90_stay_min: 100 }), daily({ p90_stay_min: 140 }), daily({ lot_id: 'L2', p90_stay_min: 30 })],
      new Map([['L1', 'Principal']]),
    )
    expect(dwell.rows).toEqual([
      { order: 1, bucket: 'Até 30 min', stays: 1 },
      { order: 2, bucket: '30 a 60 min', stays: 5 },
    ])
    expect(dwell.summary).toContain('Principal: permanência média 60 min, mediana 50 min, P90 120 min')
    expect(dwell.summary).toContain('L2: permanência média 60 min, mediana 50 min, P90 30 min')
  })

  it('builds turnover lines per lot and quality sums per day', () => {
    const t = turnoverChart([daily({}), daily({ lot_id: 'L2', turnover: 0.5 })], new Map())
    expect((t.option as { series: unknown[] }).series).toHaveLength(2)
    const q = (date: string, lot: string) => ({
      lot_id: lot, service_date: date, closed_by_reconciliation: 2, exit_only: 1, missing_exit: 0, rejects_total: 3,
      rejects_direction_mismatch: 0, rejects_event_id_conflict: 0, rejects_in_future: 1, rejects_lot_mismatch: 0,
      rejects_schema_invalid: 2, rejects_too_old: 0, rejects_unknown_gate: 0, rejects_unknown_student: 0,
    })
    const spec = qualityChart([q('2026-10-01', 'L1'), q('2026-10-01', '-1')])
    expect(spec.rows[0]).toMatchObject({ total: 6, reconciled: 4, exitOnly: 2 })
    expect(spec.rows[0]?.rejects.schema_invalid).toBe(4)
    expect(spec.summary).toBe('No período: 6 rejeitados, 4 fechadas na reconciliação, 2 só com saída.')
  })
})
