// Per-weight entry points, so the bundle carries only the four fonts in use.
import { Montserrat_600SemiBold } from '@expo-google-fonts/montserrat/600SemiBold'
import { Montserrat_700Bold } from '@expo-google-fonts/montserrat/700Bold'
import { Roboto_400Regular } from '@expo-google-fonts/roboto/400Regular'
import { Roboto_500Medium } from '@expo-google-fonts/roboto/500Medium'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createParkingClient } from '@undb-campus-parking/api-client'
import { useFonts } from 'expo-font'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'
import { ParkingProvider } from '../src/parking'
import { MissingConfigScreen } from '../src/screens/MissingConfigScreen'
import { headerOptions } from '../src/theme'

// Created once at module scope: the live hook reconnects whenever the client changes.
const baseUrl = process.env.EXPO_PUBLIC_API_BASE_URL?.trim() ?? ''
const client = baseUrl ? createParkingClient({ baseUrl }) : null
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 30_000 } } })

// Keep the splash up until the fonts are ready, so the first frame already uses them.
void SplashScreen.preventAutoHideAsync()

export default function RootLayout() {
  const [loaded, fontError] = useFonts({ Montserrat_600SemiBold, Montserrat_700Bold, Roboto_400Regular, Roboto_500Medium })
  // On a font error the app still starts, with system fonts.
  const ready = loaded || fontError !== null
  useEffect(() => {
    if (ready) void SplashScreen.hideAsync()
  }, [ready])
  if (!ready) return null
  if (client === null) return <MissingConfigScreen />
  return (
    <QueryClientProvider client={queryClient}>
      <ParkingProvider client={client}>
        <StatusBar style="light" />
        <Stack screenOptions={headerOptions}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="estacionamentos/[lotId]" options={{ title: 'Estacionamento' }} />
        </Stack>
      </ParkingProvider>
    </QueryClientProvider>
  )
}
