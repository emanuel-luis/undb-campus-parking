module.exports = {
  preset: 'jest-expo',
  // The api-client ships TypeScript source, so it goes through babel like the app.
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|expo-router|react-native-svg|@undb-campus-parking/.*))',
  ],
  collectCoverageFrom: ['src/**/*.{ts,tsx}'],
  coverageThreshold: { global: { lines: 80, statements: 80 } },
}
