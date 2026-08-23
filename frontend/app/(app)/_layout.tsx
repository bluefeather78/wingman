import { Redirect, Tabs } from 'expo-router';
import { useAuth } from '@/auth/AuthContext';

// The authed app shell. Four primary destinations from the old page model: Home/Dashboard,
// Finder/Wizard, Tracker, Profile. Guards the stack — a signed-out user is bounced to login,
// which is also where a 401 (via AuthExpiredError) lands after the session is dropped.
export default function AppLayout() {
  const { ready, user } = useAuth();
  if (ready && !user) return <Redirect href="/login" />;
  return (
    <Tabs screenOptions={{ headerShown: true }}>
      <Tabs.Screen name="index" options={{ title: 'Home' }} />
      <Tabs.Screen name="finder" options={{ title: 'Finder' }} />
      <Tabs.Screen name="tracker" options={{ title: 'Tracker' }} />
      <Tabs.Screen name="profile" options={{ title: 'Profile' }} />
    </Tabs>
  );
}
