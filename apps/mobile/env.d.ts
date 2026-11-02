// Expo inlines EXPO_PUBLIC_* variables at build time; this is the only one the app reads.
declare const process: { env: { EXPO_PUBLIC_API_BASE_URL?: string } }

declare module '*.png' {
  const source: number
  export default source
}
