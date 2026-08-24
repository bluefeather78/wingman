import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import {
  loadTrackerData,
  loadTrackerSaved,
  removeTrackerItem,
  saveTrackerSaved,
  type SavedState,
  type TrackerData,
  type TrackerItem,
} from '@/api/trackerStore';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import {
  assignCalendarColors,
  BUCKET_LABELS,
  computeProgressStatus,
  earliestUpcoming,
  formatMonthDay,
  getDisplayMilestones,
  MONTH_NAMES,
  type CalColor,
  type Milestone,
} from '@/lib/status';

// script.js sortedByTrackerDeadline: status group first (Happening Now, Future, Past),
// then soonest upcoming date within each group.
const STATUS_ORDER = { in_progress: 0, not_started: 1, completed: 2 } as const;
function sortEntries(entries: { item: TrackerItem; bucket: Bucket }[]) {
  const dateOf = (item: TrackerItem) => earliestUpcoming(item)?.date ?? '9999-12-31';
  return [...entries].sort((a, b) => {
    const s = STATUS_ORDER[computeProgressStatus(a.item)] - STATUS_ORDER[computeProgressStatus(b.item)];
    if (s !== 0) return s;
    return dateOf(a.item).localeCompare(dateOf(b.item));
  });
}
import { IconBtn, MiniBadge, PopButton, Screen, SoftCard, StatusPill, Txt } from '@/ui/components';
import { CalendarIcon, CalendarSyncIcon, ListIcon, RefreshIcon, StarIcon, XIcon } from '@/ui/icons';
import { colors, fonts, popShadow, radius, space } from '@/ui/theme';

// Quest Log — ported from the live app's #page-tracker: header controls (refresh status,
// calendar-sync + Add Opportunity), "Actively Tracked" + count + Calendar/List view-tabs,
// the swimlane month-card calendar, and the list view with pop cards + Saved for Later.
export default function Tracker() {
  const router = useRouter();
  const [data, setData] = useState<TrackerData | null>(null);
  const [saved, setSaved] = useState<SavedState>({});
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'calendar' | 'list'>('calendar');

  useFocusEffect(
    useCallback(() => {
      let alive = true;
      Promise.all([loadTrackerData(), loadTrackerSaved()])
        .then(([d, s]) => {
          if (!alive) return;
          setData(d);
          setSaved(s);
        })
        .catch((e) => alive && setError((e as Error).message));
      return () => {
        alive = false;
      };
    }, []),
  );

  async function remove(id: string) {
    setData(await removeTrackerItem(id));
  }
  async function toggleSaved(id: string) {
    const next = { ...saved, [id]: !saved[id] };
    setSaved(next);
    await saveTrackerSaved(next);
  }

  // Raw bucket order — the calendar's color assignment depends on first-appearance order
  // exactly like the old app's renderCalendarSwimlanes (which never sorts).
  const rawActiveItems = useMemo(() => {
    if (!data) return [] as { item: TrackerItem; bucket: Bucket }[];
    const out: { item: TrackerItem; bucket: Bucket }[] = [];
    ALL_BUCKETS.forEach((b) => data[b].forEach((item) => !saved[item.id] && out.push({ item, bucket: b })));
    return out;
  }, [data, saved]);
  const activeItems = useMemo(() => sortEntries(rawActiveItems), [rawActiveItems]);
  const savedItems = useMemo(() => {
    if (!data) return [] as { item: TrackerItem; bucket: Bucket }[];
    const out: { item: TrackerItem; bucket: Bucket }[] = [];
    ALL_BUCKETS.forEach((b) => data[b].forEach((item) => saved[item.id] && out.push({ item, bucket: b })));
    return sortEntries(out);
  }, [data, saved]);

  return (
    <Screen>
      {/* Header controls */}
      <View style={styles.topRow}>
        <View style={styles.topLeft}>
          <Text style={styles.lastChecked}>Last checked: never</Text>
          <IconBtn>
            <RefreshIcon size={14} color={colors.indigo600} />
          </IconBtn>
        </View>
        <View style={styles.topRight}>
          <IconBtn>
            <CalendarSyncIcon size={16} color={colors.navy} />
          </IconBtn>
          <PopButton label="Add Opportunity" onPress={() => router.push('/(app)/finder')} />
        </View>
      </View>

      {/* Actively Tracked + view toggle */}
      <View style={styles.headRow}>
        <View style={styles.titleWrap}>
          <Txt variant="h2" style={{ color: colors.ink }}>Actively Tracked</Txt>
          <View style={styles.countPill}>
            <Text style={styles.countText}>{String(activeItems.length).padStart(2, '0')}</Text>
          </View>
        </View>
        <View style={styles.viewTabs}>
          {(['calendar', 'list'] as const).map((v) => (
            <Pressable key={v} onPress={() => setView(v)} style={[styles.viewTab, view === v && styles.viewTabActive]}>
              {v === 'calendar' ? (
                <CalendarIcon size={16} color={view === v ? colors.white : '#5B6785'} />
              ) : (
                <ListIcon size={16} color={view === v ? colors.white : '#5B6785'} />
              )}
              <Text style={[styles.viewTabText, view === v && styles.viewTabTextActive]}>
                {v === 'calendar' ? 'Calendar' : 'List'}
              </Text>
            </Pressable>
          ))}
        </View>
      </View>

      {error ? (
        <SoftCard><Txt variant="body">Couldn't load your tracker: {error}</Txt></SoftCard>
      ) : !data ? (
        <SoftCard><Txt variant="body">Loading…</Txt></SoftCard>
      ) : view === 'calendar' ? (
        <CalendarCard entries={rawActiveItems} />
      ) : (
        <>
          <View style={{ gap: space.lg }}>
          {activeItems.length === 0 ? (
            <SoftCard><Text style={styles.emptyState}>Nothing tracked here yet — add opportunities via the Finder or the button above.</Text></SoftCard>
          ) : (
            activeItems.map(({ item, bucket }) => (
              <ListCard key={item.id} item={item} bucket={bucket} isSaved={false} onRemove={remove} onToggleSaved={toggleSaved} />
            ))
          )}
          </View>
          <View style={styles.savedHead}>
            <Txt variant="h2" style={{ color: colors.ink }}>Saved for Later</Txt>
            <View style={styles.countPill}>
              <Text style={styles.countText}>{String(savedItems.length).padStart(2, '0')}</Text>
            </View>
          </View>
          <View style={{ gap: space.lg }}>
          {savedItems.length === 0 ? (
            <Text style={styles.emptyState}>Nothing saved yet — click "☆ Save for later" on any card to move it here.</Text>
          ) : (
            savedItems.map(({ item, bucket }) => (
              <ListCard key={item.id} item={item} bucket={bucket} isSaved onRemove={remove} onToggleSaved={toggleSaved} />
            ))
          )}
          </View>
        </>
      )}
    </Screen>
  );
}

// ---------- Calendar (one card-soft holding a swimlane per opportunity type) ----------
function CalendarCard({ entries }: { entries: { item: TrackerItem; bucket: Bucket }[] }) {
  const lanes = useMemo(() => {
    const byBucket = new Map<Bucket, { item: TrackerItem; milestones: Milestone[] }[]>();
    entries.forEach(({ item, bucket }) => {
      if (item.status === 'not_running') return;
      const ms = getDisplayMilestones(item);
      if (!ms.length) return;
      const list = byBucket.get(bucket) ?? [];
      list.push({ item, milestones: ms });
      byBucket.set(bucket, list);
    });
    return ALL_BUCKETS.filter((b) => byBucket.has(b)).map((b) => ({ bucket: b, rows: byBucket.get(b)! }));
  }, [entries]);

  const colorMap = useMemo(() => {
    const ids: string[] = [];
    lanes.forEach((l) => l.rows.forEach((r) => r.milestones.forEach(() => ids.push(r.item.id))));
    return assignCalendarColors(ids);
  }, [lanes]);

  if (!lanes.length) {
    return (
      <SoftCard>
        <Text style={styles.emptyState}>Nothing on the calendar yet — add opportunities via the Finder or the button above.</Text>
      </SoftCard>
    );
  }

  const now = new Date();
  const currentYM = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const next = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  const nextYM = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`;

  return (
    <SoftCard style={{ gap: 20 }}>
      {lanes.map(({ bucket, rows }) => {
        const byMonth = new Map<string, { day: number; label: string; text: string; type: string; isPast: boolean; venueId: string }[]>();
        rows.forEach(({ item, milestones }) => {
          const shortLabel = item.name.length > 22 ? item.name.slice(0, 20) + '…' : item.name;
          milestones.forEach((m) => {
            const ym = m.date.slice(0, 7);
            const list = byMonth.get(ym) ?? [];
            list.push({ day: parseInt(m.date.slice(8, 10), 10), label: shortLabel, text: m.label, type: m.type, isPast: m.isPast, venueId: item.id });
            byMonth.set(ym, list);
          });
        });
        const months = Array.from(byMonth.keys()).sort();
        return (
          <View key={bucket} style={{ gap: 8 }}>
            <Text style={styles.laneHead}>{BUCKET_LABELS[bucket].toUpperCase()}</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator contentContainerStyle={styles.strip}>
              {months.map((ym) => {
                const isCurrent = ym === currentYM;
                const isNext = ym === nextYM;
                const [y, m] = ym.split('-');
                const evs = (byMonth.get(ym) ?? []).sort((a, b) => a.day - b.day);
                return (
                  <View
                    key={ym}
                    style={[
                      styles.monthCard,
                      isCurrent && [styles.monthCurrent, popShadow(3, colors.indigo)],
                      isNext && [styles.monthNext, popShadow(3, '#A5B4FC')],
                    ]}
                  >
                    <View style={styles.monthHead}>
                      <Text style={styles.monthHeadText}>{MONTH_NAMES[parseInt(m, 10) - 1]} {y}</Text>
                      {isCurrent && <View style={styles.nowBadge}><Text style={styles.nowBadgeText}>NOW</Text></View>}
                      {isNext && <View style={[styles.nowBadge, styles.nextBadge]}><Text style={[styles.nowBadgeText, { color: '#1E1B4B' }]}>NEXT</Text></View>}
                    </View>
                    <View style={{ gap: 6 }}>
                      {evs.map((e, i) => {
                        const c: CalColor = colorMap.get(e.venueId) ?? { bg: '#eee', border: '#999', text: '#333' };
                        return (
                          <View key={i} style={[styles.entry, { backgroundColor: c.bg, borderLeftColor: c.border }, e.isPast && styles.entryPast]}>
                            <Text style={[styles.entryDay, { color: c.text }]}>{e.day}</Text>
                            <View style={styles.flex1}>
                              <Text style={[styles.entryName, { color: c.text }]}>{e.label}</Text>
                              <Text style={[styles.entryText, { color: c.text }]}>
                                — {e.text} <Text style={[styles.entryType, { color: c.text }]}>{e.type.toUpperCase()}{e.isPast ? ' · PASSED' : ''}</Text>
                              </Text>
                            </View>
                          </View>
                        );
                      })}
                    </View>
                  </View>
                );
              })}
            </ScrollView>
          </View>
        );
      })}
    </SoftCard>
  );
}

// ---------- List card (trackerCardHTML) ----------
function ListCard({
  item,
  bucket,
  isSaved,
  onRemove,
  onToggleSaved,
}: {
  item: TrackerItem;
  bucket: Bucket;
  isSaved: boolean;
  onRemove: (id: string) => void;
  onToggleSaved: (id: string) => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const milestones = getDisplayMilestones(item);
  const allPast = milestones.length > 0 && milestones.every((m) => m.isPast);
  const progress = computeProgressStatus(item);
  const notRunning = item.status === 'not_running';

  // Group milestone rows by year, split into two balanced columns past 5 entries.
  const entries: ({ kind: 'tag'; year: string; cont?: boolean } | { kind: 'date'; m: Milestone })[] = [];
  const byYear = new Map<string, Milestone[]>();
  milestones.forEach((m) => {
    const y = m.date.slice(0, 4);
    const list = byYear.get(y) ?? [];
    list.push(m);
    byYear.set(y, list);
  });
  Array.from(byYear.keys()).sort().forEach((y) => {
    entries.push({ kind: 'tag', year: y });
    byYear.get(y)!.forEach((m) => entries.push({ kind: 'date', m }));
  });
  let col1 = entries;
  let col2: typeof entries = [];
  if (entries.length > 5) {
    const size = Math.ceil(entries.length / 2);
    col1 = entries.slice(0, size);
    col2 = entries.slice(size);
    if (col2.length && col2[0].kind !== 'tag') {
      let lastYear: string | null = null;
      for (let i = size - 1; i >= 0; i--) {
        const e = entries[i];
        if (e.kind === 'tag') { lastYear = e.year; break; }
      }
      if (lastYear) col2 = [{ kind: 'tag', year: lastYear, cont: true }, ...col2];
    }
  }

  const renderCol = (col: typeof entries) => (
    <View style={styles.flex1}>
      {col.map((e, i) =>
        e.kind === 'tag' ? (
          <View key={i} style={styles.yearTag}><Text style={styles.yearTagText}>{e.year}{e.cont ? ' (cont.)' : ''}</Text></View>
        ) : (
          <View key={i} style={styles.dateRow}>
            <Text style={styles.dateRowDate}>{formatMonthDay(e.m.date)}</Text>
            <Text style={styles.dateRowLabel}>{e.m.label}</Text>
          </View>
        ),
      )}
    </View>
  );

  return (
    <View style={[styles.listCard, popShadow(4), notRunning && { opacity: 0.6 }]}>
      <View style={styles.cardTop}>
        <View style={styles.badgeRow}>
          <MiniBadge label={BUCKET_LABELS[bucket]} bg={colors.violet200} fg={colors.violet900} />
          {notRunning && <MiniBadge label="Not running" bg="#FFE4E6" fg="#881337" />}
          {item.reviewStatus === 'positive' && <MiniBadge label="Well reviewed" bg={colors.emerald100} fg={colors.emerald900} />}
          {item.reviewStatus === 'mixed' && <MiniBadge label="Mixed reviews" bg="#FFEDD5" fg="#7C2D12" />}
        </View>
        <View style={styles.iconRow}>
          <IconBtn onPress={() => onToggleSaved(item.id)}>
            <StarIcon size={15} color={isSaved ? colors.orange : colors.navy} filled={isSaved} />
          </IconBtn>
          <IconBtn onPress={() => onRemove(item.id)}>
            <XIcon size={14} color={colors.slate400} />
          </IconBtn>
        </View>
      </View>

      <View>
        <Pressable onPress={() => item.url && Linking.openURL(item.url)}>
          <Text style={styles.cardName}>{item.name}</Text>
        </Pressable>
        {!!item.meta && <Text style={styles.cardMeta} numberOfLines={1}>{item.meta}</Text>}
      </View>

      {item.wasEstimated && !notRunning && (
        <View style={styles.estimatedNote}>
          <Text style={styles.estimatedText}>Predicted dates from past cycle.</Text>
        </View>
      )}
      {allPast && (
        <View style={[styles.estimatedNote, item.status !== 'running' && styles.staleBad]}>
          <Text style={[styles.estimatedText, item.status !== 'running' && styles.staleBadText]}>
            {item.status === 'running'
              ? '📅 These dates are from the last cycle. Check the program site for next cycle dates.'
              : "⚠ No upcoming dates — this program's last cycle has ended."}
          </Text>
        </View>
      )}

      {milestones.length > 0 && (
        <View style={col2.length ? styles.dateCols : undefined}>
          {renderCol(col1)}
          {col2.length > 0 && renderCol(col2)}
        </View>
      )}

      <Pressable onPress={() => setShowDetails(!showDetails)}>
        <Text style={styles.detailsToggle}>▶ Show details</Text>
      </Pressable>
      {showDetails && (
        <View style={styles.detailsBox}>
          {!!item.fit && <Text style={styles.detailsText}>{item.fit}</Text>}
          {!!item.note && <Text style={styles.detailsNote}>{item.note}</Text>}
        </View>
      )}

      <View style={styles.cardFoot}>
        <StatusPill status={progress} />
        {!!(item.applyUrl || item.url) && (
          <Pressable
            onPress={() => Linking.openURL((item.applyUrl || item.url) as string)}
            style={[styles.applyBtn, popShadow(3)]}
          >
            <Text style={styles.applyText}>{item.applyLabel || 'Apply'}</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  topRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: space.lg, flexWrap: 'wrap' },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  lastChecked: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate400 },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },

  headRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' },
  titleWrap: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  countPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  countText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  viewTabs: { flexDirection: 'row', backgroundColor: colors.lavender, borderRadius: radius.pill, padding: 3, gap: 2 },
  viewTab: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 8, paddingHorizontal: 14, borderRadius: radius.pill },
  viewTabActive: { backgroundColor: colors.navy },
  viewTabText: { fontFamily: fonts.bodyXBold, fontSize: 13, lineHeight: 20, color: '#5B6785' },
  viewTabTextActive: { color: colors.white },

  emptyState: { color: '#94A3B8', fontStyle: 'italic', fontSize: 13, fontFamily: fonts.bodyMed },

  laneHead: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  strip: { gap: 16, paddingBottom: 8 },
  monthCard: { width: 200, backgroundColor: colors.slate50, borderWidth: 2, borderColor: '#CBD5E1', borderRadius: radius.lg, padding: 12 },
  monthCurrent: { borderWidth: 3, borderColor: colors.indigo, backgroundColor: '#EEF2FF' },
  monthNext: { borderWidth: 3, borderColor: '#A5B4FC', backgroundColor: '#F5F5FF' },
  monthHead: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 8 },
  monthHeadText: { fontFamily: fonts.bodyXBold, fontSize: 11, color: colors.slate500, letterSpacing: 0.55, textTransform: 'uppercase' },
  nowBadge: { backgroundColor: colors.indigo, borderRadius: radius.pill, paddingHorizontal: 6, paddingVertical: 2 },
  nextBadge: { backgroundColor: '#A5B4FC' },
  nowBadgeText: { fontFamily: fonts.bodyXBold, fontSize: 9, color: colors.white, letterSpacing: 0.45 },
  entry: { borderRadius: 8, paddingVertical: 6, paddingHorizontal: 8, borderLeftWidth: 3, flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  entryPast: { opacity: 0.5 },
  entryDay: { fontFamily: fonts.bodyXBold, fontSize: 18, lineHeight: 18 },
  entryText: { fontFamily: fonts.bodyMed, fontSize: 11, lineHeight: 15 },
  entryName: { fontFamily: fonts.bodyBold, fontSize: 11 },
  entryType: { fontFamily: fonts.bodyMed, fontSize: 9, opacity: 0.7, marginTop: 1 },
  flex1: { flex: 1 },

  savedHead: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 24 },

  listCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 },
  badgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', flex: 1 },
  iconRow: { flexDirection: 'row', gap: 6 },
  cardName: { fontFamily: fonts.display, fontSize: 30, lineHeight: 34, color: colors.slate900 },
  cardMeta: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500, marginTop: 4 },
  estimatedNote: { backgroundColor: '#FEF08A', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 10 },
  estimatedText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#92400E' },
  staleBad: { backgroundColor: '#FFE4E6' },
  staleBadText: { color: '#9F1239' },
  dateCols: { flexDirection: 'row', gap: 24 },
  yearTag: { backgroundColor: '#EEE9DD', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start', marginTop: 10, marginBottom: 6 },
  yearTagText: { fontFamily: fonts.bodyXBold, fontSize: 10, color: '#0F1C33', letterSpacing: 0.3 },
  dateRow: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: '#EEEEEE' },
  dateRowDate: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#0F1C33', width: 52 },
  dateRowLabel: { fontFamily: fonts.bodyMed, fontSize: 14, color: '#33404F', flex: 1 },
  detailsToggle: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600 },
  detailsBox: { backgroundColor: colors.slate50, borderWidth: 1, borderColor: colors.slate200, borderRadius: radius.md, padding: 12, gap: 4 },
  detailsText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500 },
  detailsNote: { fontFamily: fonts.bodyMed, fontSize: 10, color: colors.slate500, fontStyle: 'italic' },
  cardFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingTop: 12, borderTopWidth: 2, borderTopColor: colors.slate100, flexWrap: 'wrap' },
  applyBtn: { backgroundColor: '#F97316', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  applyText: { fontFamily: fonts.bodyXBold, fontSize: 12, color: colors.slate900 },
});
