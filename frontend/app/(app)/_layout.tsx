import { Redirect, Slot, usePathname } from 'expo-router';
import { View } from 'react-native';
import { useAuth } from '@/auth/AuthContext';
import { NavBar } from '@/ui/NavBar';
import { colors } from '@/ui/theme';

// The authed app shell: the branded top nav over the active route. Matches the live app's
// top-nav layout (rather than bottom tabs) on every platform. Signed-out users bounce to login.
//
// It is also the PAYWALL. An account whose trial or subscription has ended keeps its
// session — it is signed in, it just may not use the app — so it is sent to Manage Plan
// and cannot leave it. `has_access` comes from the server's subscription_state() via the
// login/refresh payload and every 402, so the client and the server cannot disagree about
// who is blocked; this half only decides what the student SEES. The server-side gate
// (app/deps.require_subscription) is the real control and refuses the same accounts even
// if this screen is bypassed.
//
// Blocked only on an explicit `false`: a session cached before the field existed leaves it
// undefined, and locking those out on a missing value would paywall people whose accounts
// are fine. The next refresh fills it in, and any request they make 402s meanwhile.
const PAYWALL_ROUTE = '/subscription';

export default function AppLayout() {
  const { ready, user } = useAuth();
  const pathname = usePathname();
  if (ready && !user) return <Redirect href="/login" />;
  const blocked = user?.subscription?.has_access === false;
  if (blocked && !pathname.endsWith(PAYWALL_ROUTE)) {
    return <Redirect href="/(app)/subscription" />;
  }
  return (
    <View style={{ flex: 1, backgroundColor: colors.cream }}>
      <NavBar locked={blocked} />
      <Slot />
    </View>
  );
}
