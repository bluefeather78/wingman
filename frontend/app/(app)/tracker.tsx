import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from 'expo-router';
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
import { Badge, PopButton, PopCard, Screen, Txt } from '@/ui/components';
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
  const [items, setItems] = useState<TrackerItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'list' | 'calendar'>('list');
  const [checking, setChecking] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      loadTrackerItems()
        .then((rows) => alive && setItems(rows))
        .catch((e) => alive && setError((e as Error).message));
      return () => {
        alive = false;
      };
    }, []),
  );

  async function checkDeadline(item: TrackerItem) {
    setChecking(item.oppId);
    const info = await httpClient.getDeadlineCheck(item.oppId);
    const dates: TrackedDate[] = (info?.important_dates ?? []).map((d) => ({
      label: d.label,
      dateISO: d.date_iso,
      type: d.type,
    }));
    const next = await updateTrackerItem(item.oppId, {
      status: info?.status ?? 'unknown',
      dates,
      checkedAt: new Date().toISOString(),
    });
    setItems(next);
    setChecking(null);
  }

  async function remove(oppId: string) {
    setItems(await removeTrackerItem(oppId));
  }

  if (error) return <Empty text={`Couldn't load your tracker: ${error}`} />;
  if (!items) return <Empty text="Loading…" />;
  if (!items.length) {
    return <Empty text="Nothing tracked yet. Add matches from the Finder and they'll show up here." />;
  }

  return (
    <Screen>
      <View style={styles.head}>
        <Txt variant="label">TRACKER</Txt>
        <Txt variant="hero">What you're chasing</Txt>
      </View>

      <Segmented value={view} onChange={setView} />

      {view === 'list' ? (
        <ListView items={items} checking={checking} onCheck={checkDeadline} onRemove={remove} />
      ) : (
        <CalendarView items={items} />
      )}
    </Screen>
  );
}

// ---------- List view ----------
function ListView({
  items,
  checking,
  onCheck,
  onRemove,
}: {
  items: TrackerItem[];
  checking: string | null;
  onCheck: (i: TrackerItem) => void;
  onRemove: (id: string) => void;
}) {
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
              <PopCard key={item.oppId} style={{ gap: space.sm }}>
                <Txt variant="h3">{item.name}</Txt>
                {!!item.org && <Txt variant="small">{item.org}</Txt>}
                {!!item.reason && (
                  <Txt variant="bodyStrong" style={{ color: colors.navy }}>
                    “{item.reason}”
                  </Txt>
                )}

                {item.status ? (
                  <StatusBadge status={item.status} />
                ) : null}
                {(item.dates ?? []).map((d, i) => (
                  <View key={i} style={styles.dateRow}>
                    <Ionicons name="calendar-outline" size={14} color={colors.navy} />
                    <Txt variant="small" style={{ color: colors.inkSoft }}>
                      {d.label}: {formatDate(d.dateISO)}
                    </Txt>
                  </View>
                ))}
                {item.checkedAt && !(item.dates ?? []).length && (
                  <Txt variant="small">No dated info found for this one.</Txt>
                )}

                <View style={styles.actions}>
                  {!!item.url && (
                    <PopButton label="Open" variant="secondary" onPress={() => Linking.openURL(item.url as string)} />
                  )}
                  <PopButton
                    label={checking === item.oppId ? 'Checking…' : item.checkedAt ? 'Recheck dates' : 'Check deadlines'}
                    variant="purple"
                    loading={checking === item.oppId}
                    onPress={() => onCheck(item)}
                  />
                  <PopButton label="Remove" variant="danger" onPress={() => onRemove(item.oppId)} />
                </View>
              </PopCard>
            ))}
          </View>
        );
      })}
    </View>
  );
}

// ---------- Calendar view ----------
function CalendarView({ items }: { items: TrackerItem[] }) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());

  // Aggregate every tracked date into a map keyed by YYYY-MM-DD.
  const byDay = useMemo(() => {
    const m = new Map<string, { label: string; name: string }[]>();
    items.forEach((it) =>
      (it.dates ?? []).forEach((d) => {
        const list = m.get(d.dateISO) ?? [];
        list.push({ label: d.label, name: it.name });
        m.set(d.dateISO, list);
      }),
    );
    return m;
  }, [items]);

  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array<null>(firstWeekday).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];

  const iso = (day: number) => `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const monthEvents = Array.from(byDay.entries())
    .filter(([k]) => k.startsWith(`${year}-${String(month + 1).padStart(2, '0')}`))
    .sort(([a], [b]) => a.localeCompare(b));

  function shift(delta: number) {
    const d = new Date(year, month + delta, 1);
    setYear(d.getFullYear());
    setMonth(d.getMonth());
  }

  const hasAnyDates = items.some((i) => (i.dates ?? []).length);

  return (
    <View style={{ gap: space.lg }}>
      <PopCard style={{ gap: space.md }}>
        <View style={styles.calHead}>
          <Pressable onPress={() => shift(-1)} hitSlop={10}>
            <Ionicons name="chevron-back" size={22} color={colors.navy} />
          </Pressable>
          <Txt variant="h2">
            {MONTHS[month]} {year}
          </Txt>
          <Pressable onPress={() => shift(1)} hitSlop={10}>
            <Ionicons name="chevron-forward" size={22} color={colors.navy} />
          </Pressable>
        </View>
        <View style={styles.weekRow}>
          {WEEKDAYS.map((w, i) => (
            <Txt key={i} variant="small" style={styles.weekday}>
              {w}
            </Txt>
          ))}
        </View>
        <View style={styles.calGrid}>
          {cells.map((day, i) => {
            const marked = day != null && byDay.has(iso(day));
            const isToday =
              day === now.getDate() && month === now.getMonth() && year === now.getFullYear();
            return (
              <View key={i} style={styles.dayCell}>
                {day != null && (
                  <View style={[styles.dayInner, isToday && styles.dayToday]}>
                    <Txt variant="small" style={[styles.dayNum, isToday && styles.dayTodayNum]}>
                      {day}
                    </Txt>
                    {marked && <View style={styles.dot} />}
                  </View>
                )}
              </View>
            );
          })}
        </View>
      </PopCard>

      {!hasAnyDates && (
        <Txt variant="body">
          No dates yet. Run “Check deadlines” on your tracked items (in List view) to fill in the calendar.
        </Txt>
      )}

      {monthEvents.map(([dateISO, evs]) => (
        <PopCard key={dateISO} color={colors.page} offset={3} style={{ gap: 4 }}>
          <Txt variant="bodyStrong">{formatDate(dateISO)}</Txt>
          {evs.map((e, i) => (
            <Txt key={i} variant="small" style={{ color: colors.inkSoft }}>
              {e.label} — {e.name}
            </Txt>
          ))}
        </PopCard>
      ))}
    </View>
  );
}

// ---------- bits ----------
function Segmented({ value, onChange }: { value: 'list' | 'calendar'; onChange: (v: 'list' | 'calendar') => void }) {
  return (
    <View style={styles.segment}>
      {(['list', 'calendar'] as const).map((v) => (
        <Pressable
          key={v}
          onPress={() => onChange(v)}
          style={[styles.segBtn, value === v && styles.segBtnActive]}
        >
          <Txt variant="bodyStrong" style={{ color: value === v ? colors.white : colors.navy }}>
            {v === 'list' ? 'List' : 'Calendar'}
          </Txt>
        </Pressable>
      ))}
    </View>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'running') return <Badge label="RUNNING" bg={colors.greenSoft} fg={colors.green} />;
  if (status === 'not_running') return <Badge label="NOT RUNNING" bg="#FBE0E0" fg={colors.red} />;
  return <Badge label="STATUS UNKNOWN" bg={colors.page} fg={colors.muted} />;
}

function Empty({ text }: { text: string }) {
  return (
    <Screen>
      <View style={styles.head}>
        <Txt variant="label">TRACKER</Txt>
        <Txt variant="hero">What you're chasing</Txt>
      </View>
      <PopCard>
        <Txt variant="body">{text}</Txt>
      </PopCard>
    </Screen>
  );
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return `${MONTHS[m - 1].slice(0, 3)} ${d}, ${y}`;
}

const styles = StyleSheet.create({
  head: { gap: space.xs, marginBottom: space.xs },
  segment: { flexDirection: 'row', backgroundColor: colors.white, borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, padding: 3 },
  segBtn: { flex: 1, alignItems: 'center', paddingVertical: 8, borderRadius: radius.pill },
  segBtnActive: { backgroundColor: colors.navy },
  actions: { flexDirection: 'row', gap: space.sm, marginTop: space.xs, flexWrap: 'wrap' },
  dateRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  calHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  weekRow: { flexDirection: 'row' },
  weekday: { flex: 1, textAlign: 'center', fontFamily: 'PlusJakartaSans_700Bold', color: colors.muted },
  calGrid: { flexDirection: 'row', flexWrap: 'wrap' },
  dayCell: { width: `${100 / 7}%`, aspectRatio: 1, padding: 2 },
  dayInner: { flex: 1, alignItems: 'center', justifyContent: 'center', borderRadius: radius.sm, gap: 2 },
  dayToday: { backgroundColor: colors.lime, borderWidth: 2, borderColor: colors.navy },
  dayNum: { color: colors.ink, fontFamily: 'PlusJakartaSans_500Medium' },
  dayTodayNum: { fontFamily: 'PlusJakartaSans_700Bold' },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.orange },
});
