import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, View } from 'react-native';
import {
  countItems,
  loadTrackerData,
  removeTrackerItem,
  type TrackerData,
  type TrackerItem,
} from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import { Badge, PopButton, PopCard, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

// Category label per bucket (matches the pills the web app shows).
const CATEGORY: Record<Bucket, string> = {
  summerPrograms: 'SUMMER PROGRAM',
  internships: 'INTERNSHIP',
  researchCompetitions: 'RESEARCH COMPETITION',
  pureCompetitions: 'ACADEMIC COMPETITION',
  conferences: 'CONFERENCE VENUE',
  journals: 'JOURNAL VENUE',
};
const MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const MONTHS_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// Event colour by date type.
const TYPE_COLOR: Record<string, string> = {
  opens: colors.green,
  deadline: colors.orange,
  event_start: colors.navy,
  event_end: colors.navy,
  other: colors.purple,
};
function typeColor(t: string): string {
  return TYPE_COLOR[t] ?? colors.purple;
}

export default function Tracker() {
  const router = useRouter();
  const [data, setData] = useState<TrackerData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'calendar' | 'list'>('calendar');

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      loadTrackerData().then((d) => alive && setData(d)).catch((e) => alive && setError((e as Error).message));
      return () => {
        alive = false;
      };
    }, []),
  );

  async function remove(id: string) {
    setData(await removeTrackerItem(id));
  }

  const count = data ? countItems(data) : 0;

  return (
    <Screen>
      <View style={styles.topRow}>
        <Txt variant="small">Last checked: never</Txt>
        <PopButton label="+ Add Opportunity" small onPress={() => router.push('/(app)/finder')} />
      </View>

      <View style={styles.headRow}>
        <View style={styles.titleWrap}>
          <Txt variant="h1">Actively Tracked</Txt>
          <View style={styles.countPill}>
            <Txt style={styles.countText}>{String(count).padStart(2, '0')}</Txt>
          </View>
        </View>
        <View style={styles.segment}>
          {(['calendar', 'list'] as const).map((v) => (
            <Pressable key={v} onPress={() => setView(v)} style={[styles.segBtn, view === v && styles.segActive]}>
              <Ionicons name={v === 'calendar' ? 'calendar-outline' : 'list-outline'} size={14} color={view === v ? colors.white : colors.navy} />
              <Txt variant="bodyStrong" style={{ color: view === v ? colors.white : colors.navy, fontSize: 13 }}>
                {v === 'calendar' ? 'Calendar' : 'List'}
              </Txt>
            </Pressable>
          ))}
        </View>
      </View>

      {error ? (
        <SoftCard><Txt variant="body">Couldn't load your tracker: {error}</Txt></SoftCard>
      ) : !data ? (
        <SoftCard><Txt variant="body">Loading…</Txt></SoftCard>
      ) : count === 0 ? (
        <SoftCard><Txt variant="body" style={styles.italic}>Nothing on the calendar yet — add opportunities via the Finder or the button above.</Txt></SoftCard>
      ) : view === 'list' ? (
        <ListView data={data} onRemove={remove} />
      ) : (
        <CalendarView data={data} />
      )}
    </Screen>
  );
}

// ---------- List ----------
function ListView({ data, onRemove }: { data: TrackerData; onRemove: (id: string) => void }) {
  return (
    <View style={{ gap: space.lg }}>
      {ALL_BUCKETS.map((bucket) =>
        data[bucket].map((item) => <ListCard key={item.id} item={item} bucket={bucket} onRemove={onRemove} />),
      )}
    </View>
  );
}

function ListCard({ item, bucket, onRemove }: { item: TrackerItem; bucket: Bucket; onRemove: (id: string) => void }) {
  const dates = (item.importantDates ?? []).slice().sort((a, b) => a.dateISO.localeCompare(b.dateISO));
  const half = Math.ceil(dates.length / 2);
  const col1 = dates.slice(0, half);
  const col2 = dates.slice(half);
  const wellReviewed = !!item.reviewStatus && item.reviewStatus !== 'insufficient_data' && item.reviewStatus !== 'concerns_found';
  const future = dates.some((d) => Date.parse(d.dateISO) >= Date.now());

  return (
    <PopCard style={{ gap: space.sm }}>
      <View style={styles.badgeRow}>
        <Badge label={CATEGORY[bucket]} bg={colors.lavender} fg={colors.purple} />
        {wellReviewed && <Badge label="✓ WELL REVIEWED" bg={colors.greenSoft} fg={colors.green} />}
        <View style={styles.flex1} />
        <Pressable onPress={() => onRemove(item.id)} hitSlop={8}><Ionicons name="close-circle-outline" size={22} color={colors.muted} /></Pressable>
      </View>
      <Txt variant="h2">{item.name}</Txt>
      {!!item.meta && <Txt variant="small" style={{ color: colors.inkSoft }}>{item.meta}</Txt>}

      {item.wasEstimated && (
        <View style={styles.predBanner}>
          <Txt variant="small" style={styles.predText}>⚠ Predicted dates from past cycle.</Txt>
        </View>
      )}

      {dates.length > 0 ? (
        <View style={styles.dateTable}>
          <View style={styles.flex1}>{col1.map((d, i) => <DateRow key={i} d={d} />)}</View>
          <View style={styles.flex1}>{col2.map((d, i) => <DateRow key={i} d={d} />)}</View>
        </View>
      ) : (
        <Txt variant="small">{item.deadlineLabel || 'No dates yet.'}</Txt>
      )}

      <View style={styles.cardFoot}>
        {future ? <Badge label="FUTURE EVENT" bg="transparent" fg={colors.teal} outline /> : item.status === 'running' ? <Badge label="HAPPENING NOW" bg="transparent" fg={colors.green} outline /> : <View />}
        {!!(item.applyUrl || item.url) && (
          <PopButton label={item.applyLabel || 'Apply now'} small onPress={() => Linking.openURL((item.applyUrl || item.url) as string)} />
        )}
      </View>
    </PopCard>
  );
}

function DateRow({ d }: { d: { label: string; dateISO: string; type: string } }) {
  return (
    <View style={styles.dRow}>
      <Txt variant="bodyStrong" style={styles.dDate}>{fmt(d.dateISO)}</Txt>
      <Txt variant="body" style={styles.flex1} numberOfLines={2}>{d.label}</Txt>
    </View>
  );
}

// ---------- Calendar (per-bucket horizontal month timeline) ----------
function CalendarView({ data }: { data: TrackerData }) {
  return (
    <View style={{ gap: space.lg }}>
      {ALL_BUCKETS.map((bucket) => {
        const items = data[bucket];
        if (!items.length) return null;
        // Collect events for this bucket, grouped by YYYY-MM.
        const byMonth = new Map<string, { day: number; dateISO: string; name: string; label: string; type: string }[]>();
        items.forEach((it) =>
          (it.importantDates ?? []).forEach((d) => {
            const t = Date.parse(d.dateISO);
            if (Number.isNaN(t)) return;
            const key = d.dateISO.slice(0, 7);
            const day = Number(d.dateISO.slice(8, 10));
            const list = byMonth.get(key) ?? [];
            list.push({ day, dateISO: d.dateISO, name: it.name, label: d.label, type: d.type });
            byMonth.set(key, list);
          }),
        );
        const months = Array.from(byMonth.keys()).sort();
        if (!months.length) return null;
        return (
          <SoftCard key={bucket} style={{ gap: space.sm }}>
            <Txt variant="label">{CATEGORY[bucket]}</Txt>
            <ScrollView horizontal showsHorizontalScrollIndicator contentContainerStyle={styles.monthsRow}>
              {months.map((mk) => {
                const [y, m] = mk.split('-').map(Number);
                const evs = (byMonth.get(mk) ?? []).sort((a, b) => a.day - b.day);
                return (
                  <View key={mk} style={styles.monthCol}>
                    <Txt variant="small" style={styles.monthHead}>{MONTHS_ABBR[m - 1].toUpperCase()} {y}</Txt>
                    {evs.map((e, i) => (
                      <View key={i} style={[styles.evCard, { borderLeftColor: typeColor(e.type) }]}>
                        <View style={styles.evTop}>
                          <Txt variant="h3" style={{ color: typeColor(e.type) }}>{e.day}</Txt>
                          <Txt variant="small" style={styles.flex1} numberOfLines={2}>{e.name}</Txt>
                        </View>
                        <Txt variant="small" style={styles.evLabel} numberOfLines={1}>— {e.label}</Txt>
                        <Txt style={styles.evType}>{e.type.replace('_', ' ').toUpperCase()}</Txt>
                      </View>
                    ))}
                  </View>
                );
              })}
            </ScrollView>
          </SoftCard>
        );
      })}
    </View>
  );
}

function fmt(iso: string): string {
  const [, m, d] = iso.split('-').map(Number);
  if (!m || !d) return iso;
  return `${MONTHS_ABBR[m - 1]} ${d}`;
}
void MONTHS_FULL;

const styles = StyleSheet.create({
  topRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md },
  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.md, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: space.sm },
  countPill: { borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 2 },
  countText: { fontFamily: 'PlusJakartaSans_700Bold', color: colors.navy, fontSize: 13 },
  segment: { flexDirection: 'row', backgroundColor: colors.white, borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, padding: 3 },
  segBtn: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 7, paddingHorizontal: 14, borderRadius: radius.pill },
  segActive: { backgroundColor: colors.navy },
  italic: { fontStyle: 'italic' },
  flex1: { flex: 1 },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' },
  predBanner: { backgroundColor: '#FFF3C4', borderRadius: radius.sm, paddingVertical: 6, paddingHorizontal: 10 },
  predText: { color: '#8A6D1A', fontFamily: 'PlusJakartaSans_700Bold' },
  dateTable: { flexDirection: 'row', gap: space.lg, marginTop: 4 },
  dRow: { flexDirection: 'row', gap: space.sm, paddingVertical: 3, alignItems: 'flex-start' },
  dDate: { width: 58, color: colors.ink },
  cardFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: space.sm, marginTop: space.xs, flexWrap: 'wrap' },
  monthsRow: { gap: space.md, paddingVertical: 4 },
  monthCol: { width: 190, gap: space.sm },
  monthHead: { fontFamily: 'PlusJakartaSans_700Bold', color: colors.muted },
  evCard: { backgroundColor: colors.white, borderWidth: 1, borderColor: colors.hairline, borderLeftWidth: 5, borderRadius: radius.sm, padding: space.sm, gap: 2 },
  evTop: { flexDirection: 'row', gap: space.sm, alignItems: 'center' },
  evLabel: { color: colors.inkSoft },
  evType: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 8, color: colors.muted, letterSpacing: 0.5 },
});
