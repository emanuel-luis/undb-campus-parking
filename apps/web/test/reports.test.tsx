import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BOM } from '../src/lib/csv'
import { calledUrls, LOTS, problem, renderApp, type Routes } from './helpers'

const summary = {
  lot_id: 'L1', from_date: '2026-10-01', to_date: '2026-10-28', days: 28, entries: 1234, exits: 1200,
  avg_daily_entries: 44.1, avg_daily_unique_students: 30, avg_stay_min: 95.5, p90_stay_min: 240, avg_turnover: 1.2,
  min_free: 0, minutes_full: 45, closed_by_reconciliation: 3, exit_only: 1, missing_exit: 2, peak_occupied: 200,
}
const dailyRow = {
  lot_id: 'L1', service_date: '2026-10-27', entries: 50, exits: 48, entries_ma7: 45, avg_stay_min: 90, median_stay_min: 80,
  p90_stay_min: 200, capacity: 200, closed_by_reconciliation: 1, exit_only: 0, missing_exit: 0, min_free: 2, minutes_full: 0,
  peak_occupied: 198, peak_slot: '10:00', stays: 50, turnover: 0.25, unique_students: 40,
}

function routes(over: Routes = {}): Routes {
  return {
    '/api/v1/lots': LOTS,
    '/api/v1/reports/freshness': { age_s: 120, finished_at: '2026-10-28T15:00:00Z', last_run_id: 'r1', mode: 'incremental', status: 'succeeded' },
    '/api/v1/reports/summary': [summary],
    '/api/v1/reports/daily': [dailyRow],
    '/api/v1/reports/hourly': [{ lot_id: 'L1', service_date: '2026-10-27', hour: 8, entries: 30, exits: 2, avg_free: 50, avg_occupied: 150, capacity: 200, max_occupied: 170 }],
    '/api/v1/reports/weekday': [{ lot_id: 'L1', iso_day_of_week: 1, weekday_name: 'segunda-feira', avg_entries: 60, avg_exits: 58, avg_peak_occupied: 190, avg_unique_students: 40, days: 4 }],
    '/api/v1/reports/dwell': [{ lot_id: 'L1', bucket_order: 1, duration_bucket: '<30m', stays: 12 }],
    '/api/v1/reports/quality': [
      { lot_id: 'L1', service_date: '2026-10-27', closed_by_reconciliation: 1, exit_only: 0, missing_exit: 0, rejects_total: 2, rejects_direction_mismatch: 0,
        rejects_event_id_conflict: 0, rejects_in_future: 0, rejects_lot_mismatch: 0, rejects_schema_invalid: 2, rejects_too_old: 0, rejects_unknown_gate: 0, rejects_unknown_student: 0 },
    ],
    '/api/v1/reports/occupancy': [
      { lot_id: 'L1', service_date: '2026-10-27', slot_key: 40, slot_start: '2026-10-27T13:00:00Z', capacity: 200, free_end: 4, free_min: 1, occupied_avg: 196, occupied_end: 196, occupied_max: 199, seconds_full: 0 },
    ],
    ...over,
  }
}

describe('Relatórios', () => {
  it('shows freshness, KPI cards and every chart panel', async () => {
    renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes())
    expect(await screen.findByText('1.234')).toBeTruthy()
    expect(screen.getByText('Dados atualizados em 28/10/2026 12:00.')).toBeTruthy()
    const card = screen.getByRole('heading', { name: 'Estacionamento Principal', level: 3 }).closest('article')!
    expect(within(card).getByText('95,5 min')).toBeTruthy()
    for (const title of ['Entradas e saídas por dia', 'Perfil por hora', 'Comparação por dia da semana', 'Permanência', 'Rotatividade por dia', 'Qualidade dos dados por dia']) {
      expect(await screen.findByRole('img', { name: title })).toBeTruthy()
    }
    expect(await screen.findByText('Estacionamento Principal: mínimo de 1 vagas livres, 0 minutos lotado.')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: 'Baixar CSV' }).length).toBe(7)
  })

  it('downloads a CSV of the chart rows with BOM and ; separator', async () => {
    const blobs: Blob[] = []
    // jsdom has no object URLs.
    Object.assign(URL, { createObjectURL: (b: Blob) => (blobs.push(b), 'blob:x'), revokeObjectURL: () => undefined })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const { user } = renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes())
    const panel = (await screen.findByRole('img', { name: 'Entradas e saídas por dia' })).closest('section')!
    await user.click(within(panel).getByRole('button', { name: 'Baixar CSV' }))
    expect(click).toHaveBeenCalled()
    const bytes = new Uint8Array(await blobs[0]!.arrayBuffer())
    expect([...bytes.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]) // UTF-8 BOM
    expect(new TextDecoder('utf-8', { ignoreBOM: true }).decode(bytes)).toBe(BOM +'Data;Entradas;Saídas;Média de 7 dias (entradas)\r\n2026-10-27;50;48;45\r\n')
  })

  it('keeps the filters in the URL and sends them to the API', async () => {
    const { fetch, user } = renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes())
    const select = await screen.findByRole('combobox', { name: 'Estacionamento' })
    await screen.findByRole('option', { name: 'Estacionamento Anexo' })
    await user.selectOptions(select, 'L2')
    expect(screen.getByTestId('location').textContent).toBe('/relatorios?from=2026-10-01&to=2026-10-28&lot=L2')
    await waitFor(() => expect(calledUrls(fetch).some((u) => u.includes('/reports/summary?') && u.includes('lot_id=L2'))).toBe(true))
    await user.selectOptions(select, '')
    expect(screen.getByTestId('location').textContent).toBe('/relatorios?from=2026-10-01&to=2026-10-28')
  })

  it('rejects an inverted range and hides the hourly profile beyond 31 days', async () => {
    const view = renderApp('/relatorios?from=2026-10-20&to=2026-10-01', routes())
    expect(screen.getByRole('alert').textContent).toMatch(/período válido/)
    view.unmount()
    renderApp('/relatorios?from=2026-01-01&to=2026-10-01', routes())
    expect(await screen.findByText('Escolha um período de até 31 dias para ver o perfil por hora.')).toBeTruthy()
  })

  it('warns about stale data and a failed refresh', async () => {
    renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes({
      '/api/v1/reports/freshness': { age_s: 4000, finished_at: '2026-10-28T15:00:00Z', last_run_id: 'r1', mode: 'full', status: 'failed' },
    }))
    expect(await screen.findByText(/Dados desatualizados: última atualização há 1 h/)).toBeTruthy()
    expect(screen.getByText(/A última atualização falhou/)).toBeTruthy()
  })

  it('shows a friendly state while the warehouse is not ready', async () => {
    const notReady = () => problem(503, 'warehouse-not-ready', 'Warehouse not built yet')
    renderApp('/relatorios', routes({
      '/api/v1/reports/freshness': notReady,
      '/api/v1/reports/summary': notReady,
      '/api/v1/reports/daily': notReady,
    }))
    expect((await screen.findAllByText(/ainda estão sendo preparados/)).length).toBeGreaterThan(2)
  })

  it('shows empty states and a never-built warehouse', async () => {
    renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes({
      '/api/v1/reports/freshness': { age_s: null, finished_at: null, last_run_id: null, mode: null, status: null },
      '/api/v1/reports/summary': [],
      '/api/v1/reports/daily': [],
      '/api/v1/reports/hourly': [],
      '/api/v1/reports/weekday': [],
      '/api/v1/reports/dwell': [],
      '/api/v1/reports/quality': [],
      '/api/v1/reports/occupancy': [],
    }))
    expect(await screen.findByText('Os relatórios ainda não foram gerados.')).toBeTruthy()
    await waitFor(() => expect(screen.getAllByText('Sem dados no período.').length).toBe(7))
    expect(screen.getByText('Sem dados de ocupação neste dia.')).toBeTruthy()
  })

  it('shows a network error', async () => {
    renderApp('/relatorios?from=2026-10-01&to=2026-10-28', routes({ '/api/v1/reports/summary': () => Promise.reject(new TypeError('Failed to fetch')) }))
    expect(await screen.findByText(/Sem conexão com o servidor/)).toBeTruthy()
  })
})
