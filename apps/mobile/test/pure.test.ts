import { ProblemError } from '@undb-campus-parking/api-client'
import { bars, isolatedPoints, linePath, niceMax, xAt, yScale } from '../src/chartPaths'
import { curveStats, dailyTotals, dwellTotals } from '../src/charts'
import { formatDateTime, formatDec, formatInt, formatMinutes, formatShortDate, localDate, updatedAgo } from '../src/format'
import { lotStatus } from '../src/status'
import type { LotDailyRow, OccupancySlot } from '../src/types'
import { errorMessage } from '../src/ui'

test('status thresholds: full at 0 free or >= 95%, busy from 75%', () => {
  expect(lotStatus(10, 74.9)).toBe('free')
  expect(lotStatus(10, 75)).toBe('busy')
  expect(lotStatus(10, 94.9)).toBe('busy')
  expect(lotStatus(10, 95)).toBe('full')
  expect(lotStatus(0, 10)).toBe('full')
  expect(lotStatus(null, null)).toBe('unknown')
})

test('isolated points are returned for single values and values between nulls', () => {
  expect(linePath([5], 300, 100, 10)).toBe('M150 50') // a lone M draws nothing
  expect(isolatedPoints([5], 300, 100, 10)).toEqual([{ x: 150, y: 50 }])
  expect(isolatedPoints([0, null, 10, null, 5, 5], 500, 100, 10)).toEqual([
    { x: 0, y: 100 },
    { x: 200, y: 0 },
  ])
  expect(isolatedPoints([null, null], 300, 100, 10)).toEqual([])
  expect(isolatedPoints([1, 2, 3], 300, 100, 10)).toEqual([])
})

test('chart path builders give exact geometry', () => {
  expect(linePath([0, 5, null, 10], 300, 100, 10)).toBe('M0 100L100 50M300 0')
  expect(linePath([], 300, 100, 10)).toBe('')
  expect(bars([1, 2], 100, 50, 2)).toEqual([
    { x: 1, y: 25, w: 48, h: 25 },
    { x: 51, y: 0, w: 48, h: 50 },
  ])
  expect(bars([], 100, 50, 2)).toEqual([])
  expect(xAt(0, 1, 300)).toBe(150)
  expect(yScale(5, 0, 100)).toBe(100)
  expect(yScale(1, 3, 100)).toBe(66.7)
  expect(niceMax([null, 0])).toBe(1)
  expect(niceMax([3, null, 7])).toBe(7)
})

test('aggregation sums lots per day and per bucket', () => {
  const day = (lot: string, d: string, entries: number, ma7: number | null) => ({ lot_id: lot, service_date: d, entries, entries_ma7: ma7 }) as LotDailyRow
  expect(dailyTotals([day('A', '2026-10-02', 5, null), day('A', '2026-10-01', 3, 2), day('B', '2026-10-01', 4, 1)])).toEqual([
    { day: '2026-10-01', entries: 7, ma7: 3 },
    { day: '2026-10-02', entries: 5, ma7: null },
  ])
  // One lot without a 7-day value makes the summed line null for that day, not a partial sum.
  expect(dailyTotals([day('A', '2026-10-03', 3, 2), day('B', '2026-10-03', 4, null)])).toEqual([
    { day: '2026-10-03', entries: 7, ma7: null },
  ])
  expect(
    dwellTotals([
      { lot_id: 'A', bucket_order: 2, duration_bucket: '30-60m', stays: 1 },
      { lot_id: 'A', bucket_order: 1, duration_bucket: '<30m', stays: 2 },
      { lot_id: 'B', bucket_order: 1, duration_bucket: '<30m', stays: 3 },
    ]),
  ).toEqual([
    { bucket: '<30m', stays: 5 },
    { bucket: '30-60m', stays: 1 },
  ])
  const slot = (free_min: number, seconds_full: number) => ({ free_min, seconds_full }) as OccupancySlot
  expect(curveStats([slot(5, 0), slot(0, 90), slot(2, 60)])).toEqual({ min: 0, minutesFull: 3 })
  expect(curveStats([])).toEqual({ min: null, minutesFull: 0 })
})

test('formats in pt-BR and America/Fortaleza', () => {
  expect(formatInt(1234)).toBe('1.234')
  expect(formatInt(null)).toBe('–')
  expect(formatDec(2.5)).toBe('2,5')
  expect(formatMinutes(42)).toBe('42 min')
  expect(formatMinutes(null)).toBe('–')
  expect(formatDateTime('2026-10-28T13:05:00Z')).toBe('28/10/2026, 10:05')
  expect(formatShortDate('2026-10-28')).toBe('28/10')
  const now = new Date('2026-10-28T02:00:00Z') // still the 27th in Fortaleza (UTC-3)
  expect(localDate(0, now)).toBe('2026-10-27')
  expect(localDate(1, now)).toBe('2026-10-26')
  expect(updatedAgo(null, now)).toBe('Aguardando dados')
  expect(updatedAgo(new Date(now.getTime() - 12_000), now)).toBe('Atualizado há 12 s')
  expect(updatedAgo(new Date(now.getTime() - 125_000), now)).toBe('Atualizado há 2 min')
})

test('error messages cover problems and network failures', () => {
  expect(errorMessage(new ProblemError({ status: 503, code: 'warehouse-not-ready' }))).toMatch(/sendo preparados/)
  expect(errorMessage(new ProblemError({ status: 500, title: 'Internal', request_id: 'r1' }))).toBe('Erro 500: Internal (id r1)')
  expect(errorMessage(new TypeError('Network request failed'))).toMatch(/Sem conexão/)
})
