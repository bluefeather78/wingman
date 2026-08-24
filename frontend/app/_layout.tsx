// Import each weight from its subpath (not the package barrel): the barrel statically pulls
// in all 14 weights, and bundling every weight both bloats the build and can fail on a
// single asset. Subpaths bundle only the five faces we actually use.
import { PlusJakartaSans_400Regular } from '@expo-google-fonts/plus-jakarta-sans/400Regular';
import { PlusJakartaSans_500Medium } from '@expo-google-fonts/plus-jakarta-sans/500Medium';
import { PlusJakartaSans_600SemiBold } from '@expo-google-fonts/plus-jakarta-sans/600SemiBold';
import { PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans/700Bold';
import { PlusJakartaSans_800ExtraBold } from '@expo-google-fonts/plus-jakarta-sans/800ExtraBold';
import { SpaceGrotesk_500Medium } from '@expo-google-fonts/space-grotesk/500Medium';
import { SpaceGrotesk_700Bold } from '@expo-google-fonts/space-grotesk/700Bold';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import Head from 'expo-router/head';
import { StatusBar } from 'expo-status-bar';
import { ActivityIndicator, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AuthProvider } from '@/auth/AuthContext';
import { colors } from '@/ui/theme';

// Root navigator. Loads the BENTO & POP typefaces (Space Grotesk display + Plus Jakarta Sans
// body) before rendering so no screen flashes in a fallback font. `index` is the auth gate.
export default function RootLayout() {
  const [fontsLoaded] = useFonts({
    SpaceGrotesk_700Bold,
    SpaceGrotesk_500Medium,
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
    PlusJakartaSans_800ExtraBold,
  });

  if (!fontsLoaded) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.cream, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator color={colors.navy} />
      </View>
    );
  }

  return (
    <AuthProvider>
      <Head>
        {/* The browser tab identity, matching the old app's <title>Wingman</title> + favicon. */}
        <title>Wingman</title>
      </Head>
      <SafeAreaProvider>
        <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.cream } }}>
          <Stack.Screen name="index" />
          <Stack.Screen name="landing" />
          <Stack.Screen name="login" />
          <Stack.Screen name="google-auth" />
          <Stack.Screen name="(app)" />
        </Stack>
        <StatusBar style="dark" />
      </SafeAreaProvider>
    </AuthProvider>
  );
}
