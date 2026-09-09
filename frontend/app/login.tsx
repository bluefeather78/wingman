import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as AppleAuthentication from 'expo-apple-authentication';
import { beginGoogleSignIn } from '@/auth/googleSignIn';
import { beginAppleSignIn, stashApplePending } from '@/auth/appleSignIn';
import { useAuth } from '@/auth/AuthContext';
import { Logo } from '@/ui/components';
import { colors, fonts, radius, softShadow, space } from '@/ui/theme';

// Sign in — Google is the ONLY path. The userid/password form (login + register + the signup
// consent checkboxes) was removed by operator directive 2026-09-07; consent is now collected
// exclusively in the Google flow (google-auth.tsx), whose wording mirrors what the form used
// to ask. The backend /api/register and /api/login endpoints and the AuthContext
// login/register methods are deliberately left in place (dormant) so existing password
// accounts and dev tooling still work — only the UI entry point is gone.
export default function Login() {
  const router = useRouter();
  const { appleSignIn } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function google() {
    setError(null);
    setBusy(true);
    try {
      const handoff = await beginGoogleSignIn();
      if (Platform.OS !== 'web' && handoff) router.replace({ pathname: '/google-auth', params: { google_token: handoff } });
    } catch (e) {
      setError((e as Error).message || 'Google sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  // Sign in with Apple — native iOS only (App Store 4.8). The token is resolved right here: an
  // existing/linked account enters the app immediately, and only a new account is handed to the
  // /apple-auth consent screen (carrying the SAME credential, which appleFinish re-sends).
  async function apple() {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      const cred = await beginAppleSignIn();
      if (!cred) return; // cancelled, or no identity token
      const result = await appleSignIn(cred);
      if (result.status === 'session') {
        router.replace('/(app)');
      } else {
        stashApplePending({
          credential: cred,
          firstName: result.firstName ?? cred.firstName,
          lastName: result.lastName ?? cred.lastName,
          email: result.email,
        });
        router.replace('/apple-auth');
      }
    } catch (e) {
      setError((e as Error).message || 'Apple sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <View style={[styles.card, softShadow()]}>
          <View style={styles.brand}>
            <View style={styles.logoBox}>
              <Logo size={56} />
            </View>
            <View style={styles.brandRow}>
              <Text style={styles.title}>Wingman</Text>
              <View style={styles.beta}>
                <Text style={styles.betaText}>BETA</Text>
              </View>
            </View>
            <Text style={styles.tagline}>Find opportunities. Never miss a deadline.</Text>
            <Text style={styles.sub}>Discover research, programs, internships, and more matched to what you love.</Text>
            <View style={styles.notice}>
              <Text style={styles.noticeText}>
                🚧 This app is in beta - features are actively evolving and results may occasionally be incomplete or inaccurate.
              </Text>
            </View>
            <Pressable onPress={() => router.push('/landing')}>
              <Text style={styles.backLink}>← Back to home</Text>
            </Pressable>
          </View>

          {/* Google sign-in, plus Sign in with Apple on iOS (required by App Store 4.8 for a
              social-login-only app). Apple's official button is used for review compliance and
              is iOS-only — web and Android see just the Google button. */}
          <View style={{ gap: 12 }}>
            {Platform.OS === 'ios' && (
              <AppleAuthentication.AppleAuthenticationButton
                buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
                buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK}
                cornerRadius={radius.md}
                style={styles.appleBtn}
                onPress={apple}
              />
            )}
            <Pressable onPress={google} style={styles.googleBtn} disabled={busy}>
              <GoogleG />
              <Text style={styles.googleText}>Continue with Google</Text>
            </Pressable>

            {!!error && <Text style={styles.error}>{error}</Text>}

            <Text style={styles.trialNote}>
              Every account is <Text style={styles.bold}>free to start</Text>, with a daily allowance of AI actions. No card required.
            </Text>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

// The four-color Google "G", drawn with simple views (good enough at 20px).
function GoogleG() {
  return (
    <View style={styles.gWrap}>
      <Text style={styles.gText}>G</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  scroll: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: space.lg },
  card: { backgroundColor: colors.white, borderRadius: radius.xl, padding: 40, gap: 24, width: '100%', maxWidth: 384, alignSelf: 'center' },
  brand: { alignItems: 'center' },
  logoBox: { width: 64, height: 64, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginBottom: 8 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  title: { fontFamily: fonts.display, fontSize: 24, color: colors.slate900 },
  beta: { backgroundColor: colors.amber300, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  betaText: { fontFamily: fonts.bodyXBold, fontSize: 10, color: colors.slate900, letterSpacing: 0.5 },
  tagline: { fontFamily: fonts.display, fontSize: 18, lineHeight: 24, color: colors.slate900, textAlign: 'center', marginTop: 12 },
  sub: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 20, color: colors.slate500, textAlign: 'center', marginTop: 4 },
  notice: { backgroundColor: colors.amber50, borderWidth: 1, borderColor: colors.amber200, borderRadius: radius.sm, paddingHorizontal: 12, paddingVertical: 8, marginTop: 12 },
  noticeText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.amber700, textAlign: 'center' },
  backLink: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, marginTop: 8 },

  googleBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderWidth: 2,
    borderColor: colors.slate900,
    borderRadius: radius.md,
    padding: 12,
    backgroundColor: colors.white,
  },
  appleBtn: { width: '100%', height: 48 },
  googleText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate900 },
  gWrap: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.white, alignItems: 'center', justifyContent: 'center' },
  gText: { fontFamily: fonts.bodyXBold, fontSize: 13, color: '#4285F4' },

  trialNote: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, textAlign: 'center' },
  bold: { fontFamily: fonts.bodyBold },
  error: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.rose600 },
});
