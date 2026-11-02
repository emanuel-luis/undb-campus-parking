import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createParkingClient, type ParkingClient, type RunRequest, type Schemas } from '@undb-campus-parking/api-client'
import { parkingKeys, parkingQueries } from '@undb-campus-parking/api-client/react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useClient } from '../api'
import { ChartPanel, ErrorBox, NOTICE, queryState } from '../components/ui'
import { isProblem } from '../lib/errors'
import { fmtDate, fmtDateTime, fmtNum, fmtTime } from '../lib/format'
import { chartOption, reasonLabel, REASONS, type Reason } from '../lib/reports'

export const KEY_STORAGE = 'undb-parking.operator-key'
const PAGE_SIZE = 20
const RUNS_REFRESH_MS = 10_000

function readKey(): string | null {
  try {
    return sessionStorage.getItem(KEY_STORAGE)
  } catch {
    return null
  }
}

function writeKey(key: string | null): void {
  try {
    if (key === null) sessionStorage.removeItem(KEY_STORAGE)
    else sessionStorage.setItem(KEY_STORAGE, key)
  } catch {
    // storage blocked: the key lives in memory for this page only
  }
}

function Login({ onLogin, expired }: { onLogin: (key: string) => void; expired: boolean }) {
  const [key, setKey] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (key.trim()) onLogin(key.trim())
  }
  return (
    <section className="login card">
      <img src="/brand/2024-11-logo.png" alt="UNDB" width={204} height={62} />
      <h1>Área do operador</h1>
      {expired && (
        <p className="state state-error" role="alert">
          Chave inválida ou expirada. Entre de novo.
        </p>
      )}
      <form onSubmit={submit}>
        <label htmlFor="operator-key">Chave do operador</label>
        <input id="operator-key" type="password" autoComplete="off" required value={key} onChange={(e) => setKey(e.target.value)} />
        <button type="submit" className="btn">
          Entrar
        </button>
      </form>
      <p className="muted">A chave fica só nesta aba e é apagada ao sair.</p>
      <p className="notice">{NOTICE}</p>
    </section>
  )
}

export function OperatorPage() {
  const client = useClient()
  const queryClient = useQueryClient()
  const [key, setKey] = useState(readKey)
  const [expired, setExpired] = useState(false)

  const logout = (isExpired: boolean) => {
    writeKey(null)
    queryClient.removeQueries({ queryKey: [...parkingKeys.all, 'operator'] })
    setExpired(isExpired)
    setKey(null)
  }

  if (key === null) {
    return (
      <Login
        expired={expired}
        onLogin={(k) => {
          writeKey(k)
          setExpired(false)
          setKey(k)
        }}
      />
    )
  }
  return <Dashboard base={client} operatorKey={key} onLogout={logout} />
}

function Dashboard({ base, operatorKey, onLogout }: { base: ParkingClient; operatorKey: string; onLogout: (expired: boolean) => void }) {
  const op = useMemo(() => createParkingClient({ baseUrl: base.baseUrl, fetch: base.fetch, operatorKey }), [base, operatorKey])
  const health = useQuery({ ...parkingQueries.ingestionHealth(op), refetchInterval: 15_000 })
  const runs = useQuery({
    ...parkingQueries.runs(op, { limit: PAGE_SIZE }),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'queued' || r.status === 'running') ? RUNS_REFRESH_MS : false),
  })
  const [reason, setReason] = useState<Reason | ''>('')
  const [cursors, setCursors] = useState<string[]>([])
  const cursor = cursors.at(-1)
  const rejects = useQuery(
    parkingQueries.rejects(op, { limit: PAGE_SIZE, ...(reason ? { reason } : {}), ...(cursor ? { cursor } : {}) }),
  )

  const unauthorized = [health, runs, rejects].some((q) => isProblem(q.error, 401))
  useEffect(() => {
    if (unauthorized) onLogout(true)
  }, [unauthorized, onLogout])

  return (
    <section>
      <div className="panel-head">
        <h1>Operador</h1>
        <button type="button" className="btn btn-ghost" onClick={() => onLogout(false)}>
          Sair
        </button>
      </div>

      <h2>Saúde da ingestão</h2>
      {queryState(health, false, '') ?? (health.data && <Health data={health.data} />)}

      <h2>Rejeitados</h2>
      <label>
        Motivo{' '}
        <select
          value={reason}
          onChange={(e) => {
            setReason(e.target.value as Reason | '')
            setCursors([])
          }}
        >
          <option value="">Todos</option>
          {REASONS.map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {queryState(rejects, (rejects.data?.items.length ?? 0) === 0, 'Nenhum evento rejeitado.') ?? (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Eventos rejeitados, mais recentes primeiro</caption>
            <thead>
              <tr>
                <th scope="col">Recebido em</th>
                <th scope="col">Motivo</th>
                <th scope="col">Catraca</th>
                <th scope="col">Estacionamento</th>
                <th scope="col">Detalhe</th>
              </tr>
            </thead>
            <tbody>
              {rejects.data?.items.map((r) => (
                <tr key={r.reject_id}>
                  <td>{fmtDateTime(r.received_at)}</td>
                  <td>{reasonLabel(r.reason)}</td>
                  <td>{r.gate_id ?? '-'}</td>
                  <td>{r.lot_id ?? '-'}</td>
                  <td>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pager">
        <button type="button" className="btn btn-ghost" disabled={cursors.length === 0} onClick={() => setCursors((c) => c.slice(0, -1))}>
          Página anterior
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={!rejects.data?.next_cursor}
          onClick={() => {
            const next = rejects.data?.next_cursor
            if (next) setCursors((c) => [...c, next])
          }}
        >
          Próxima página
        </button>
      </div>

      <h2>Execuções</h2>
      <EnqueueForm op={op} onUnauthorized={() => onLogout(true)} />
      {queryState(runs, (runs.data?.length ?? 0) === 0, 'Nenhuma execução registrada.') ?? <Runs runs={runs.data ?? []} />}
    </section>
  )
}

function Health({ data }: { data: Schemas['IngestionHealth'] }) {
  const rows = data.per_minute
  const reasons = Object.entries(data.rejects_by_reason)
  return (
    <>
      <p>
        Atraso de chegada: média {fmtNum(data.lag.avg_s)} s, máximo {fmtNum(data.lag.max_s)} s. Último lote: {fmtDateTime(data.last_batch_at)}.
      </p>
      {rows.length === 0 ? (
        <p className="state">Nenhum lote recebido na janela.</p>
      ) : (
        <ChartPanel
          title="Eventos por minuto"
          summary={`${fmtNum(rows.reduce((n, r) => n + r.accepted, 0))} aceitos, ${fmtNum(rows.reduce((n, r) => n + r.duplicates, 0))} duplicados e ${fmtNum(rows.reduce((n, r) => n + r.rejected, 0))} rejeitados em ${fmtNum(rows.length)} minutos.`}
          option={chartOption(rows.map((r) => fmtTime(r.minute)), [
            { name: 'Aceitos', type: 'bar', stack: 'events', data: rows.map((r) => r.accepted) },
            { name: 'Duplicados', type: 'bar', stack: 'events', data: rows.map((r) => r.duplicates) },
            { name: 'Rejeitados', type: 'bar', stack: 'events', data: rows.map((r) => r.rejected) },
          ])}
          columns={[
            { label: 'Minuto', value: (r) => fmtDateTime(r.minute) },
            { label: 'Aceitos', value: (r) => r.accepted },
            { label: 'Duplicados', value: (r) => r.duplicates },
            { label: 'Rejeitados', value: (r) => r.rejected },
          ]}
          rows={rows}
          filename="ingestao-por-minuto.csv"
        />
      )}
      <h3>Rejeitados por motivo</h3>
      {reasons.length === 0 ? (
        <p className="state">Nenhum rejeitado na janela.</p>
      ) : (
        <ul>
          {reasons.map(([r, n]) => (
            <li key={r}>
              {reasonLabel(r)}: {fmtNum(n)}
            </li>
          ))}
        </ul>
      )}
      <h3>Catracas</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Catraca</th>
              <th scope="col">Estacionamento</th>
              <th scope="col">Último evento</th>
              <th scope="col">Último recebimento</th>
            </tr>
          </thead>
          <tbody>
            {data.gates.map((g) => (
              <tr key={g.gate_id}>
                <td>{g.gate_id}</td>
                <td>{g.lot_id}</td>
                <td>{fmtDateTime(g.last_event_at)}</td>
                <td>{fmtDateTime(g.last_received_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

const RUN_STATUS: Record<Schemas['PipelineRunOut']['status'], string> = {
  queued: 'Na fila',
  running: 'Em execução',
  succeeded: 'Concluída',
  failed: 'Falhou',
}

function Runs({ runs }: { runs: Schemas['PipelineRunOut'][] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Execuções mais recentes</caption>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Job</th>
            <th scope="col">Modo</th>
            <th scope="col">Data</th>
            <th scope="col">Solicitada</th>
            <th scope="col">Início</th>
            <th scope="col">Fim</th>
            <th scope="col">Métricas</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td>
                <span className={`badge run-${r.status}`}>{RUN_STATUS[r.status]}</span>
              </td>
              <td>{r.job}</td>
              <td>{r.mode ?? '-'}</td>
              <td>{fmtDate(r.service_date)}</td>
              <td>{fmtDateTime(r.requested_at)}</td>
              <td>{fmtDateTime(r.started_at)}</td>
              <td>{fmtDateTime(r.finished_at)}</td>
              <td>
                {`lidas ${fmtNum(r.rows_read)}, gravadas ${fmtNum(r.rows_written)}, rejeitadas ${fmtNum(r.rows_rejected)}`}
                {r.error && <div className="error-text">{r.error}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export const MODES: Record<RunRequest['job'], { value: string; label: string }[]> = {
  daily: [],
  close_day: [
    { value: '', label: 'Padrão (fechar o dia)' },
    { value: 'export', label: 'export (só reexportar)' },
  ],
  warehouse: [
    { value: 'incremental', label: 'incremental' },
    { value: 'full', label: 'full' },
  ],
}

function EnqueueForm({ op, onUnauthorized }: { op: ParkingClient; onUnauthorized: () => void }) {
  const queryClient = useQueryClient()
  const [job, setJob] = useState<RunRequest['job']>('daily')
  const [mode, setMode] = useState('')
  const [date, setDate] = useState('')
  const enqueue = useMutation({
    mutationFn: (body: RunRequest) => op.operator.enqueueRun(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...parkingKeys.all, 'operator', 'runs'] }),
    onError: (e) => {
      if (isProblem(e, 401)) onUnauthorized()
    },
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const body: RunRequest = { job }
    if (mode) body.mode = mode as NonNullable<RunRequest['mode']>
    if (date) body.service_date = date
    enqueue.mutate(body)
  }

  const err = enqueue.error
  return (
    <form className="filters" onSubmit={submit} aria-label="Enfileirar execução">
      <label>
        Job{' '}
        <select
          value={job}
          onChange={(e) => {
            const next = e.target.value as RunRequest['job']
            setJob(next)
            setMode(MODES[next][0]?.value ?? '')
          }}
        >
          <option value="daily">daily</option>
          <option value="close_day">close_day</option>
          <option value="warehouse">warehouse</option>
        </select>
      </label>
      {MODES[job].length > 0 && (
        <label>
          Modo{' '}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODES[job].map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Data de serviço <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <button type="submit" className="btn" disabled={enqueue.isPending}>
        Enfileirar
      </button>
      {enqueue.isSuccess && (
        <p role="status">
          Execução enfileirada: {enqueue.data.job} de {fmtDate(enqueue.data.service_date)}.
        </p>
      )}
      {err && isProblem(err, 409) && err.code === 'run-already-active' && (
        <p className="state state-error" role="alert">
          Já existe uma execução ativa deste job para essa data.
        </p>
      )}
      {err && !isProblem(err, 401) && <ErrorBox error={err} />}
    </form>
  )
}
