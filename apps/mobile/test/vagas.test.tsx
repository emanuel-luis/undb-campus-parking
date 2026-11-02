import { act, fireEvent, screen } from '@testing-library/react-native'
import { router } from 'expo-router'
import { AppState, type AppStateStatus } from 'react-native'
import { VagasScreen } from '../src/screens/VagasScreen'
import { FakeSocket, lotA, lotB, renderApp, snapshot } from './helpers'

jest.mock('expo-router', () => ({ router: { push: jest.fn() } }))

test('shows loading, then a card per lot from REST', async () => {
  await renderApp(<VagasScreen />, { '/availability': { body: snapshot([lotA, lotB]) } })
  expect(screen.getByLabelText('Carregando')).toBeTruthy()
  expect(await screen.findByLabelText('Estacionamento Principal: 42 vagas livres de 200. Quase cheio')).toBeTruthy()
  expect(screen.getByText('Lotado')).toBeTruthy()
  expect(screen.getByText('Acima da capacidade')).toBeTruthy()
  expect(screen.getByText(/Projeto acadêmico não oficial/)).toBeTruthy()
})

test('live frames update the cards and the status chip', async () => {
  await renderApp(<VagasScreen />, { '/availability': { body: snapshot([lotA]) } })
  await screen.findByText('Estacionamento Principal')
  const ws = FakeSocket.last()
  expect(ws.url).toBe('ws://api.test/api/v1/live/availability')
  await act(async () => {
    ws.onopen?.({})
    ws.onmessage?.({ data: JSON.stringify(snapshot([{ ...lotA, free: 7, occupancy_pct: 96.5, version: 9 }])) })
  })
  expect(screen.getByText('Ao vivo')).toBeTruthy()
  expect(screen.getByLabelText('Estacionamento Principal: 7 vagas livres de 200. Lotado')).toBeTruthy()
  expect(screen.getByText(/Atualizado há \d+ s/)).toBeTruthy()
})

test('empty state', async () => {
  await renderApp(<VagasScreen />, { '/availability': { body: snapshot([]) } })
  expect(await screen.findByText('Nenhum estacionamento cadastrado.')).toBeTruthy()
})

test('network error state', async () => {
  await renderApp(<VagasScreen />, { '/availability': new TypeError('Network request failed') })
  expect(await screen.findByText(/Sem conexão com a API/)).toBeTruthy()
})

test('opens the lot detail on press', async () => {
  await renderApp(<VagasScreen />, { '/availability': { body: snapshot([lotA]) } })
  await fireEvent.press(await screen.findByLabelText(/Estacionamento Principal: 42/))
  expect(router.push).toHaveBeenCalledWith('/estacionamentos/A')
})

test('closes the socket in the background and reconnects when active', async () => {
  let onChange: ((s: AppStateStatus) => void) | undefined
  const spy = jest.spyOn(AppState, 'addEventListener').mockImplementation((_type, fn) => {
    onChange = fn as (s: AppStateStatus) => void
    return { remove: jest.fn() }
  })
  await renderApp(<VagasScreen />, { '/availability': { body: snapshot([lotA]) } })
  await screen.findByText('Estacionamento Principal')
  const first = FakeSocket.last()
  await act(async () => onChange?.('background'))
  expect(first.closed).toBe(true)
  expect(FakeSocket.all).toHaveLength(1)
  await act(async () => onChange?.('active'))
  expect(FakeSocket.all).toHaveLength(2)
  expect(FakeSocket.last().closed).toBe(false)
  spy.mockRestore()
})
