import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, StyleSheet, Switch, View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { beginGoogleSignIn } from '@/auth/googleSignIn';
import { Field, PopButton, PopCard, Screen, Txt } from '@/ui/components';
import { colors, space } from '@/ui/theme';

// Login / Register (password path, Phase 2 contract). The client SHA-256s the password
// before sending. Google sign-in kicks off the redirect flow.
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
        await register({
          firstName: firstName.trim(),
          lastName: lastName.trim(),
          email: email.trim(),
          userid: userid.trim().toLowerCase(),
          location: location.trim(),
          password,
          isAdult,
          parentalConsent,
          acceptedTerms,
        });
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
      if (Platform.OS !== 'web' && handoff) {
        router.replace({ pathname: '/google-auth', params: { google_token: handoff } });
      }
    } catch (e) {
      setError((e as Error).message || 'Google sign-in failed.');
    }
  }

  return (
    <Screen>
      {/* Hero wordmark — the loud, characteristic thing first. */}
      <View style={styles.hero}>
        <View style={styles.wordmarkRow}>
          <Txt variant="hero" style={styles.wordmark}>
            Highschool
          </Txt>
          <View style={styles.wingBadge}>
            <Txt variant="hero" style={styles.wingText}>
              Wingman
            </Txt>
          </View>
        </View>
        <Txt variant="body" style={styles.tagline}>
          Find and track the programs, internships, and competitions worth your time.
        </Txt>
      </View>

      <PopCard style={styles.formCard}>
        <Txt variant="h2">{isRegister ? 'Create your account' : 'Welcome back'}</Txt>

        {isRegister && (
          <>
            <View style={styles.twoCol}>
              <Field label="First name" value={firstName} onChangeText={setFirstName} style={styles.flex1} />
              <Field label="Last name" value={lastName} onChangeText={setLastName} style={styles.flex1} />
            </View>
            <Field
              label="Email"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
            />
            <Field label="Location" value={location} onChangeText={setLocation} placeholder="e.g. Seattle, WA" />
          </>
        )}

        <Field label="User ID" value={userid} onChangeText={setUserid} autoCapitalize="none" />
        <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry />

        {isRegister && (
          <View style={styles.consent}>
            <ConsentRow label="I'm 18 or older" value={isAdult} onValueChange={setIsAdult} />
            <ConsentRow
              label="If under 18, I have a parent/guardian's permission (Terms §2)"
              value={parentalConsent}
              onValueChange={setParentalConsent}
            />
            <ConsentRow
              label="I accept the Terms and Privacy Policy"
              value={acceptedTerms}
              onValueChange={setAcceptedTerms}
            />
          </View>
        )}

        {!!error && <Txt style={styles.error}>{error}</Txt>}

        <PopButton
          label={isRegister ? 'Create account' : 'Log in'}
          onPress={submit}
          loading={busy}
          full
          style={styles.mt}
        />

        <View style={styles.dividerRow}>
          <View style={styles.rule} />
          <Txt variant="small">or</Txt>
          <View style={styles.rule} />
        </View>

        <PopButton label="Continue with Google" variant="secondary" onPress={google} disabled={busy} full />

        <PopButton
          label={isRegister ? 'Have an account? Log in' : 'New here? Create an account'}
          variant="ghost"
          onPress={() => {
            setError(null);
            setMode(isRegister ? 'login' : 'register');
          }}
          full
        />
      </PopCard>
    </Screen>
  );
}

function ConsentRow({
  label,
  value,
  onValueChange,
}: {
  label: string;
  value: boolean;
  onValueChange: (v: boolean) => void;
}) {
  return (
    <View style={styles.consentRow}>
      <Switch
        value={value}
        onValueChange={onValueChange}
        trackColor={{ true: colors.purple, false: colors.hairline }}
        thumbColor={colors.white}
      />
      <Txt variant="small" style={styles.consentLabel}>
        {label}
      </Txt>
    </View>
  );
}

const styles = StyleSheet.create({
  hero: { gap: space.sm, marginTop: space.lg, marginBottom: space.sm },
  wordmarkRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  wordmark: { color: colors.navy },
  wingBadge: {
    backgroundColor: colors.lime,
    borderWidth: 3,
    borderColor: colors.navy,
    borderRadius: 14,
    paddingHorizontal: 10,
    paddingVertical: 2,
    transform: [{ rotate: '-2deg' }],
  },
  wingText: { color: colors.ink },
  tagline: { maxWidth: 460 },
  formCard: { gap: space.md },
  twoCol: { flexDirection: 'row', gap: space.md },
  flex1: { flex: 1 },
  consent: { gap: space.sm, marginTop: space.xs },
  consentRow: { flexDirection: 'row', alignItems: 'center', gap: space.md },
  consentLabel: { flex: 1 },
  error: { color: colors.red, fontFamily: 'PlusJakartaSans_700Bold' },
  mt: { marginTop: space.xs },
  dividerRow: { flexDirection: 'row', alignItems: 'center', gap: space.md, marginVertical: 2 },
  rule: { flex: 1, height: 2, backgroundColor: colors.hairline, borderRadius: 2 },
});
