import { useLiveAvailability } from '@undb-campus-parking/api-client/react'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'
import { useClient } from '../api'
import { LiveChip, LotCard } from '../components/live'
import { Empty, Loading } from '../components/ui'
import { announcements } from '../lib/status'

export function LotsPage() {
  const client = useClient()
  const { snapshot, status, lastUpdate } = useLiveAvailability(client)
  const region = useRef<HTMLDivElement>(null)
  const memory = useRef(new Map<string, { free: number | null; at: number }>())

  // Written straight into the live region: one polite message per lot per minute at most.
  useEffect(() => {
    if (!snapshot || !region.current) return
    const said = announcements(memory.current, snapshot.lots, Date.now())
    if (said.length > 0) region.current.textContent = said.join('. ')
  }, [snapshot])

  let body
  if (snapshot === null) {
    body =
      status === 'polling' ? (
        <div className="state state-error" role="alert">
          <p>Não foi possível carregar as vagas. Tentando de novo a cada 10 s.</p>
        </div>
      ) : (
        <Loading>Carregando vagas…</Loading>
      )
  } else if (snapshot.lots.length === 0) {
    body = <Empty>Nenhum estacionamento cadastrado.</Empty>
  } else {
    body = (
      <ul className="grid">
        {snapshot.lots.map((lot) => (
          <li key={lot.lot_id}>
            <LotCard lot={lot} title={<Link to={`/estacionamentos/${encodeURIComponent(lot.lot_id)}`}>{lot.name}</Link>} />
          </li>
        ))}
      </ul>
    )
  }

  return (
    <section>
      <h1>Vagas agora</h1>
      <LiveChip status={status} lastUpdate={lastUpdate} hasData={snapshot !== null} />
      {body}
      <div ref={region} className="sr-only" aria-live="polite" />
    </section>
  )
}
