import { act, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FakeSocket, LOTS, lotAvailability, problem, pushSnapshot, renderApp } from './helpers'

describe('Vagas', () => {
  it('shows loading until the first snapshot, then one card per lot', async () => {
    renderApp('/')
    expect(screen.getByText('Carregando vagas…')).toBeTruthy()
    expect(screen.getByText('Conectando')).toBeTruthy()
    expect(screen.getByText('Projeto acadêmico não oficial, sem vínculo com a UNDB nem endosso dela.')).toBeTruthy()

    act(() =>
      pushSnapshot([
        lotAvailability(),
        lotAvailability({ lot_id: 'L2', name: 'Anexo', free: 0, occupied: 85, capacity: 80, occupancy_pct: 106.3, over_capacity: true }),
        lotAvailability({ lot_id: 'L3', name: 'Sem cadastro', capacity: null, free: null, occupancy_pct: null }),
      ]),
    )
    expect(screen.getByText('Ao vivo')).toBeTruthy()
    expect(screen.getByText(/Atualizado há \d+ s/)).toBeTruthy()
    const principal = screen.getByRole('link', { name: 'Estacionamento Principal' }).closest('article')!
    expect(principal.textContent).toContain('42 vagas livres de 200')
    expect(within(principal).getByText('Movimentado')).toBeTruthy()
    const anexo = screen.getByRole('link', { name: 'Anexo' }).closest('article')!
    expect(within(anexo).getByText('Lotado')).toBeTruthy()
    expect(within(anexo).getByText('Acima da capacidade')).toBeTruthy()
    expect(within(anexo).getByRole('meter').getAttribute('aria-valuenow')).toBe('100')
    expect(screen.getByText('Sem capacidade cadastrada')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Anexo' }).getAttribute('href')).toBe('/estacionamentos/L2')
  })

  it('announces a change in the polite live region', () => {
    const { container } = renderApp('/')
    act(() => pushSnapshot([lotAvailability()]))
    act(() => FakeSocket.latest().onmessage?.({ data: JSON.stringify({ generated_at: 'x', lots: [lotAvailability({ free: 41, version: 2 })] }) }))
    expect(container.querySelector('[aria-live="polite"]')?.textContent).toBe('Estacionamento Principal: 41 vagas livres')
  })

  it('shows the empty state', () => {
    renderApp('/')
    act(() => pushSnapshot([]))
    expect(screen.getByText('Nenhum estacionamento cadastrado.')).toBeTruthy()
  })

  it('shows the error state while the socket is down and polling fails', async () => {
    renderApp('/', { '/api/v1/availability': () => problem(503, 'unavailable', 'down') })
    act(() => FakeSocket.latest().onclose?.({}))
    expect(await screen.findByText(/Não foi possível carregar as vagas/)).toBeTruthy()
    expect(screen.getByText('Sem conexão: atualizando a cada 10 s')).toBeTruthy()
  })

  it('closes the socket while the page is hidden', () => {
    renderApp('/')
    act(() => pushSnapshot([lotAvailability()]))
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(FakeSocket.latest().closed).toBe(true)
    expect(screen.getByText('Pausado')).toBeTruthy()
  })

  it('has a skip link and the header logo', () => {
    renderApp('/nao-existe')
    expect(screen.getByRole('link', { name: 'Pular para o conteúdo' }).getAttribute('href')).toBe('#conteudo')
    expect(screen.getByAltText('UNDB')).toBeTruthy()
    expect(screen.getByText('Página não encontrada')).toBeTruthy()
    expect(LOTS).toHaveLength(2)
  })
})
