import { useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { AuthExpiredError } from '@/api/ApiClient';
import { httpClient } from '@/api/httpClient';
import { useAuth } from '@/auth/AuthContext';

// Home / Dashboard. Also performs one authed data round-trip on mount (save -> load) as a
// live check that the Bearer token reaches gated routes, and demonstrates the shared 401
// handling: an AuthExpiredError (refresh failed) drops the session and bounces to login.
export default function Home() {
  const router = useRouter();
  const { logout } = useAuth();
  const [status, setStatus] = useState('Checking your saved data…');

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const marker = { seen: true };
        await httpClient.saveData('rn-smoke', marker);
        const loaded = await httpClient.loadData<{ seen?: boolean }>('rn-smoke');
        if (alive) setStatus(loaded?.seen ? 'Authed data round-trip OK.' : 'Loaded, no marker.');
      } catch (e) {
        if (e instanceof AuthExpiredError) {
          await logout();
          router.replace('/login');
          return;
        }
        if (alive) setStatus(`Data error: ${(e as Error).message}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, [logout, router]);

  return (
    <View style={styles.container}>
      <Text style={styles.h1}>Dashboard</Text>
      <Text style={styles.body}>Progress and todo counts land here.</Text>
      <Text style={styles.status}>{status}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 24, gap: 8 },
  h1: { fontSize: 22, fontWeight: '700' },
  body: { color: '#555' },
  status: { color: '#2563eb', marginTop: 8 },
});
