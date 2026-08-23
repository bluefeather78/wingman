import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { beginGoogleSignIn } from '@/auth/googleSignIn';

// Login / Register screen (password path, per the Phase 2 contract). The client SHA-256s the
// password before sending. Google sign-in is a separate redirect/deep-link flow — see the
// note below — and is not wired here yet.
export default function Login() {
  const router = useRouter();
  const { login, register } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Shared
  const [userid, setUserid] = useState('');
  const [password, setPassword] = useState('');
  // Register-only
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [location, setLocation] = useState('');
  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(userid.trim().toLowerCase(), password);
      } else {
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
      // Web redirects away here; native returns a handoff token to resume on /google-auth.
      const handoff = await beginGoogleSignIn();
      if (Platform.OS !== 'web' && handoff) {
        router.replace({ pathname: '/google-auth', params: { google_token: handoff } });
      }
    } catch (e) {
      setError((e as Error).message || 'Google sign-in failed.');
    }
  }

  const isRegister = mode === 'register';

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Highschool Wingman</Text>
      <Text style={styles.subtitle}>{isRegister ? 'Create your account' : 'Welcome back'}</Text>

      {isRegister && (
        <>
          <Field label="First name" value={firstName} onChangeText={setFirstName} />
          <Field label="Last name" value={lastName} onChangeText={setLastName} />
          <Field
            label="Email"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
          />
          <Field label="Location" value={location} onChangeText={setLocation} />
        </>
      )}

      <Field label="User ID" value={userid} onChangeText={setUserid} autoCapitalize="none" />
      <Field label="Password" value={password} onChangeText={setPassword} secureTextEntry />

      {isRegister && (
        <View style={styles.consent}>
          <Check label="I am 18 or older" value={isAdult} onValueChange={setIsAdult} />
          <Check
            label="If under 18, I have parent/guardian permission (Terms §2)"
            value={parentalConsent}
            onValueChange={setParentalConsent}
          />
          <Check
            label="I accept the Terms and Privacy Policy"
            value={acceptedTerms}
            onValueChange={setAcceptedTerms}
          />
        </View>
      )}

      {error && <Text style={styles.error}>{error}</Text>}

      <Pressable
        style={[styles.button, busy && styles.buttonDisabled]}
        onPress={submit}
        disabled={busy}
      >
        {busy ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.buttonText}>{isRegister ? 'Create account' : 'Log in'}</Text>
        )}
      </Pressable>

      <Pressable
        onPress={() => {
          setError(null);
          setMode(isRegister ? 'login' : 'register');
        }}
      >
        <Text style={styles.switch}>
          {isRegister ? 'Have an account? Log in' : "New here? Create an account"}
        </Text>
      </Pressable>

      <View style={styles.divider}>
        <Text style={styles.dividerText}>or</Text>
      </View>

      <Pressable style={styles.googleBtn} onPress={google} disabled={busy}>
        <Text style={styles.googleText}>Continue with Google</Text>
      </Pressable>
    </ScrollView>
  );
}

function Field({
  label,
  ...props
}: { label: string } & React.ComponentProps<typeof TextInput>) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput style={styles.input} autoCorrect={false} {...props} />
    </View>
  );
}

function Check({
  label,
  value,
  onValueChange,
}: {
  label: string;
  value: boolean;
  onValueChange: (v: boolean) => void;
}) {
  return (
    <View style={styles.checkRow}>
      <Switch value={value} onValueChange={onValueChange} />
      <Text style={styles.checkLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, gap: 12, maxWidth: 460, width: '100%', alignSelf: 'center', flexGrow: 1, justifyContent: 'center' },
  title: { fontSize: 26, fontWeight: '800', textAlign: 'center' },
  subtitle: { fontSize: 15, color: '#666', textAlign: 'center', marginBottom: 8 },
  field: { gap: 4 },
  label: { fontSize: 13, color: '#444', fontWeight: '600' },
  input: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 12, fontSize: 16 },
  consent: { gap: 10, marginTop: 4 },
  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  checkLabel: { flex: 1, fontSize: 13, color: '#444' },
  error: { color: '#b91c1c', fontSize: 14 },
  button: { backgroundColor: '#2563eb', borderRadius: 10, padding: 14, alignItems: 'center', marginTop: 8 },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  switch: { color: '#2563eb', textAlign: 'center', marginTop: 12 },
  divider: { alignItems: 'center', marginVertical: 6 },
  dividerText: { color: '#9aa3b2', fontSize: 12 },
  googleBtn: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 12, alignItems: 'center' },
  googleText: { color: '#1a2540', fontWeight: '600', fontSize: 15 },
});
