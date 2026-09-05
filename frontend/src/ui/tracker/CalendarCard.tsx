import { useMemo } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { ALL_BUCKETS, type Bucket } from '@/lib/constants';
import {
  BUCKET_LABELS, MONTH_NAMES, assignCalendarColors, getDisplayMilestones,
  type CalColor, type Milestone,
} from '@/lib/status';
import type { TrackerItem } from '@/api/trackerStore';
import { SoftCard } from '@/ui/components';
import { colors, fonts, popShadow, radius } from '@/ui/theme';

// The Quest Log's Calendar view — month lanes plus the always-open band.
//
// Phase 5, frontend_report §4 ("Files over 800 lines — suggested splits"), which names
// `tracker/CalendarCard.tsx` for exactly this. It was already a standalone component sharing
// one StyleSheet with the screen and the list card; the styles it owns came with it.
//
// The split is COMPILER-CHECKED in both directions, which is what makes it safe without a
// render test: StyleSheet.create returns a TYPED object, so a key moved out that the screen
// still uses, or left behind that this file needs, is a build error rather than a style that
// silently renders as nothing.

export function CalendarCard({
  entries,
  onEntryPress,
}: {
  entries: { item: TrackerItem; bucket: Bucket }[];
  onEntryPress: (id: string) => void;
}) {
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

  // P11: rolling / always-open programs. They carry NO dates by design (G3 — a genuinely
  // continuous program has no deadline to place on a month lane), so without this band the
  // Calendar view simply never shows them and a student scanning "what can I apply to right
  // now" misses every always-open program. The band lists them OUTSIDE the month lanes and
  // the date sort — deliberately no placeholder date is invented to force them onto a lane
  // ("never anchor a date"). Saved-for-later is already excluded upstream (entries), and
  // not_running has no business here. Dated programs whose window is currently open are NOT
  // duplicated into the band: they already sit on a month lane with their real dates.
  const openNow = useMemo(
    () => entries.filter(({ item }) => item.status === 'rolling'),
    [entries],
  );

  const colorMap = useMemo(() => {
    const ids: string[] = [];
    lanes.forEach((l) => l.rows.forEach((r) => r.milestones.forEach(() => ids.push(r.item.id))));
    return assignCalendarColors(ids);
  }, [lanes]);

  if (!lanes.length && !openNow.length) {
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
      {openNow.length > 0 && (
        <View style={styles.openNowBand}>
          <Text style={styles.openNowHead}>OPEN NOW — APPLY ANYTIME</Text>
          <View style={styles.openNowRow}>
            {openNow.map(({ item }) => (
              <Pressable key={item.id} onPress={() => onEntryPress(item.id)} style={styles.openNowPill}>
                <Text style={styles.openNowPillText} numberOfLines={1}>
                  {item.name.length > 32 ? item.name.slice(0, 30) + '…' : item.name}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}
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
                          <Pressable
                            key={i}
                            onPress={() => onEntryPress(e.venueId)}
                            style={[styles.entry, { backgroundColor: c.bg, borderLeftColor: c.border }, e.isPast && styles.entryPast]}
                          >
                            <Text style={[styles.entryDay, { color: c.text }]}>{e.day}</Text>
                            <View style={styles.flex1}>
                              <Text style={[styles.entryName, { color: c.text }]}>{e.label}</Text>
                              <Text style={[styles.entryText, { color: c.text }]}>
                                — {e.text} <Text style={[styles.entryType, { color: c.text }]}>{e.type.toUpperCase()}{e.isPast ? ' · PASSED' : ''}</Text>
                              </Text>
                            </View>
                          </Pressable>
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

const styles = StyleSheet.create({
  // `emptyState` and `flex1` also exist on the screen's own sheet — they are generic and used
  // on both sides, so each file declares the one it uses rather than exporting a shared
  // fragment that neither owns.
  emptyState: { color: '#94A3B8', fontStyle: 'italic', fontSize: 13, fontFamily: fonts.bodyMed },
  flex1: { flex: 1 },

  laneHead: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  // P11: the rolling-programs band. Same green family as the card's "Open now" badge and
  // the rolling note — one visual language for "open right now".
  openNowBand: { backgroundColor: '#DCFCE7', borderWidth: 2, borderColor: '#166534', borderRadius: radius.lg, padding: 12, gap: 8 },
  openNowHead: { fontFamily: fonts.bodyXBold, fontSize: 11, color: '#166534', letterSpacing: 0.55 },
  openNowRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  openNowPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: '#166534', borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 5, maxWidth: '100%' },
  openNowPillText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#166534' },
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
});
