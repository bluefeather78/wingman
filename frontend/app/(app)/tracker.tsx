import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import { httpClient } from '@/api/httpClient';
import {
  loadTrackerItems,
  removeTrackerItem,
  updateTrackerItem,
  type TrackedDate,
  type TrackerItem,
} from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import { Badge, PopButton, Screen, SoftCard, Txt } from '@/ui/components';
import { colors, radius, space } from '@/ui/theme';

const BUCKET_LABELS: Record<Bucket, string> = {
  summerPrograms: 'Summer Programs',
  internships: 'Internships',
  researchCompetitions: 'Research & Project Competitions',
  pureCompetitions: 'Academic Competitions',
  conferences: 'Conferences',
  journals: 'Journals',
};
const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export default function Tracker() {
  const router = useRouter();
  const [items, setItems] = useState<TrackerItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'calendar' | 'list'>('list');
  const [checking, setChecking] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      loadTrackerItems().then((r) => alive && setItems(r)).catch((e) => alive && setError((e as Error).message));
      return () => {
        alive = false;
      };
    }, []),
  );

  async function checkDeadline(item: TrackerItem) {
    setChecking(item.oppId);
    const info = await httpClient.getDeadlineCheck(item.oppId);
    const dates: TrackedDate[] = (info?.important_dates ?? []).map((d) => ({ label: d.label, dateISO: d.date_iso, type: d.type }));
    setItems(await updateTrackerItem(item.oppId, { status: info?.status ?? 'unknown', dates, checkedAt: new Date().toISOString() }));
    setChecking(null);
  }
  async function remove(oppId: string) {
    setItems(await removeTrackerItem(oppId));
  }

  const count = items?.length ?? 0;

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
      ) : !items ? (
        <SoftCard><Txt variant="body">Loading…</Txt></SoftCard>
      ) : count === 0 ? (
        <SoftCard>
          <Txt variant="body" style={styles.italic}>
            Nothing on the calendar yet — add opportunities via the Finder or the button above.
          </Txt>
        </SoftCard>
      ) : view === 'list' ? (
        <ListView items={items} checking={checking} onCheck={checkDeadline} onRemove={remove} />
      ) : (
        <CalendarView items={items} />
      )}
    </Screen>
  );
}

function ListView({ items, checking, onCheck, onRemove }: { items: TrackerItem[]; checking: string | null; onCheck: (i: TrackerItem) => void; onRemove: (id: string) => void }) {
  return (
    <View style={{ gap: space.xl }}>
      {ALL_BUCKETS.map((bucket) => {
        const rows = items.filter((i) => i.bucket === bucket);
        if (!rows.length) return null;
        return (
          <View key={bucket} style={{ gap: space.md }}>
            <Txt variant="h3">
              {BUCKET_LABELS[bucket]} · {rows.length}
            </Txt>
            {rows.map((item) => (
              <SoftCard key={item.oppId} style={{ gap: space.sm }}>
                <Txt variant="h3">{item.name}</Txt>
                {!!item.org && <Txt variant="small">{item.org}</Txt>}
                {!!item.reason && <Txt variant="bodyStrong" style={{ color: colors.navy }}>“{item.reason}”</Txt>}
                {!!item.status && <StatusBadge status={item.status} />}
                {(item.dates ?? []).map((d, i) => (
                  <View key={i} style={styles.dateRow}>
                    <Ionicons name="calendar-outline" size={14} color={colors.navy} />
                    <Txt variant="small" style={{ color: colors.inkSoft }}>
                      {d.label}: {formatDate(d.dateISO)}
                    </Txt>
                  </View>
                ))}
                {item.checkedAt && !(item.dates ?? []).length && <Txt variant="small">No dated info found for this one.</Txt>}
                <View style={styles.actions}>
                  {!!item.url && <PopButton label="Open" variant="secondary" small onPress={() => Linking.openURL(item.url as string)} />}
                  <PopButton label={checking === item.oppId ? 'Checking…' : item.checkedAt ? 'Recheck dates' : 'Check deadlines'} small loading={checking === item.oppId} onPress={() => onCheck(item)} />
                  <PopButton label="Remove" variant="danger" small onPress={() => onRemove(item.oppId)} />
                </View>
              </SoftCard>
            ))}
          </View>
        );
      })}
    </View>
  );
}

function CalendarView({ items }: { items: TrackerItem[] }) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());

  const byDay = useMemo(() => {
    const m = new Map<string, { label: string; name: string }[]>();
    items.forEach((it) => (it.dates ?? []).forEach((d) => {
      const list = m.get(d.dateISO) ?? [];
      list.push({ label: d.label, name: it.name });
      m.set(d.dateISO, list);
    }));
    return m;
  }, [items]);

  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [...Array<null>(firstWeekday).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  const iso = (day: number) => `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const monthPrefix = `${year}-${String(month + 1).padStart(2, '0')}`;
  const monthEvents = Array.from(byDay.entries()).filter(([k]) => k.startsWith(monthPrefix)).sort(([a], [b]) => a.localeCompare(b));
  const hasAny = items.some((i) => (i.dates ?? []).length);

  function shift(delta: number) {
    const d = new Date(year, month + delta, 1);
    setYear(d.getFullYear());
    setMonth(d.getMonth());
  }

  return (
    <View style={{ gap: space.lg }}>
      <SoftCard style={{ gap: space.md }}>
        <View style={styles.calHead}>
          <Pressable onPress={() => shift(-1)} hitSlop={10}><Ionicons name="chevron-back" size={22} color={colors.navy} /></Pressable>
          <Txt variant="h2">{MONTHS[month]} {year}</Txt>
          <Pressable onPress={() => shift(1)} hitSlop={10}><Ionicons name="chevron-forward" size={22} color={colors.navy} /></Pressable>
        </View>
        <View style={styles.weekRow}>
          {WEEKDAYS.map((w, i) => <Txt key={i} variant="small" style={styles.weekday}>{w}</Txt>)}
        </View>
        <View style={styles.calGrid}>
          {cells.map((day, i) => {
            const marked = day != null && byDay.has(iso(day));
            const isToday = day === now.getDate() && month === now.getMonth() && year === now.getFullYear();
            return (
              <View key={i} style={styles.dayCell}>
                {day != null && (
                  <View style={[styles.dayInner, isToday && styles.dayToday]}>
                    <Txt variant="small" style={[styles.dayNum, isToday && { color: colors.white }]}>{day}</Txt>
                    {marked && <View style={styles.dot} />}
                  </View>
                )}
              </View>
            );
          })}
        </View>
      </SoftCard>
      {!hasAny && <Txt variant="body">No dates yet. Run “Check deadlines” on your tracked items (List view) to fill the calendar.</Txt>}
      {monthEvents.map(([dateISO, evs]) => (
        <SoftCard key={dateISO} color={colors.lavender} style={{ gap: 4 }}>
          <Txt variant="bodyStrong">{formatDate(dateISO)}</Txt>
          {evs.map((e, i) => <Txt key={i} variant="small" style={{ color: colors.inkSoft }}>{e.label} — {e.name}</Txt>)}
        </SoftCard>
      ))}
    </View>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'running') return <Badge label="RUNNING" bg={colors.greenSoft} fg={colors.green} />;
  if (status === 'not_running') return <Badge label="NOT RUNNING" bg={colors.redSoft} fg={colors.red} />;
  return <Badge label="STATUS UNKNOWN" bg={colors.lavender} fg={colors.muted} />;
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS[m - 1].slice(0, 3)} ${d}, ${y}`;
}

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
  dateRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  actions: { flexDirection: 'row', gap: space.sm, marginTop: space.xs, flexWrap: 'wrap' },
  calHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  weekRow: { flexDirection: 'row' },
  weekday: { flex: 1, textAlign: 'center', fontFamily: 'PlusJakartaSans_700Bold', color: colors.muted },
  calGrid: { flexDirection: 'row', flexWrap: 'wrap' },
  dayCell: { width: `${100 / 7}%`, aspectRatio: 1, padding: 2 },
  dayInner: { flex: 1, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, gap: 2 },
  dayToday: { backgroundColor: colors.orange },
  dayNum: { color: colors.ink, fontFamily: 'PlusJakartaSans_500Medium' },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.orange },
});
