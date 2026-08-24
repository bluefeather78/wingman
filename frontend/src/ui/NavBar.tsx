import { Ionicons } from '@expo/vector-icons';
import { usePathname, useRouter } from 'expo-router';
import { useState } from 'react';
import { Modal, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/auth/AuthContext';
import { Logo, PopButton, Txt } from './components';
import { APP_MAX_WIDTH, colors, fonts, navShadow, radius, space } from './theme';

// The live app's floating pill navigation: sticky, centered in the max-w-4xl column with
// 16px top inset, navy pill with a soft blue glow. Wordmark + BETA, four tabs (orange when
// active, #B7D3E8 otherwise), and the teal 👤 account badge which opens the account drawer.
type IconName = React.ComponentProps<typeof Ionicons>['name'];
const TABS: { label: string; path: string; icon: IconName }[] = [
  { label: 'Home Base', path: '/(app)', icon: 'home-outline' },
  { label: 'My Vibe', path: '/(app)/profile', icon: 'person-outline' },
  { label: 'Fresh Finds', path: '/(app)/finder', icon: 'search-outline' },
  { label: 'Quest Log', path: '/(app)/tracker', icon: 'calendar-outline' },
];

function isActive(pathname: string, tabPath: string): boolean {
  if (tabPath === '/(app)') return pathname === '/' || pathname === '/(app)' || pathname === '';
  const leaf = tabPath.replace('/(app)', '');
  return pathname === leaf || pathname.endsWith(leaf);
}

export function NavBar() {
  const router = useRouter();
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);

  async function handleLogout() {
    setDrawerOpen(false);
    await logout();
    router.replace('/login');
  }

  return (
    <SafeAreaView edges={['top']} style={styles.safe}>
      <View style={styles.column}>
        <View style={[styles.bar, navShadow()]}>
          <Pressable style={styles.brand} onPress={() => router.push('/(app)' as never)}>
            <Logo size={32} />
            <Text style={styles.word}>Wingman</Text>
            <View style={styles.beta}>
              <Text style={styles.betaText}>BETA</Text>
            </View>
          </Pressable>

          <View style={styles.tabs}>
            {TABS.map((t) => {
              const active = isActive(pathname, t.path);
              return (
                <Pressable
                  key={t.path}
                  onPress={() => router.push(t.path as never)}
                  style={[styles.tab, active && styles.tabActive]}
                >
                  <Ionicons name={t.icon} size={18} color={active ? colors.white : '#B7D3E8'} />
                  <Text style={[styles.tabText, active && styles.tabTextActive]}>{t.label}</Text>
                </Pressable>
              );
            })}
          </View>

          <Pressable style={styles.avatar} onPress={() => setDrawerOpen(true)}>
            <Text style={styles.avatarEmoji}>👤</Text>
          </Pressable>
        </View>
      </View>

      {/* Account drawer — right side panel, like the live app's #profilePanel. */}
      <Modal visible={drawerOpen} transparent animationType="fade" onRequestClose={() => setDrawerOpen(false)}>
        <Pressable style={styles.scrim} onPress={() => setDrawerOpen(false)}>
          <Pressable style={styles.drawer} onPress={(e) => e.stopPropagation()}>
            <View style={styles.drawerHead}>
              <Txt variant="h3">Your Account</Txt>
              <Pressable onPress={() => setDrawerOpen(false)} hitSlop={10}>
                <Text style={styles.close}>✕</Text>
              </Pressable>
            </View>
            <View style={styles.accountBox}>
              <View style={styles.accountRow}>
                <View style={styles.flex1}>
                  <Text style={styles.accountName}>
                    {[user?.firstName, user?.lastName].filter(Boolean).join(' ') || user?.userid}
                  </Text>
                  {!!user?.email && <Text style={styles.accountEmail}>{user.email}</Text>}
                </View>
                <Pressable onPress={handleLogout}>
                  <Text style={styles.logout}>Log out</Text>
                </Pressable>
              </View>
            </View>
            <View style={styles.betaBox}>
              <Text style={styles.betaBoxTitle}>🚧 Beta product</Text>
              <Text style={styles.betaBoxText}>
                You're using an early, actively-evolving version of Wingman. Thanks for testing it out!
              </Text>
            </View>
            <PopButton label="Log out" variant="ink" small square onPress={handleLogout} shadowColor={colors.ink} />
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    backgroundColor: colors.cream,
    zIndex: 50,
    ...(Platform.OS === 'web' ? ({ position: 'sticky', top: 0 } as object) : null),
  },
  column: { width: '100%', maxWidth: APP_MAX_WIDTH, alignSelf: 'center', paddingHorizontal: space.lg, paddingTop: space.lg },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: colors.navy,
    borderRadius: radius.pill,
    paddingVertical: 8,
    paddingLeft: 12,
    paddingRight: 8,
    gap: 8,
  },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  word: { fontFamily: fonts.display, fontSize: 18, color: colors.white, letterSpacing: -0.3 },
  beta: { backgroundColor: colors.orange, borderRadius: radius.pill, paddingHorizontal: 8, paddingVertical: 2 },
  betaText: { fontFamily: fonts.bodyXBold, fontSize: 9, color: colors.white, letterSpacing: 0.5, textTransform: 'uppercase' },
  tabs: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tab: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 16, borderRadius: radius.pill },
  tabActive: { backgroundColor: colors.orange },
  tabText: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#B7D3E8' },
  tabTextActive: { color: colors.white },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.teal, alignItems: 'center', justifyContent: 'center' },
  avatarEmoji: { fontSize: 16, color: colors.white },

  scrim: { flex: 1, backgroundColor: 'rgba(15,23,42,0.4)', flexDirection: 'row', justifyContent: 'flex-end' },
  drawer: {
    width: 320,
    maxWidth: '90%',
    backgroundColor: colors.white,
    borderLeftWidth: 4,
    borderLeftColor: colors.slate900,
    padding: space.xl,
    gap: space.lg,
    height: '100%',
  },
  drawerHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 2, borderBottomColor: colors.slate900, paddingBottom: space.lg },
  close: { fontFamily: fonts.bodyBold, fontSize: 18, color: colors.slate500 },
  accountBox: { backgroundColor: colors.slate50, borderWidth: 2, borderColor: colors.slate200, borderRadius: radius.lg, padding: space.md },
  accountRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  flex1: { flex: 1, minWidth: 0 },
  accountName: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.slate900 },
  accountEmail: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500 },
  logout: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.rose600 },
  betaBox: { backgroundColor: colors.amber50, borderWidth: 2, borderColor: colors.amber200, borderRadius: radius.lg, padding: space.md, gap: 4 },
  betaBoxTitle: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.amber700 },
  betaBoxText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.amber700 },
});
