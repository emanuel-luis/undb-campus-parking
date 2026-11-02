import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KEY_STORAGE } from '../src/pages/OperatorPage'
import { calledUrls, problem, renderApp, type Routes } from './helpers'

const run = {
  run_id: 'run-1', job: 'warehouse', mode: 'incremental', status: 'running', trigger: 'operator', service_date: '2026-10-28',
  requested_at: '2026-10-28T15:00:00Z', started_at: '2026-10-28T15:00:05Z', finished_at: null, error: null, metrics: null,
  parent_run_id: null, rows_read: 10, rows_written: 9, rows_rejected: 1,
}

function routes(over: Routes = {}): Routes {
  return {
    '/api/v1/operator/ingestion/health': {
      gates: [{ gate_id: 'G1', lot_id: 'L1', last_event_at: '2026-10-28T15:00:00Z', last_received_at: '2026-10-28T15:00:01Z' }],
      lag: { avg_s: 1.5, max_s: 4 },
      last_batch_at: '2026-10-28T15:00:01Z',
      per_minute: [{ minute: '2026-10-28T15:00:00Z', accepted: 10, duplicates: 1, rejected: 2 }],
      rejects_by_reason: { unknown_gate: 2 },
    },
    '/api/v1/operator/rejects': (url: URL) => ({
      items: [{
        reject_id: url.searchParams.get('cursor') ? 2 : 1, batch_id: 'b', detail: 'gate G9 is not registered', event_id: null, gate_id: 'G9',
        lot_id: null, payload: {}, reason: 'unknown_gate', received_at: '2026-10-28T15:00:00Z', service_date: null,
      }],
      next_cursor: url.searchParams.get('cursor') ? null : 'c2',
    }),
    '/api/v1/operator/runs': (_: URL, req: Request) => (req.method === 'POST' ? { ...run, run_id: 'run-2', status: 'queued' } : [run]),
    ...over,
  }
}

async function login(path = '/operador', r: Routes = routes()) {
  const view = renderApp(path, r)
  expect(screen.getByText('Projeto acadêmico não oficial, sem vínculo com a UNDB nem endosso dela.', { selector: '.notice' })).toBeTruthy()
  await view.user.type(screen.getByLabelText('Chave do operador'), 'op-key')
  await view.user.click(screen.getByRole('button', { name: 'Entrar' }))
  return view
}

describe('Operador', () => {
  it('logs in, keeps the key in sessionStorage only and shows health, rejects and runs', async () => {
    const { fetch } = await login()
    expect(sessionStorage.getItem(KEY_STORAGE)).toBe('op-key')
    expect(await screen.findByText(/média 1,5 s, máximo 4 s/)).toBeTruthy()
    expect(await screen.findByRole('img', { name: 'Eventos por minuto' })).toBeTruthy()
    expect(screen.getByText('Catraca desconhecida: 2')).toBeTruthy()
    expect(await screen.findByText('gate G9 is not registered')).toBeTruthy()
    expect(await screen.findByText('Em execução')).toBeTruthy()
    expect(screen.getByText('lidas 10, gravadas 9, rejeitadas 1')).toBeTruthy()
    const req = fetch.mock.calls.map(([r]) => r as Request).find((r) => r.url.includes('/operator/runs'))
    expect(req?.headers.get('X-API-Key')).toBe('op-key')
  })

  it('pages the rejects with the cursor and filters by reason', async () => {
    const { fetch, user } = await login()
    await user.click(await screen.findByRole('button', { name: 'Próxima página' }))
    await waitFor(() => expect(calledUrls(fetch).some((u) => u.includes('cursor=c2'))).toBe(true))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Página anterior' }).hasAttribute('disabled')).toBe(false))
    await user.click(screen.getByRole('button', { name: 'Página anterior' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Motivo' }), 'too_old')
    await waitFor(() => expect(calledUrls(fetch).some((u) => u.includes('reason=too_old'))).toBe(true))
  })

  it('goes back to the login on a 401', async () => {
    const unauthorized = () => problem(401, 'unauthorized', 'Invalid API key')
    await login('/operador', routes({ '/api/v1/operator/ingestion/health': unauthorized }))
    expect(await screen.findByText('Chave inválida ou expirada. Entre de novo.')).toBeTruthy()
    expect(sessionStorage.getItem(KEY_STORAGE)).toBeNull()
  })

  it('enqueues a run and shows 409 and 422 problems with the request id', async () => {
    let answer: () => Response | object = () => problem(409, 'run-already-active', 'A warehouse run is already queued', 'req-409')
    const { user } = await login('/operador', routes({
      '/api/v1/operator/runs': (_: URL, req: Request) => (req.method === 'POST' ? answer() : [run]),
    }))
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Job' }), 'warehouse')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Modo' }), 'full')
    await user.click(screen.getByRole('button', { name: 'Enfileirar' }))
    expect(await screen.findByText('Já existe uma execução ativa deste job para essa data.')).toBeTruthy()
    expect(screen.getByText('Código da requisição: req-409')).toBeTruthy()

    answer = () => problem(422, 'validation-error', 'service_date must not be in the future', 'req-422')
    await user.click(screen.getByRole('button', { name: 'Enfileirar' }))
    expect(await screen.findByText('service_date must not be in the future')).toBeTruthy()
    expect(screen.getByText('Código da requisição: req-422')).toBeTruthy()

    answer = () => ({ ...run, status: 'queued', service_date: '2026-10-27' })
    await user.click(screen.getByRole('button', { name: 'Enfileirar' }))
    expect(await screen.findByText('Execução enfileirada: warehouse de 27/10/2026.')).toBeTruthy()
  })

  it('clears the key on Sair and starts logged in when the tab already has one', async () => {
    sessionStorage.setItem(KEY_STORAGE, 'saved')
    const { user } = renderApp('/operador', routes({ '/api/v1/operator/runs': [], '/api/v1/operator/rejects': { items: [], next_cursor: null } }))
    expect(await screen.findByText('Nenhuma execução registrada.')).toBeTruthy()
    expect(screen.getByText('Nenhum evento rejeitado.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Sair' }))
    expect(sessionStorage.getItem(KEY_STORAGE)).toBeNull()
    expect(screen.getByLabelText('Chave do operador')).toBeTruthy()
    expect(screen.queryByText('Chave inválida ou expirada. Entre de novo.')).toBeNull()
  })
})
