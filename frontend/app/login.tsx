import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Switch, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthContext';
import { beginGoogleSignIn } from '@/auth/googleSignIn';
import { Field, PopButton, Txt } from '@/ui/components';
import { colors, radius, softShadow, space } from '@/ui/theme';

// Login / Register — a centered card matching the live app: orange bar-chart mark, BETA
// badge, beta notice, Google, and the password form. Register adds name/email/location +
// the three consent switches the server re-checks.
export default function Login() {
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [userid, setUserid] = useState('');
  const [password, setPassword] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [location, setLocation] = useState('');
  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  const isRegister = mode === 'register';

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (isRegister) {
        await register({ firstName: firstName.trim(), lastName: lastName.trim(), email: email.trim(), userid: userid.trim().toLowerCase(), location: location.trim(), password, isAdult, parentalConsent, acceptedTerms });
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
            <Ionicons name="stats-chart" size={30} color={colors.orange} />
            <View style={styles.brandRow}>
              <Txt variant="h1" style={{ color: colors.navy }}>Wingman</Txt>
              <View style={styles.beta}><Txt style={styles.betaText}>BETA</Txt></View>
            </View>
            <Txt variant="h3" style={styles.tagline}>Find opportunities. Never miss a deadline.</Txt>
            <Txt variant="small" style={styles.center}>Discover research, programs, internships, and more matched to what you love.</Txt>
          </View>

          <View style={styles.notice}>
            <Txt variant="small" style={styles.noticeText}>
              🚧 This app is in beta — features are actively evolving and results may occasionally be incomplete or inaccurate.
            </Txt>
          </View>

          {isRegister && (
            <>
              <View style={styles.twoCol}>
                <Field label="FIRST NAME" value={firstName} onChangeText={setFirstName} style={styles.flex1} />
                <Field label="LAST NAME" value={lastName} onChangeText={setLastName} style={styles.flex1} />
              </View>
              <Field label="EMAIL" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" />
              <Field label="LOCATION" value={location} onChangeText={setLocation} placeholder="e.g. Seattle, WA" />
            </>
          )}

          <Field label="USER ID" value={userid} onChangeText={setUserid} autoCapitalize="none" placeholder="e.g. sid2028" />
          <Field label="PASSWORD" value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" />

          {isRegister && (
            <View style={styles.consent}>
              <ConsentRow label="I'm 18 or older" value={isAdult} onValueChange={setIsAdult} />
              <ConsentRow label="If under 18, I have a parent/guardian's permission (Terms §2)" value={parentalConsent} onValueChange={setParentalConsent} />
              <ConsentRow label="I accept the Terms and Privacy Policy" value={acceptedTerms} onValueChange={setAcceptedTerms} />
            </View>
          )}

          {!!error && <Txt style={styles.error}>{error}</Txt>}

          <PopButton label={isRegister ? 'Create account' : 'Sign In'} onPress={submit} loading={busy} full />

          <View style={styles.dividerRow}>
            <View style={styles.rule} />
            <Txt variant="small">OR</Txt>
            <View style={styles.rule} />
          </View>

          <PopButton label="Continue with Google" variant="secondary" onPress={google} disabled={busy} full />

          <Pressable onPress={() => { setError(null); setMode(isRegister ? 'login' : 'register'); }} style={styles.center}>
            <Txt variant="small">
              {isRegister ? 'Have an account? ' : "Don't have an account? "}
              <Txt variant="small" style={styles.link}>{isRegister ? 'Sign in' : 'Register'}</Txt>
            </Txt>
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function ConsentRow({ label, value, onValueChange }: { label: string; value: boolean; onValueChange: (v: boolean) => void }) {
  return (
    <View style={styles.consentRow}>
      <Switch value={value} onValueChange={onValueChange} trackColor={{ true: colors.orange, false: colors.hairline }} thumbColor={colors.white} />
      <Txt variant="small" style={styles.flex1}>{label}</Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.cream },
  scroll: { flexGrow: 1, justifyContent: 'center', padding: space.lg },
  card: { backgroundColor: colors.white, borderRadius: radius.xl, padding: space.xl, gap: space.md, width: '100%', maxWidth: 440, alignSelf: 'center' },
  brand: { alignItems: 'center', gap: 6 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  beta: { backgroundColor: colors.yellow, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  betaText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 10, color: colors.navyDeep, letterSpacing: 0.5 },
  tagline: { textAlign: 'center', color: colors.ink, marginTop: 4 },
  center: { textAlign: 'center', alignItems: 'center' },
  notice: { backgroundColor: '#FFF7E6', borderRadius: radius.md, padding: space.md },
  noticeText: { color: '#8A6D1A', textAlign: 'center' },
  twoCol: { flexDirection: 'row', gap: space.md },
  flex1: { flex: 1 },
  consent: { gap: space.sm },
  consentRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  error: { color: colors.red, fontFamily: 'PlusJakartaSans_700Bold', textAlign: 'center' },
  dividerRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  rule: { flex: 1, height: 1, backgroundColor: colors.hairline },
  link: { color: colors.orangeDeep, fontFamily: 'PlusJakartaSans_700Bold' },
});
