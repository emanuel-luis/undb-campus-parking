import { cleanup, configure } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, beforeEach, vi } from 'vitest'
import { FakeSocket } from './helpers'

// jsdom has no canvas: pages render a stand-in chart; test/EChart.test.tsx covers the real wrapper.
vi.mock('../src/components/EChart', () => ({
  EChart: ({ label }: { label: string }) => createElement('div', { role: 'img', 'aria-label': label }),
}))

configure({ asyncUtilTimeout: 3000 })

function setVisibility(state: 'hidden' | 'visible'): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket)
  setVisibility('visible')
})

afterEach(() => {
  cleanup()
  sessionStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
