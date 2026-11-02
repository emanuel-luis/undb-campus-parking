/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin; empty means the page's own origin. */
  readonly VITE_API_BASE_URL?: string
}
