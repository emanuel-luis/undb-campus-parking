import { lazy, Suspense, useId, type ReactNode } from 'react'
import { downloadCsv, toCsv, type Column } from '../lib/csv'
import { describeError, isProblem } from '../lib/errors'
import { fmtNum } from '../lib/format'
import type { ChartOption } from './EChart'

// ECharts is most of the bundle: load it with the first chart, not with the Vagas page.
const EChart = lazy(() => import('./EChart').then((m) => ({ default: m.EChart })))

const cellText = (v: string | number | null | undefined): string => (typeof v === 'number' ? fmtNum(v) : (v ?? ''))

export const NOTICE = 'Projeto acadêmico não oficial, sem vínculo com a UNDB nem endosso dela.'

export function Loading({ children = 'Carregando…' }: { children?: ReactNode }) {
  return (
    <p className="state" role="status">
      {children}
    </p>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="state">{children}</p>
}

/** API or network error with the request id when the server sent one. */
export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="state state-error" role="alert">
      <p>{describeError(error)}</p>
      {isProblem(error) && error.requestId && <p className="muted">Código da requisição: {error.requestId}</p>}
    </div>
  )
}

/** Loading, error or empty state for one query; null when the data can be shown. */
export function queryState(q: { isPending: boolean; isError: boolean; error: unknown }, empty: boolean, emptyText: string) {
  if (q.isPending) return <Loading />
  if (q.isError) return <ErrorBox error={q.error} />
  if (empty) return <Empty>{emptyText}</Empty>
  return null
}

export interface ChartPanelProps<T> {
  title: string
  /** One-sentence reading of the chart, shown under it. */
  summary: ReactNode
  option: ChartOption
  columns: Column<T>[]
  rows: T[]
  filename: string
}

/** Chart with a "Baixar CSV" button and a visually hidden table holding the same rows. */
export function ChartPanel<T>({ title, summary, option, columns, rows, filename }: ChartPanelProps<T>) {
  const id = useId()
  return (
    <section className="panel" aria-labelledby={id}>
      <div className="panel-head">
        <h3 id={id}>{title}</h3>
        <button type="button" className="btn btn-ghost" onClick={() => downloadCsv(filename, toCsv(columns, rows))}>
          Baixar CSV
        </button>
      </div>
      <Suspense fallback={<div className="chart-placeholder" />}>
        <EChart option={option} label={title} />
      </Suspense>
      <p className="caption">{summary}</p>
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.label} scope="col">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.label}>{cellText(c.value(r))}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="kpi">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
