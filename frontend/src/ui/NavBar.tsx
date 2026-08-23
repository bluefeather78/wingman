import { Ionicons } from '@expo/vector-icons';
import { usePathname, useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { colors, fonts, radius, space } from './theme';

// The branded top nav from the live app: navy bar, bar-chart wordmark + BETA badge, four
// pill tabs (orange when active), and a teal avatar. Tab names are the product's own
// vocabulary — Home Base / My Vibe / Fresh Finds / Quest Log — not the route names.
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

  return (
    <SafeAreaView edges={['top']} style={styles.safe}>
      <View style={styles.bar}>
        <View style={styles.brand}>
          <Ionicons name="stats-chart" size={18} color={colors.orange} />
          <Text style={styles.word}>Wingman</Text>
          <View style={styles.beta}>
            <Text style={styles.betaText}>BETA</Text>
          </View>
        </View>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.tabs}
        >
          {TABS.map((t) => {
            const active = isActive(pathname, t.path);
            return (
              <Pressable
                key={t.path}
                onPress={() => router.push(t.path as never)}
                style={[styles.tab, active && styles.tabActive]}
              >
                <Ionicons name={t.icon} size={15} color={active ? colors.white : '#B7C6DE'} />
                <Text style={[styles.tabText, active && styles.tabTextActive]}>{t.label}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <Pressable style={styles.avatar} onPress={() => router.push('/(app)/profile')}>
          <Ionicons name="person" size={16} color={colors.white} />
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { backgroundColor: colors.cream },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.navy,
    borderRadius: radius.pill,
    marginHorizontal: space.md,
    marginTop: space.sm,
    paddingVertical: 8,
    paddingHorizontal: space.md,
    gap: space.md,
  },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  word: { fontFamily: fonts.display, fontSize: 17, color: colors.white },
  beta: { backgroundColor: colors.yellow, borderRadius: radius.pill, paddingHorizontal: 7, paddingVertical: 1 },
  betaText: { fontFamily: fonts.bodyBold, fontSize: 9, color: colors.navyDeep, letterSpacing: 0.5 },
  tabs: { alignItems: 'center', gap: 4, flexGrow: 1, justifyContent: 'center' },
  tab: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 7, paddingHorizontal: 12, borderRadius: radius.pill },
  tabActive: { backgroundColor: colors.orange },
  tabText: { fontFamily: fonts.bodyBold, fontSize: 13, color: '#B7C6DE' },
  tabTextActive: { color: colors.white },
  avatar: { width: 34, height: 34, borderRadius: 17, backgroundColor: colors.teal, alignItems: 'center', justifyContent: 'center' },
});
