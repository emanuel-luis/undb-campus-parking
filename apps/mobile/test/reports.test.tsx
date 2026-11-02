import { fireEvent, render, screen } from '@testing-library/react-native'
import { ReportsScreen } from '../src/screens/ReportsScreen'
import { MissingConfigScreen } from '../src/screens/MissingConfigScreen'
import type { LotDailyRow } from '../src/types'
import { problem, renderApp, type Routes } from './helpers'

const lots = [
  { lot_id: 'A', name: 'Estacionamento Principal', capacity: 200, capacity_history: [], gates: [] },
  { lot_id: 'B', name: 'Bloco B', capacity: 50, capacity_history: [], gates: [] },
]
const summaryRow = {
  lot_id: 'A',
  from_date: '2026-09-29',
  to_date: '2026-10-28',
  days: 30,
  entries: 4321,
  exits: 4300,
  avg_daily_entries: 144,
  avg_daily_unique_students: 100,
  avg_stay_min: 95.5,
  p90_stay_min: 240,
  avg_turnover: 1.5,
  min_free: 3,
  minutes_full: 45,
  peak_occupied: 197,
  closed_by_reconciliation: 2,
  exit_only: 1,
  missing_exit: 0,
}
const daily = [
  { lot_id: 'A', service_date: '2026-10-27', entries: 150, entries_ma7: 140 },
  { lot_id: 'A', service_date: '2026-10-28', entries: 130, entries_ma7: 142 },
] as LotDailyRow[]

const routes = (over: Routes = {}): Routes => ({
  '/lots': { body: lots },
  '/reports/freshness': { body: { finished_at: '2026-10-28T13:05:00Z', age_s: 60, last_run_id: 'r', mode: 'incremental', status: 'succeeded' } },
  '/reports/summary': { body: [summaryRow] },
  '/reports/daily': { body: daily },
  '/reports/dwell': { body: [{ lot_id: 'A', bucket_order: 1, duration_bucket: '<30m', stays: 12 }] },
  '/reports/occupancy': { body: [] },
  ...over,
})

test('loading, then freshness, KPIs and charts', async () => {
  const { fetch } = await renderApp(<ReportsScreen />, routes())
  expect(screen.getByLabelText('Carregando')).toBeTruthy()
  expect(await screen.findByLabelText('Entradas: 4.321')).toBeTruthy()
  expect(screen.getByLabelText('Permanência média: 95,5 min')).toBeTruthy()
  expect(screen.getByLabelText('Rotatividade: 1,5')).toBeTruthy()
  expect(screen.getByText('Dados atualizados em 28/10/2026, 10:05')).toBeTruthy()
  expect(await screen.findByLabelText(/Entradas por dia: 280 no período, em 2 dias/)).toBeTruthy()
  expect(await screen.findByLabelText('Permanência: <30m, 12.')).toBeTruthy()
  expect(screen.getByText(/Escolha um estacionamento/)).toBeTruthy()

  await fireEvent.press(screen.getByText('7 dias'))
  await screen.findByLabelText('Entradas: 4.321')
  const urls = fetch.mock.calls.map(([req]) => (req as Request).url)
  expect(urls.some((u) => u.includes('/reports/summary') && u.includes('from='))).toBe(true)

  await fireEvent.press(screen.getByText('Bloco B'))
  expect(await screen.findByText('Sem dados de ocupação para este dia.')).toBeTruthy()
  expect(fetch.mock.calls.some(([req]) => (req as Request).url.includes('lot_id=B'))).toBe(true)
})

test('warehouse not ready shows a friendly state', async () => {
  await renderApp(<ReportsScreen />, routes({ '/reports/summary': problem(503, 'warehouse-not-ready'), '/reports/freshness': problem(503, 'warehouse-not-ready') }))
  expect(await screen.findByText(/Os relatórios ainda estão sendo preparados/)).toBeTruthy()
  expect(screen.getByText('Dados ainda sem atualização do armazém.')).toBeTruthy()
})

test('empty range', async () => {
  await renderApp(<ReportsScreen />, routes({ '/reports/summary': { body: [] } }))
  expect(await screen.findByText('Sem dados no período.')).toBeTruthy()
})

test('missing API address explains how to set it', async () => {
  await render(<MissingConfigScreen />)
  expect(screen.getByText(/EXPO_PUBLIC_API_BASE_URL/)).toBeTruthy()
  expect(screen.getByLabelText('UNDB')).toBeTruthy()
  expect(screen.getByText(/Projeto acadêmico não oficial/)).toBeTruthy()
})
