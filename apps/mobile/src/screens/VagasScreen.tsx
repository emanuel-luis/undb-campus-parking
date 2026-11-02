import { parkingQueries } from '@undb-campus-parking/api-client/react'
import { useQuery } from '@tanstack/react-query'
import { router } from 'expo-router'
import { View } from 'react-native'
import { updatedAgo } from '../format'
import { useClient, useLive, useNow } from '../parking'
import { Empty, ErrorState, Loading, LotCard, Screen, StatusChip, T } from '../ui'

export function VagasScreen() {
  const client = useClient()
  const live = useLive()
  const now = useNow()
  // REST snapshot for the first paint and pull to refresh; the live one wins once it is newer.
  const rest = useQuery(parkingQueries.availability(client))
  const restAt = rest.dataUpdatedAt > 0 ? new Date(rest.dataUpdatedAt) : null
  const useRest = rest.data !== undefined && (live.lastUpdate === null || (restAt !== null && restAt > live.lastUpdate))
  const snapshot = useRest ? rest.data : (live.snapshot ?? undefined)
  const lastUpdate = useRest ? restAt : live.lastUpdate

  return (
    <Screen refreshing={rest.isRefetching} onRefresh={() => void rest.refetch()}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <StatusChip status={live.status} />
        <T variant="muted" accessibilityLiveRegion="polite">
          {updatedAgo(lastUpdate, now)}
        </T>
      </View>
      {snapshot === undefined ? (
        rest.isError ? (
          <ErrorState error={rest.error} onRetry={() => void rest.refetch()} />
        ) : (
          <Loading />
        )
      ) : snapshot.lots.length === 0 ? (
        <Empty message="Nenhum estacionamento cadastrado." />
      ) : (
        snapshot.lots.map((lot) => (
          <LotCard key={lot.lot_id} lot={lot} onPress={() => router.push(`/estacionamentos/${encodeURIComponent(lot.lot_id)}`)} />
        ))
      )}
    </Screen>
  )
}
