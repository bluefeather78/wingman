import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

// PRODUCTION_READINESS_PLAN.md Phase 5. The frontend had ZERO tests; `scripts/verify.ts` is a
// live-backend smoke script that makes paid calls against a hard-coded account, and is
// excluded from tsc.
//
// SCOPE IS DELIBERATE: this covers `src/lib` and `src/api` — the pure logic ported out of the
// retired SPA — and nothing that renders. Those modules are where every Phase 5 bug lives
// (grade parsing, date validation, tracker merges, retry counting), they are the same code the
// golden-matching harness imports, and they need no DOM. Adding a React renderer would pull in
// react-native-web, jsdom and a transform pipeline to test screens whose bugs are all in the
// logic these files hold.
//
// `environment: node` for the same reason. Nothing under test touches `document`; the two
// modules that touch `localStorage` (tokenStore, trackerStore) reach it through a platform
// shim the tests inject.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    // Each test file gets its own module registry, which matters here: several of the modules
    // under test are MODULE SINGLETONS (lastChecked, the httpClient token state, the finder's
    // session cache). Sharing them across files would make one test's leftover state another
    // test's starting point — which is finding 18 itself, and not something to reproduce in
    // the harness that is meant to catch it.
    isolate: true,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // React Native's index.js is Flow (`import typeof * as ...`), which no plain ESM parser
      // accepts, and expo-secure-store / expo-crypto are native modules. Nothing under test
      // renders — `src/api/tokenStore.ts` is the only file that touches any of them, and only
      // for Platform.OS. Stubbing the three is far less machinery than adding a Flow transform
      // to test pure logic. See tests/stubs/ for what each one provides and why.
      'react-native': fileURLToPath(new URL('./tests/stubs/react-native.ts', import.meta.url)),
      'expo-secure-store': fileURLToPath(new URL('./tests/stubs/expo-secure-store.ts', import.meta.url)),
      'expo-crypto': fileURLToPath(new URL('./tests/stubs/expo-crypto.ts', import.meta.url)),
    },
  },
});
