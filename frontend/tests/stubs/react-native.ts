// Minimal stand-in for `react-native` under Vitest.
//
// React Native's own index.js is written in Flow (`import typeof * as ...`), which no plain
// ESM parser accepts. The modules under test here are pure logic; the only thing any of them
// takes from react-native is `Platform`, used by tokenStore to choose between SecureStore
// (native) and localStorage (web). Stubbing it is far less machinery than adding a Flow
// transform to test code that never renders anything.
//
// `web`, because that is the platform whose storage path these tests exercise.
export const Platform = {
  OS: 'web' as const,
  select: <T,>(spec: { web?: T; default?: T; native?: T; ios?: T; android?: T }): T | undefined =>
    spec.web ?? spec.default,
};
