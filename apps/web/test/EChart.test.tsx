import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.unmock('../src/components/EChart')
const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }
vi.mock('echarts/core', () => ({ use: vi.fn(), registerTheme: vi.fn(), init: vi.fn(() => instance) }))
vi.mock('echarts/charts', () => ({ BarChart: {}, LineChart: {} }))
vi.mock('echarts/components', () => ({ GridComponent: {}, LegendComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))

describe('EChart', () => {
  it('inits with the brand theme, sets the option, resizes and disposes', async () => {
    const observers: (() => void)[] = []
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: () => void) {
          observers.push(cb)
        }
        observe() {}
        disconnect() {}
      },
    )
    const { EChart, THEME } = await import('../src/components/EChart')
    const { getByRole, unmount } = render(<EChart option={{ series: [] }} label="Entradas" />)
    expect(getByRole('img', { name: 'Entradas' })).toBeTruthy()
    expect(instance.setOption).toHaveBeenCalledWith(expect.objectContaining({ series: [] }), { notMerge: true })
    observers[0]?.()
    expect(instance.resize).toHaveBeenCalled()
    unmount()
    expect(instance.dispose).toHaveBeenCalled()
    expect(THEME.valueAxis.axisLabel.formatter(10000)).toBe('10.000')
  })
})
