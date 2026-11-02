import { useLocalSearchParams } from 'expo-router'
import { LotDetailScreen } from '../../src/screens/LotDetailScreen'

export default function LotDetailRoute() {
  const { lotId } = useLocalSearchParams<{ lotId: string }>()
  return <LotDetailScreen lotId={lotId} />
}
