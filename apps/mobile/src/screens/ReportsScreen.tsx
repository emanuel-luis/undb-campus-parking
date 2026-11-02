import { parkingQueries } from '@undb-campus-parking/api-client/react'
import type { ReportRange } from '@undb-campus-parking/api-client'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { View } from 'react-native'
import { DailyChart, DwellChart, FreeCurve } from '../charts'
import { formatDateTime, formatDec, formatInt, formatMinutes, localDate } from '../format'
import { useClient } from '../parking'
import { Button, Empty, ErrorState, Kpi, Loading, Screen, T } from '../ui'

const PRESETS = [7, 30, 90] as const
const row = { flexDirection: 'row', flexWrap: 'wrap', gap: 8 } as const

export function ReportsScreen() {
  const client = useClient()
  const [lotId, setLotId] = useState<string | null>(null)
  const [days, setDays] = useState<(typeof PRESETS)[number]>(30)
  const range: ReportRange = { from: localDate(days - 1), to: localDate(0), ...(lotId ? { lot_id: lotId } : {}) }
  const yesterday = localDate(1)

  const lots = useQuery(parkingQueries.lots(client))
  const freshness = useQuery(parkingQueries.freshness(client))
  const summary = useQuery(parkingQueries.summary(client, range))
  const daily = useQuery(parkingQueries.daily(client, range))
  const dwell = useQuery(parkingQueries.dwell(client, range))
  const curve = useQuery({ ...parkingQueries.occupancy(client, { date: yesterday, lot_id: lotId ?? '' }), enabled: lotId !== null })
  const names = new Map((lots.data ?? []).map((l) => [l.lot_id, l.name]))
  const refetchAll = () => void Promise.all([freshness.refetch(), summary.refetch(), daily.refetch(), dwell.refetch()])

  return (
    <Screen onRefresh={refetchAll} refreshing={summary.isRefetching}>
      <T variant="muted">
        {freshness.data?.finished_at
          ? `Dados atualizados em ${formatDateTime(freshness.data.finished_at)}`
          : freshness.isPending
            ? 'Verificando atualização dos dados…'
            : 'Dados ainda sem atualização do armazém.'}
      </T>
      <View style={row} accessibilityLabel="Estacionamento">
        <Button label="Todos" selected={lotId === null} onPress={() => setLotId(null)} />
        {(lots.data ?? []).map((l) => (
          <Button key={l.lot_id} label={l.name} selected={lotId === l.lot_id} onPress={() => setLotId(l.lot_id)} />
        ))}
      </View>
      <View style={row} accessibilityLabel="Período">
        {PRESETS.map((d) => (
          <Button key={d} label={`${d} dias`} selected={days === d} onPress={() => setDays(d)} />
        ))}
      </View>
      {summary.isPending ? (
        <Loading />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={refetchAll} />
      ) : summary.data.length === 0 ? (
        <Empty message="Sem dados no período." />
      ) : (
        <>
          {summary.data.map((s) => (
            <View key={s.lot_id} style={{ gap: 8 }}>
              <T variant="subtitle">{names.get(s.lot_id) ?? s.lot_id}</T>
              <View style={row}>
                <Kpi label="Entradas" value={formatInt(s.entries)} />
                <Kpi label="Saídas" value={formatInt(s.exits)} />
                <Kpi label="Permanência média" value={formatMinutes(s.avg_stay_min)} />
                <Kpi label="P90" value={formatMinutes(s.p90_stay_min)} />
                <Kpi label="Rotatividade" value={formatDec(s.avg_turnover)} />
                <Kpi label="Vagas livres (mínimo)" value={formatInt(s.min_free)} />
                <Kpi label="Minutos lotado" value={formatInt(s.minutes_full)} />
              </View>
            </View>
          ))}
          <T variant="subtitle">Entradas por dia</T>
          {daily.data ? <DailyChart rows={daily.data} /> : daily.isError ? <ErrorState error={daily.error} /> : <Loading />}
          <T variant="subtitle">Permanência</T>
          {dwell.data ? <DwellChart rows={dwell.data} /> : dwell.isError ? <ErrorState error={dwell.error} /> : <Loading />}
          <T variant="subtitle">Vagas livres ontem</T>
          {lotId === null ? (
            <T variant="muted">Escolha um estacionamento para ver a curva de vagas livres.</T>
          ) : curve.data ? (
            <FreeCurve slots={curve.data} name={names.get(lotId) ?? lotId} />
          ) : curve.isError ? (
            <ErrorState error={curve.error} />
          ) : (
            <Loading />
          )}
        </>
      )}
    </Screen>
  )
}
