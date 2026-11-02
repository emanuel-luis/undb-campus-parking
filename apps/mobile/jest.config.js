module.exports = {
  preset: 'jest-expo',
  // The api-client ships TypeScript source, so it goes through babel like the app.
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|expo-router|react-native-svg|@undb-campus-parking/.*))',
  ],
  // A cold run (always the case in CI) transforms React Native and Expo modules lazily during
  // the first render of a file, which alone can take over the default 5 s on a busy runner.
  testTimeout: 30_000,
  collectCoverageFrom: ['src/**/*.{ts,tsx}'],
  coverageThreshold: { global: { lines: 80, statements: 80 } },
}
