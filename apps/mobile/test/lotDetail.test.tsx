import { act, screen } from '@testing-library/react-native'
import { LotDetailScreen } from '../src/screens/LotDetailScreen'
import type { OccupancySlot } from '../src/types'
import { FakeSocket, lotA, problem, renderApp, snapshot } from './helpers'

const slot = (k: number, free: number, secondsFull = 0): OccupancySlot => ({
  lot_id: 'A',
  service_date: '2026-10-28',
  slot_key: k,
  slot_start: `2026-10-28T${String(10 + Math.floor(k / 4)).padStart(2, '0')}:${String((k % 4) * 15).padStart(2, '0')}:00Z`,
  capacity: 200,
  free_end: free,
  free_min: free,
  occupied_avg: 200 - free,
  occupied_end: 200 - free,
  occupied_max: 200 - free,
  seconds_full: secondsFull,
})

test('loading, then live numbers and today curve', async () => {
  await renderApp(<LotDetailScreen lotId="A" />, {
    '/lots/A/availability': { body: lotA },
    '/reports/occupancy': { body: [slot(0, 120), slot(1, 0, 600), slot(2, 30)] },
  })
  expect(screen.getAllByLabelText('Carregando').length).toBeGreaterThan(0)
  expect(await screen.findByLabelText(/Estacionamento Principal: vagas livres por intervalo de 15 minutos. Mínimo de 0/)).toBeTruthy()
  expect(screen.getByLabelText('Minutos lotado: 10')).toBeTruthy()
  expect(screen.getByLabelText(/Estacionamento Principal: 42 vagas livres de 200/)).toBeTruthy()
  expect(screen.getByText('07:00')).toBeTruthy() // 10:00Z in Fortaleza
})

test('empty curve', async () => {
  await renderApp(<LotDetailScreen lotId="A" />, {
    '/lots/A/availability': { body: lotA },
    '/reports/occupancy': { body: [] },
  })
  expect(await screen.findByText('Sem dados de ocupação para este dia.')).toBeTruthy()
})

test('an id missing from the live snapshot falls back to REST and shows the not-found error', async () => {
  await renderApp(<LotDetailScreen lotId="Z" />, {
    '/lots/Z/availability': problem(404, 'lot-not-found', 'Lot Z does not exist.'),
    '/reports/occupancy': { body: [] },
  })
  await act(async () => {
    const ws = FakeSocket.last()
    ws.onopen?.({})
    ws.onmessage?.({ data: JSON.stringify(snapshot([lotA])) })
  })
  expect(await screen.findByText('Erro 404: Lot Z does not exist. (id req-1)')).toBeTruthy()
  expect(screen.queryByLabelText('Estacionamento Principal: 42 vagas livres de 200')).toBeNull()
})

test('lot and curve errors', async () => {
  await renderApp(<LotDetailScreen lotId="Z" />, {
    '/lots/Z/availability': problem(404, 'lot-not-found', 'Lot Z does not exist.'),
    '/reports/occupancy': problem(503, 'warehouse-not-ready'),
  })
  expect(await screen.findByText('Erro 404: Lot Z does not exist. (id req-1)')).toBeTruthy()
  expect(await screen.findByText(/sendo preparados/)).toBeTruthy()
})
