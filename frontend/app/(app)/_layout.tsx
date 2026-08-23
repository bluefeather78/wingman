import { Redirect, Slot } from 'expo-router';
import { View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { NavBar } from '@/ui/NavBar';
import { colors } from '@/ui/theme';

// The authed app shell: the branded top nav over the active route. Matches the live app's
// top-nav layout (rather than bottom tabs) on every platform. Signed-out users bounce to login.
export default function AppLayout() {
  const { ready, user } = useAuth();
  if (ready && !user) return <Redirect href="/login" />;
  return (
    <View style={{ flex: 1, backgroundColor: colors.cream }}>
      <NavBar />
      <Slot />
    </View>
  );
}
