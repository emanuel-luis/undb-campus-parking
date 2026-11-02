import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import { fmtNum } from '../lib/format'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export type ChartOption = echarts.EChartsCoreOption

// Same values as theme.css (canvas cannot read CSS variables); each has 3:1 or more on white.
const PALETTE = ['#CF004E', '#7A1F5C', '#1F7A4D', '#9A5B00', '#54595F', '#770131', '#B42318', '#1B0D17']
const AXIS = { axisLabel: { color: '#54595F' }, axisLine: { lineStyle: { color: '#54595F' } } }

export const THEME = {
  color: PALETTE,
  textStyle: { fontFamily: 'Roboto, system-ui, sans-serif', color: '#1B0D17' },
  categoryAxis: AXIS,
  valueAxis: { ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: fmtNum } },
  tooltip: { valueFormatter: fmtNum },
  legend: { textStyle: { color: '#1B0D17' } },
}
echarts.registerTheme('undb', THEME)

/** Thin ECharts wrapper: resize with its box, dispose on unmount. The text alternative lives in ChartPanel. */
export function EChart({ option, label, height = 300 }: { option: ChartOption; label: string; height?: number }) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const node = el.current
    if (!node) return undefined
    const instance = echarts.init(node, 'undb', { renderer: 'canvas' })
    chart.current = instance
    const ro = new ResizeObserver(() => instance.resize())
    ro.observe(node)
    return () => {
      ro.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption({ grid: { left: 48, right: 16, top: 40, bottom: 32 }, tooltip: { trigger: 'axis' }, ...option }, { notMerge: true })
  }, [option])

  return <div ref={el} role="img" aria-label={label} style={{ width: '100%', height }} />
}
