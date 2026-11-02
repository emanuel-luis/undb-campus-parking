import '@fontsource/montserrat/600.css'
import '@fontsource/montserrat/700.css'
import '@fontsource/roboto/400.css'
import '@fontsource/roboto/500.css'
import './theme.css'
import { QueryClientProvider } from '@tanstack/react-query'
import { createParkingClient } from '@undb-campus-parking/api-client'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { ApiContext, apiBaseUrl, createQueryClient } from './api'
import { AppRoutes } from './App'

// Created once: useLiveAvailability reconnects whenever the client object changes.
const client = createParkingClient({ baseUrl: apiBaseUrl(import.meta.env.VITE_API_BASE_URL, window.location.origin) })
const queryClient = createQueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </QueryClientProvider>
    </ApiContext.Provider>
  </StrictMode>,
)
