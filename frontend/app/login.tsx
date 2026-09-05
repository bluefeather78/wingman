import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthContext';
import { beginGoogleSignIn } from '@/auth/googleSignIn';
import { Field, Logo, PopButton, Txt } from '@/ui/components';
import { colors, fonts, radius, softShadow, space } from '@/ui/theme';

// The shortest password a new account may have. S1-11: the server only ever sees
// sha256(password), so it cannot tell a passphrase from a single letter — the length rule
// has to live here, and the field's placeholder was already promising it.
const MIN_PASSWORD_LENGTH = 8;

// Login / Register — ported from the live app's #page-login: centered card-soft (max-w-sm),
// favicon + Wingman + BETA, tagline, beta notice, back-to-home, Google row (with the
// live app's COMING SOON treatment) above the form, then the form and the register link.
export default function Login() {
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [userid, setUserid] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  const isRegister = mode === 'register';

  async function submit() {
    setError(null);
    // S1-11: there was no minimum anywhere — not here, not on the server, which only ever
    // sees sha256(password) and so cannot tell a 20-character passphrase from one letter.
    // The placeholder said "At least 8 characters" and nothing enforced it.
    if (isRegister && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (isRegister && password !== passwordConfirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      if (isRegister) {
        await register({ firstName: firstName.trim(), lastName: lastName.trim(), email: email.trim(), userid: userid.trim().toLowerCase(), password, isAdult, parentalConsent, acceptedTerms });
      } else {
        await login(userid.trim().toLowerCase(), password);
      }
      router.replace('/(app)');
    } catch (e) {
      setError((e as Error).message || 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setError(null);
    try {
      const handoff = await beginGoogleSignIn();
      if (Platform.OS !== 'web' && handoff) router.replace({ pathname: '/google-auth', params: { google_token: handoff } });
    } catch (e) {
      setError((e as Error).message || 'Google sign-in failed.');
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

          {/* Google sign-in row — visually matches the live app's coming-soon button. */}
          <View style={{ gap: 12 }}>
            <Pressable onPress={google} style={styles.googleBtn} disabled={busy}>
              <GoogleG />
              <Text style={styles.googleText}>Continue with Google</Text>
              <View style={styles.comingSoon}>
                <Text style={styles.comingSoonText}>COMING SOON</Text>
              </View>
            </Pressable>
            <View style={styles.orRow}>
              <View style={styles.rule} />
              <Text style={styles.orText}>OR</Text>
              <View style={styles.rule} />
            </View>
          </View>

          <View style={{ gap: 16 }}>
            {isRegister && (
              <>
                <View style={styles.twoCol}>
                  <View style={styles.col}>
                    <Field label="First name" value={firstName} onChangeText={setFirstName} />
                  </View>
                  <View style={styles.col}>
                    <Field label="Last name" value={lastName} onChangeText={setLastName} />
                  </View>
                </View>
                <Field label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="you@example.com" />
                <Field label="User ID" value={userid} onChangeText={setUserid} autoCapitalize="none" placeholder="Pick a user ID" />
                <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry placeholder="At least 8 characters" />
                <Field label="Confirm password" value={passwordConfirm} onChangeText={setPasswordConfirm} secureTextEntry placeholder="••••••••" />
                <View style={styles.consentBox}>
                  <ConsentRow label="I am 18 years of age or older." value={isAdult} onValueChange={setIsAdult} />
                  {!isAdult && (
                    <ConsentRow
                      label="I am at least 13, and my parent or legal guardian has given me permission to use Wingman and agrees to the Terms of Use on my behalf."
                      value={parentalConsent}
                      onValueChange={setParentalConsent}
                    />
                  )}
                  <ConsentRow label="I have read and agree to the Terms of Use and the Privacy Policy." value={acceptedTerms} onValueChange={setAcceptedTerms} />
                </View>
                <Text style={styles.trialNote}>
                  Every new account starts with a <Text style={styles.bold}>7-day free trial</Text>. No card required to start.
                </Text>
              </>
            )}

            {!isRegister && (
              <>
                <Field label="User ID" value={userid} onChangeText={setUserid} autoCapitalize="none" placeholder="e.g. sid2028" />
                <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" />
              </>
            )}

            {!!error && <Text style={styles.error}>{error}</Text>}

            <PopButton label={isRegister ? 'Create Account' : 'Sign In'} onPress={submit} loading={busy} full textStyle={styles.submitText} />

            <Text style={styles.switchText}>
              {isRegister ? 'Already have an account? ' : "Don't have an account? "}
              <Text style={styles.switchLink} onPress={() => { setError(null); setMode(isRegister ? 'login' : 'register'); }}>
                {isRegister ? 'Sign In' : 'Register'}
              </Text>
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

function ConsentRow({ label, value, onValueChange }: { label: string; value: boolean; onValueChange: (v: boolean) => void }) {
  return (
    <Pressable style={styles.consentRow} onPress={() => onValueChange(!value)}>
      <Switch value={value} onValueChange={onValueChange} trackColor={{ true: colors.indigo600, false: colors.slate200 }} thumbColor={colors.white} style={styles.switch} />
      <Text style={styles.consentText}>{label}</Text>
    </Pressable>
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
    borderColor: '#CBD5E1',
    borderRadius: radius.md,
    padding: 12,
    backgroundColor: colors.slate100,
  },
  googleText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate400 },
  gWrap: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.white, alignItems: 'center', justifyContent: 'center', opacity: 0.5 },
  gText: { fontFamily: fonts.bodyXBold, fontSize: 13, color: '#4285F4' },
  comingSoon: { position: 'absolute', top: -8, right: -8, backgroundColor: colors.orange, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  comingSoonText: { fontFamily: fonts.bodyXBold, fontSize: 9, color: colors.white, letterSpacing: 0.5 },
  orRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  rule: { flex: 1, height: 1, backgroundColor: colors.slate200 },
  orText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate400 },

  // Each column must flex — a bare Field keeps its intrinsic width and overflows the card.
  twoCol: { flexDirection: 'row', gap: 12 },
  col: { flex: 1, minWidth: 0 },
  consentBox: { borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, padding: 12, gap: 10, backgroundColor: colors.slate50 },
  consentRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  switch: Platform.OS === 'web' ? ({ transform: [{ scale: 0.8 }] } as object) : {},
  consentText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.slate900, flex: 1 },
  trialNote: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, textAlign: 'center' },
  bold: { fontFamily: fonts.bodyBold },

  submitText: { fontFamily: fonts.bodyXBold },
  error: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.rose600 },
  switchText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500, textAlign: 'center' },
  switchLink: { fontFamily: fonts.bodyBold, color: colors.indigo600 },
});
