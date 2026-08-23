import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useAuth } from '@/auth/AuthContext';

// Resumes the Google redirect flow. Reads the one-time `google_token` (web: from the URL the
// backend redirected to; native: passed as a route param by the login screen), resolves it,
// and either enters the app (existing/linked account) or collects consent + location for a
// new account before finishing.
export default function GoogleAuth() {
  const router = useRouter();
  const { googleSession, googleFinish } = useAuth();
  const params = useLocalSearchParams<{ google_token?: string }>();
  const handoff = typeof params.google_token === 'string' ? params.google_token : '';

  const [phase, setPhase] = useState<'resolving' | 'pending' | 'error'>('resolving');
  const [error, setError] = useState<string | null>(null);
  const [pendingInfo, setPendingInfo] = useState<{ firstName?: string; lastName?: string; email?: string }>({});
  const [busy, setBusy] = useState(false);

  // Consent form (new account)
  const [location, setLocation] = useState('');
  const [isAdult, setIsAdult] = useState(false);
  const [parentalConsent, setParentalConsent] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  // The handoff token is single-use — resolve it exactly once even under StrictMode.
  const resolved = useRef(false);

  useEffect(() => {
    if (resolved.current) return;
    resolved.current = true;
    if (!handoff) {
      setError('Missing sign-in token. Please try again.');
      setPhase('error');
      return;
    }
    (async () => {
      try {
        const result = await googleSession(handoff);
        if (result.status === 'session') {
          router.replace('/(app)');
        } else {
          setPendingInfo(result);
          setPhase('pending');
        }
      } catch (e) {
        setError((e as Error).message || 'Google sign-in failed.');
        setPhase('error');
      }
    })();
  }, [handoff, googleSession, router]);

  async function finish() {
    setError(null);
    setBusy(true);
    try {
      await googleFinish(handoff, { location: location.trim(), isAdult, parentalConsent, acceptedTerms });
      router.replace('/(app)');
    } catch (e) {
      setError((e as Error).message || 'Could not finish sign-up.');
    } finally {
      setBusy(false);
    }
  }

  if (phase === 'resolving') {
    return (
      <View style={styles.center}>
        <ActivityIndicator />
        <Text style={styles.dim}>Signing you in…</Text>
      </View>
    );
  }

  if (phase === 'error') {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error}</Text>
        <Pressable style={styles.link} onPress={() => router.replace('/login')}>
          <Text style={styles.linkText}>Back to sign in</Text>
        </Pressable>
      </View>
    );
  }

  // pending: finish creating the account
  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Text style={styles.title}>Almost there</Text>
      <Text style={styles.subtitle}>
        Finish setting up{pendingInfo.firstName ? `, ${pendingInfo.firstName}` : ''}
        {pendingInfo.email ? ` (${pendingInfo.email})` : ''}
      </Text>

      <Text style={styles.label}>Location</Text>
      <TextInput style={styles.input} value={location} onChangeText={setLocation} autoCorrect={false} />

      <View style={styles.consent}>
        <Row label="I am 18 or older" value={isAdult} onValueChange={setIsAdult} />
        <Row
          label="If under 18, I have parent/guardian permission (Terms §2)"
          value={parentalConsent}
          onValueChange={setParentalConsent}
        />
        <Row
          label="I accept the Terms and Privacy Policy"
          value={acceptedTerms}
          onValueChange={setAcceptedTerms}
        />
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      <Pressable style={[styles.button, busy && styles.buttonDisabled]} onPress={finish} disabled={busy}>
        {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Create account</Text>}
      </Pressable>
    </ScrollView>
  );
}

function Row({
  label,
  value,
  onValueChange,
}: {
  label: string;
  value: boolean;
  onValueChange: (v: boolean) => void;
}) {
  return (
    <View style={styles.row}>
      <Switch value={value} onValueChange={onValueChange} />
      <Text style={styles.rowLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10, padding: 24 },
  container: { padding: 24, gap: 12, maxWidth: 460, width: '100%', alignSelf: 'center', flexGrow: 1, justifyContent: 'center' },
  title: { fontSize: 24, fontWeight: '800', textAlign: 'center' },
  subtitle: { fontSize: 14, color: '#666', textAlign: 'center', marginBottom: 8 },
  label: { fontSize: 13, color: '#444', fontWeight: '600' },
  input: { borderWidth: 1, borderColor: '#cbd2e0', borderRadius: 10, padding: 12, fontSize: 16 },
  consent: { gap: 10, marginTop: 4 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  rowLabel: { flex: 1, fontSize: 13, color: '#444' },
  dim: { color: '#889', fontSize: 13 },
  error: { color: '#b91c1c', fontSize: 14, textAlign: 'center' },
  button: { backgroundColor: '#2563eb', borderRadius: 10, padding: 14, alignItems: 'center', marginTop: 8 },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  link: { marginTop: 8 },
  linkText: { color: '#2563eb' },
});
