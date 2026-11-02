import { parkingQueries } from '@undb-campus-parking/api-client/react'
import { useQuery } from '@tanstack/react-query'
import { View } from 'react-native'
import { curveStats, FreeCurve } from '../charts'
import { formatInt, updatedAgo } from '../format'
import { useClient, useLive, useNow } from '../parking'
import { ErrorState, Kpi, Loading, LotCard, Screen, StatusChip, T } from '../ui'

export function LotDetailScreen({ lotId }: { lotId: string }) {
  const client = useClient()
  const live = useLive()
  const now = useNow()
  const rest = useQuery({ ...parkingQueries.lotAvailability(client, lotId), enabled: live.snapshot === null })
  // Today is the server's default date (America/Fortaleza).
  const curve = useQuery(parkingQueries.occupancy(client, { lot_id: lotId }))
  const lot = live.snapshot?.lots.find((l) => l.lot_id === lotId) ?? rest.data
  const slots = curve.data ?? []
  const { min, minutesFull } = curveStats(slots)

  return (
    <Screen onRefresh={() => void curve.refetch()} refreshing={curve.isRefetching}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <StatusChip status={live.status} />
        <T variant="muted">{updatedAgo(live.lastUpdate, now)}</T>
      </View>
      {lot ? <LotCard lot={lot} /> : rest.isError ? <ErrorState error={rest.error} /> : <Loading />}
      <T variant="subtitle">Vagas livres hoje</T>
      {curve.isPending ? (
        <Loading />
      ) : curve.isError ? (
        <ErrorState error={curve.error} onRetry={() => void curve.refetch()} />
      ) : (
        <>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            <Kpi label="Mínimo de vagas livres" value={formatInt(min)} />
            <Kpi label="Minutos lotado" value={formatInt(minutesFull)} />
          </View>
          <FreeCurve slots={slots} name={lot?.name ?? lotId} />
        </>
      )}
    </Screen>
  )
}
