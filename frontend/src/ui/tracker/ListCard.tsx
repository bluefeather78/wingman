import { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import type { Bucket } from '@/lib/constants';
import { buildMetaPills } from '@/lib/opportunityPills';
import {
  BUCKET_LABELS, computeProgressStatus, cycleYearShift, daysUntil, formatMonthDay,
  getDisplayMilestones, hasProjectedDates, type Milestone,
} from '@/lib/status';
import type { TrackerItem } from '@/api/trackerStore';
import { IconBtn, MiniBadge, ReviewBadge, StatusPill, usePopInteraction } from '@/ui/components';
import { StarIcon, XIcon } from '@/ui/icons';
import { colors, fonts, popShadow, radius } from '@/ui/theme';

// One tracked opportunity's card in the Quest Log's List view.
//
// Phase 5, frontend_report §4 ("Files over 800 lines — suggested splits"), which names
// `tracker/ListCard.tsx` for exactly this. See CalendarCard.tsx for why moving the shared
// StyleSheet with it is safe: the split is checked by the compiler in both directions.

export function ListCard({
  item,
  bucket,
  isSaved,
  isNew,
  onRemove,
  onToggleSaved,
  highlighted,
  cardRef,
  reviewOpen,
  onToggleReview,
}: {
  item: TrackerItem;
  bucket: Bucket;
  isSaved: boolean;
  isNew?: boolean;
  onRemove: (id: string) => void;
  onToggleSaved: (id: string) => void;
  highlighted?: boolean;
  cardRef?: (el: unknown) => void;
  reviewOpen?: boolean;
  onToggleReview?: (id: string) => void;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const cardPop = usePopInteraction(4, colors.navy, 2);
  const applyPop = usePopInteraction(3, colors.navy, 1);
  const milestones = getDisplayMilestones(item);
  const allPast = milestones.length > 0 && milestones.every((m) => m.isPast);
  // Dates rolled forward to the next annual cycle rather than read off the page.
  const projected = milestones.some((m) => m.projected);
  const progress = computeProgressStatus(item);
  const notRunning = item.status === 'not_running';
  // Rolling / always-open: no cycle, no dates — the deadline checker's "apply anytime"
  // answer (G3). It reads as Happening Now via computeProgressStatus; the badge and note
  // below make the "no dates is correct here" explicit so an empty card doesn't look broken.
  const rolling = item.status === 'rolling';
  const metaPills = buildMetaPills({ price: item.price, format: item.format, state: item.state, season: item.season });

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
            <Text style={styles.dateRowLabel}>
              {e.m.label}
              {/* Per-date, so a confirmed deadline and a projected opening on the SAME card
                  are told apart. The card-level "Predicted dates from past cycle" banner
                  can only say that something here is a guess. Suppressed when the label
                  already says it — some rows were written before the flag existed and the
                  model put "(estimated)" in the label text itself. */}
              {e.m.estimated && !/estimat/i.test(e.m.label) && (
                <Text style={styles.dateRowEstimated}>{'  (estimated)'}</Text>
              )}
              {/* P7 verified marker — shown only on verified === true (P6c found this exact
                  date on a page it fetched; absent = unknown, never rendered as proof). Green
                  matches the official-tier task chip: one visual language for "we checked
                  this against the source" (T4). Tapping opens the evidence page when the
                  check recorded one. Mutually exclusive with (estimated) by design — an
                  estimated date is verified:false. */}
              {e.m.verified === true && !e.m.estimated && (
                <Text
                  style={styles.dateRowVerified}
                  onPress={e.m.sourceUrl ? () => Linking.openURL(e.m.sourceUrl as string) : undefined}
                >
                  {e.m.sourceUrl ? '  ✓ verified ↗' : '  ✓ verified'}
                </Text>
              )}
            </Text>
          </View>
        ),
      )}
    </View>
  );

  return (
    <Pressable
      ref={cardRef as never}
      {...cardPop.handlers}
      // reviewOpen raises this card above the ones after it in source order, or the popover is
      // painted over by the next card instead of overlapping it.
      style={[styles.listCard, cardPop.shadowStyle, notRunning && { opacity: 0.6 }, highlighted && styles.listCardHighlighted, reviewOpen && styles.listCardReviewOpen]}
    >
      {isNew && (
        <View style={styles.newMarker}>
          <Text style={styles.newMarkerText}>New</Text>
        </View>
      )}
      <View style={styles.cardTop}>
        <View style={styles.badgeRow}>
          <MiniBadge label={BUCKET_LABELS[bucket]} bg={colors.violet200} fg={colors.violet900} />
          {notRunning && <MiniBadge label="Not running" bg="#FFE4E6" fg="#881337" />}
          {rolling && <MiniBadge label="Open now" bg="#DCFCE7" fg="#166534" />}
          <ReviewBadge
            status={item.reviewStatus}
            summary={item.reviewSummary}
            open={!!reviewOpen}
            onToggle={() => onToggleReview?.(item.id)}
          />
        </View>
        <View style={styles.iconRow}>
          {/* The label carries the item NAME and the CURRENT state: on a list of a dozen
              cards, "Save for later" a dozen times says nothing about which one, and a
              star that is already filled needs to announce that it will un-save. */}
          <IconBtn
            onPress={() => onToggleSaved(item.id)}
            label={isSaved ? `Remove ${item.name} from saved for later` : `Save ${item.name} for later`}
          >
            <StarIcon size={15} color={isSaved ? colors.orange : colors.navy} filled={isSaved} />
          </IconBtn>
          <IconBtn onPress={() => onRemove(item.id)} label={`Remove ${item.name} from your Quest Log`}>
            <XIcon size={14} color={colors.slate400} />
          </IconBtn>
        </View>
      </View>

      <View>
        <Pressable onPress={() => item.url && Linking.openURL(item.url)}>
          <Text style={styles.cardName}>{item.name}</Text>
        </Pressable>
        {!!item.org && <Text style={styles.cardOrg} numberOfLines={1}>{item.org}</Text>}
        {/* Meta pills, matching Fresh Finds (buildMetaPills): cost / format / season /
            location-if-in-person. Items added before these structured fields existed carry
            only the free-text `meta` line, so fall back to it when there are no pills. */}
        {metaPills.length > 0 ? (
          <View style={styles.metaRow}>
            {metaPills.map((p, i) => (
              <View key={i} style={styles.metaPill}>
                <Text style={styles.metaPillText}>{p}</Text>
              </View>
            ))}
          </View>
        ) : (
          !!item.meta && <Text style={styles.cardMeta} numberOfLines={1}>{item.meta}</Text>
        )}
      </View>

      {(item.wasEstimated || projected) && !notRunning && (
        <View style={styles.estimatedNote}>
          <Text style={styles.estimatedText}>Predicted dates from past cycle.</Text>
        </View>
      )}
      {/* Only the "this program is over" case is worth a banner. The running-but-past-dates
          note said nothing the dates themselves don't and appeared on most cards. */}
      {allPast && item.status !== 'running' && (
        <View style={[styles.estimatedNote, styles.staleBad]}>
          <Text style={[styles.estimatedText, styles.staleBadText]}>
            ⚠ No upcoming dates — this program's last cycle has ended.
          </Text>
        </View>
      )}

      {rolling && (
        <View style={[styles.estimatedNote, styles.rollingNote]}>
          <Text style={[styles.estimatedText, styles.rollingText]}>
            Open now — rolling admission, apply anytime.
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
            {...applyPop.handlers}
            style={[styles.applyBtn, applyPop.shadowStyle]}
          >
            <Text style={styles.applyText}>{item.applyLabel || 'Apply'}</Text>
          </Pressable>
        )}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  // Also on the screen's own sheet — generic, used on both sides, so each file declares the
  // one it uses rather than exporting a shared fragment that neither owns.
  flex1: { flex: 1 },

  listCard: { backgroundColor: colors.white, borderWidth: 4, borderColor: colors.slate900, borderRadius: radius.xxl, padding: 24, gap: 16 },
  listCardReviewOpen: { zIndex: 20 },
  listCardHighlighted: { backgroundColor: colors.lavender, borderColor: colors.indigo },
  // The retired SPA's newBanner, restored value-for-value (script.js trackerCardHTML): a
  // lime tab notched over the card's top-left corner, not a badge in the flow. The negative
  // offsets put it OUTSIDE the 4px border, which is what makes it read as a marker ON the
  // card rather than content IN it — so listCard must never gain overflow: 'hidden'.
  newMarker: {
    position: 'absolute', left: -8, top: -8, zIndex: 10,
    backgroundColor: colors.lime, borderRadius: 8,
    borderWidth: 2, borderColor: colors.navy,
    paddingHorizontal: 10, paddingVertical: 3,
    ...popShadow(2),
  },
  newMarkerText: { fontFamily: fonts.bodyBold, fontSize: 10, letterSpacing: 0.5, textTransform: 'uppercase', color: colors.ink },
  // zIndex on both rows is what lets ReviewBadge's popover paint OVER the card's dates and
  // meta below it rather than under them — RN-web makes every View its own stacking context
  // at z-index 0, so the popover cannot escape this row on its own. See the STACKING note on
  // ReviewBadge in src/ui/components.tsx. Kept at 1, well clear of topRow's 50.
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, zIndex: 1 },
  badgeRow: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', flex: 1, zIndex: 1 },
  iconRow: { flexDirection: 'row', gap: 6 },
  cardName: { fontFamily: fonts.display, fontSize: 30, lineHeight: 34, color: colors.slate900 },
  // The organization name, sat directly under the opportunity name in a smaller grey — the
  // same treatment Fresh Finds gives it (resultOrg).
  cardOrg: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500, marginTop: 4 },
  cardMeta: { fontFamily: fonts.bodyMed, fontSize: 14, color: colors.slate500, marginTop: 2 },
  // Meta pills — kept byte-identical to Fresh Finds' metaRow/metaPill so the two cards match.
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  metaPill: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.indigo200, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  metaPillText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate900 },
  estimatedNote: { backgroundColor: '#FEF08A', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.md, paddingHorizontal: 16, paddingVertical: 10 },
  estimatedText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#92400E' },
  staleBad: { backgroundColor: '#FFE4E6' },
  staleBadText: { color: '#9F1239' },
  rollingNote: { backgroundColor: '#DCFCE7' },
  rollingText: { color: '#166534' },
  dateCols: { flexDirection: 'row', gap: 24 },
  yearTag: { backgroundColor: '#EEE9DD', borderRadius: 6, paddingHorizontal: 8, paddingVertical: 3, alignSelf: 'flex-start', marginTop: 10, marginBottom: 6 },
  yearTagText: { fontFamily: fonts.bodyXBold, fontSize: 10, color: '#0F1C33', letterSpacing: 0.3 },
  dateRow: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: '#EEEEEE' },
  dateRowDate: { fontFamily: fonts.bodyBold, fontSize: 14, color: '#0F1C33', width: 52 },
  dateRowLabel: { fontFamily: fonts.bodyMed, fontSize: 14, color: '#33404F', flex: 1 },
  dateRowEstimated: { fontFamily: fonts.bodyMed, fontSize: 12, color: '#92400E' },
  // Same green as the official-tier source chip — the shared "checked against the source"
  // colour (T4).
  dateRowVerified: { fontFamily: fonts.bodyBold, fontSize: 11, color: '#166534' },
  detailsToggle: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.indigo600 },
  detailsBox: { backgroundColor: colors.slate50, borderWidth: 1, borderColor: colors.slate200, borderRadius: radius.md, padding: 12, gap: 4 },
  detailsText: { fontFamily: fonts.bodyMed, fontSize: 12, color: colors.slate500 },
  detailsNote: { fontFamily: fonts.bodyMed, fontSize: 10, color: colors.slate500, fontStyle: 'italic' },
  cardFoot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, paddingTop: 12, borderTopWidth: 2, borderTopColor: colors.slate100, flexWrap: 'wrap' },
  applyBtn: { backgroundColor: '#F97316', borderWidth: 2, borderColor: colors.slate900, borderRadius: radius.pill, paddingHorizontal: 20, paddingVertical: 10 },
  applyText: { fontFamily: fonts.bodyXBold, fontSize: 12, color: colors.slate900 },
});
