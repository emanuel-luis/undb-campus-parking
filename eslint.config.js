import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import tseslint from 'typescript-eslint'

// Shared by every workspace: each one runs `eslint --max-warnings 0 .` and finds this file upward.
export default tseslint.config(
  { ignores: ['**/dist/**', '**/coverage/**', '**/node_modules/**', 'packages/api-client/src/schema.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat['recommended-latest'],
  { languageOptions: { globals: globals.browser } },
  // Node APIs only in tests and tool configs, so src/ stays portable to browsers and React Native.
  { files: ['**/test/**', '**/*.config.{js,ts}'], languageOptions: { globals: globals.node } },
)
