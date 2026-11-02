import { act, screen } from '@testing-library/react'
import type { Schemas } from '@undb-campus-parking/api-client'
import { describe, expect, it } from 'vitest'
import { calledUrls, LOTS, lotAvailability, problem, pushSnapshot, renderApp } from './helpers'

const slot = (key: number, free: number, secondsFull = 0): Schemas['OccupancySlot'] => ({
  lot_id: 'L1',
  service_date: '2026-10-28',
  slot_key: key,
  slot_start: '2026-10-28T10:00:00Z',
  capacity: 200,
  free_end: free,
  free_min: free,
  occupied_avg: 200 - free,
  occupied_end: 200 - free,
  occupied_max: 200 - free,
  seconds_full: secondsFull,
})

describe('Lot detail', () => {
  it("shows live numbers, today's curve with minimum and minutes full, and the gates", async () => {
    const { fetch } = renderApp('/estacionamentos/L1', {
      '/api/v1/lots': LOTS,
      '/api/v1/reports/occupancy': [slot(32, 20), slot(33, 0, 600), slot(34, 3, 300)],
    })
    expect(screen.getByText('Carregando vagas…')).toBeTruthy()
    act(() => pushSnapshot([lotAvailability()]))
    expect(await screen.findByText('Mínimo de vagas livres hoje: 0. Minutos lotado: 15.')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: 'Estacionamento Principal' })).toBeTruthy()
    expect(document.querySelector('.lot-free')?.textContent).toBe('42 vagas livres de 200')
    expect(screen.getByText('Portão Norte')).toBeTruthy()
    expect(screen.getByText('(Saída)')).toBeTruthy()
    expect(screen.getByRole('table', { hidden: true }).textContent).toContain('08:15')
    expect(calledUrls(fetch).some((u) => u.includes('/reports/occupancy?') && u.includes('lot_id=L1') && u.includes('date='))).toBe(true)
  })

  it('shows the empty states', async () => {
    renderApp('/estacionamentos/L1', {
      '/api/v1/lots': LOTS,
      '/api/v1/reports/occupancy': [],
    })
    expect(await screen.findByText('Ainda não há dados de ocupação hoje.')).toBeTruthy()
    act(() => pushSnapshot([]))
    expect(await screen.findByText('Sem dados ao vivo para este estacionamento.')).toBeTruthy()
  })

  it('explains a warehouse that is not ready and shows the request id', async () => {
    renderApp('/estacionamentos/L1', {
      '/api/v1/lots': LOTS,
      '/api/v1/reports/occupancy': () => problem(503, 'warehouse-not-ready', 'not ready', 'req-9'),
    })
    expect(await screen.findByText(/ainda estão sendo preparados/)).toBeTruthy()
    expect(screen.getByText('Código da requisição: req-9')).toBeTruthy()
  })

  it('says when the lot does not exist', async () => {
    renderApp('/estacionamentos/X9', { '/api/v1/lots': LOTS, '/api/v1/reports/occupancy': [] })
    act(() => pushSnapshot([lotAvailability()]))
    expect(await screen.findByText('Estacionamento não encontrado')).toBeTruthy()
  })
})
