// The polished Fresh Finds presentation, ported from the `opportunity-matching` branch's
// FreshFindsFlow (design mocks in design/matching-ux/) and re-typed to neutral props so it is
// driven by the BACKEND-orchestrated pipeline (POST /api/match) rather than the client-side
// funnel/curate logic it originally shipped with. These components hold no matching logic — they
// render what the container (finder.tsx) hands them and call back on every action.
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, View } from 'react-native';

import type { Opportunity } from '@/api/types';
import { Badge, MiniBadge, PopButton, PopCard, REVIEW_STATUS_META, RightDrawer, Screen, Txt } from '@/ui/components';
import { colors, fonts, radius, space, type } from '@/ui/theme';

export type Tier = 'strong' | 'look' | 'stretch';
// One curated card: the catalog row plus the server's verdict (why-it-fits + tier) and any
// eligibility caveats the server surfaced (empty for now — Phase D fills these).
export interface ShortlistItem {
  opp: Opportunity;
  reason: string;
  tier: Tier;
  flags?: string[];
}
export interface ReviewSection {
  title: string;
  items: { label: string; value: string }[];
}
export interface RungOption {
  label: string;
  value: string;
  count?: number | null;
}

const type_h1 = { fontFamily: fonts.display, fontSize: 24, lineHeight: 32, color: colors.navy };
const type_h2 = { fontFamily: fonts.display, fontSize: 20, lineHeight: 28, color: colors.navy };
const type_body = { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.inkSoft };

const NOT_INTERESTED_REASONS = ['Wrong subject', 'Too advanced', 'Not my thing', 'Doesn’t fit my location'];

const TIER_BADGE: Record<Tier, { label: string; bg: string; fg: string }> = {
  strong: { label: 'Strong Fit', bg: colors.yellow300, fg: colors.ink },
  look: { label: 'Worth a look', bg: colors.slate100, fg: colors.slate500 },
  stretch: { label: 'Stretch pick', bg: colors.mint, fg: colors.emerald900 },
};

// ---------- states ----------
export function FunnelLoading({ label = 'Finding your matches…' }: { label?: string }) {
  return (
    <Screen>
      <View style={{ paddingVertical: 80, alignItems: 'center', gap: space.lg }}>
        <ActivitySpinner />
        <Txt style={type_h2}>{label}</Txt>
      </View>
    </Screen>
  );
}
export function MatchError({ message, onRetry }: { message?: string | null; onRetry: () => void }) {
  return (
    <Screen>
      <PopCard style={{ marginTop: space.xl }}>
        <Txt style={type_h2}>That didn’t load</Txt>
        <Txt style={{ ...type_body, marginTop: space.sm }}>It’s on our side — your profile and Quest Log are safe. {message}</Txt>
        <View style={{ marginTop: space.lg }}><PopButton label="Try again" onPress={onRetry} /></View>
      </PopCard>
    </Screen>
  );
}
export function EntryScreen({ thin, onFind, onBrowse, onBuildProfile }: {
  thin: boolean; onFind: () => void; onBrowse: () => void; onBuildProfile: () => void;
}) {
  return (
    <Screen>
      <View style={{ marginTop: space.xl }}>
        <PopCard>
          <Txt style={type_h1}>{thin ? 'Your profile is empty' : 'Ready when you are'}</Txt>
          <Txt style={{ ...type_body, marginTop: space.sm }}>
            {thin
              ? 'Every match gets better once we know you. Takes 2 minutes — add a few things and your matches show up here.'
              : 'Answer a few quick questions and we’ll hand you a short, curated shortlist chosen for you.'}
          </Txt>
          <View style={{ marginTop: space.lg, flexDirection: 'row', gap: space.md, flexWrap: 'wrap' }}>
            {thin
              ? <PopButton label="Build my profile" onPress={onBuildProfile} />
              : <PopButton label="Find my matches" onPress={onFind} />}
          </View>
        </PopCard>
        <Pressable onPress={onBrowse} style={{ marginTop: space.lg, alignItems: 'center' }}>
          <Txt style={{ ...type_body, textDecorationLine: 'underline', color: colors.navy }}>
            {thin ? 'Browse opportunities instead' : 'Browse opportunities'}
          </Txt>
        </Pressable>
      </View>
    </Screen>
  );
}

// A small spinner wrapper so the color pin lives in one place.
function ActivitySpinner() {
  return <ActivityIndicator size="large" color={colors.orange} />;
}

// ---------- funnel rung ----------
// One question per screen: options as chips carrying their live survivor count, a "Show my
// matches now" escape (optional — only when the container supports it), Back and Skip.
export function RungStep({
  question, rationale, options, isVibe, poolCount, canBack, loading, onPick, onSkip, onBack, onShowAll,
}: {
  question: string; rationale?: string | null; options: RungOption[]; isVibe?: boolean;
  poolCount?: number | null; canBack: boolean; loading?: boolean;
  onPick: (value: string) => void; onSkip: () => void; onBack: () => void; onShowAll?: () => void;
}) {
  if (loading) {
    return (
      <Screen>
        <View style={{ paddingVertical: 80, alignItems: 'center', gap: space.lg }}>
          <ActivitySpinner />
          <Txt style={type_body}>Narrowing your list…</Txt>
        </View>
      </Screen>
    );
  }
  return (
    <Screen>
      <View style={{ marginTop: space.lg, gap: space.lg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: space.sm }}>
          {isVibe
            ? <Txt style={{ ...type_body, color: colors.slate500 }}>Quick vibe check</Txt>
            : <Txt style={{ ...type_body, color: colors.slate500 }}>
                {poolCount != null && <Txt style={{ fontFamily: fonts.display, color: colors.orange, fontSize: 18 }}>~{poolCount} </Txt>}
                opportunities left
              </Txt>}
          {onShowAll && (
            <Pressable onPress={onShowAll}><Txt style={{ fontFamily: fonts.bodyBold, color: colors.navy, textDecorationLine: 'underline' }}>Show my matches now →</Txt></Pressable>
          )}
        </View>
        {isVibe
          ? <Badge label="JUST FOR VIBES · SHAPES YOUR ORDER, NOT A FILTER" bg={colors.mint} fg={colors.emerald900} outline />
          : <Badge label="NARROWS THE LIST" bg={colors.peach} fg={colors.orangeDeep} outline />}

        <Txt style={type_h1}>{question}</Txt>
        {rationale ? <Txt style={type_body}>{rationale}</Txt> : null}

        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md }}>
          {options.map((o) => (
            <Pressable
              key={o.value}
              onPress={() => onPick(o.value)}
              style={{
                flex: 1, minWidth: 150, borderWidth: 3, borderColor: colors.navy, borderRadius: radius.lg,
                paddingVertical: space.lg, paddingHorizontal: space.lg, backgroundColor: colors.card,
              }}>
              <Txt style={{ fontFamily: fonts.bodyBold, fontSize: 15, color: colors.ink }}>{o.label}</Txt>
              {!isVibe && o.count != null && (
                <Txt style={{ ...type_body, fontSize: 12, color: colors.slate500, marginTop: 2 }}>{o.count} match{o.count === 1 ? '' : 'es'} left</Txt>
              )}
            </Pressable>
          ))}
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg }}>
          {canBack && (
            <Pressable onPress={onBack}><Txt style={{ ...type_body, color: colors.navy }}>← Back</Txt></Pressable>
          )}
          <View style={{ flex: 1 }} />
          <Pressable onPress={onSkip}><Txt style={{ ...type_body, color: colors.slate500 }}>{isVibe ? 'No preference' : 'Skip this question'}</Txt></Pressable>
        </View>
      </View>
    </Screen>
  );
}

// ---------- shortlist ----------
export function ShortlistView({
  picks, pendingIds, savedIds, dismissedIds, onToggle, onNotInterested, onSubmit, onStartFresh, onReview,
}: {
  picks: ShortlistItem[];
  pendingIds: Set<string>; savedIds: Set<string>; dismissedIds: Set<string>;
  onToggle: (id: string) => void; onNotInterested: (id: string) => void; onSubmit: () => Promise<void>;
  onStartFresh: () => void; onReview: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const stretch = picks.filter((p) => p.tier === 'stretch').length;
  const visible = picks.filter((p) => !dismissedIds.has(p.opp.id));

  return (
    <View style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={{ padding: space.lg, paddingBottom: 120, maxWidth: 896, alignSelf: 'center', width: '100%' }}>
        <Txt style={type_h1}>Your shortlist</Txt>
        <Txt style={{ ...type_body, marginTop: space.xs }}>
          {visible.length === 1 ? '1 chosen for you — more show up as we learn about you.'
            : `${visible.length} chosen for you, ordered by fit${stretch ? ` — ${stretch} ${stretch === 1 ? 'is a stretch pick' : 'are stretch picks'} worth a look` : ''}.`}
        </Txt>
        <View style={{ flexDirection: 'row', gap: space.lg, marginTop: space.sm, flexWrap: 'wrap' }}>
          <Pressable onPress={onReview}><Txt style={{ ...type_body, textDecorationLine: 'underline', color: colors.navy }}>Review your answers</Txt></Pressable>
          <Pressable onPress={onStartFresh}><Txt style={{ ...type_body, textDecorationLine: 'underline', color: colors.navy }}>Start fresh</Txt></Pressable>
        </View>

        {visible.length === 0 && (
          <PopCard style={{ marginTop: space.lg }}>
            <Txt style={type_h2}>Nothing quite fit — yet</Txt>
            <Txt style={{ ...type_body, marginTop: space.sm }}>That’s on the catalog being thin for what you asked, not on you. Try loosening a filter or check back soon.</Txt>
            <View style={{ marginTop: space.lg }}><PopButton label="Adjust my answers" onPress={onStartFresh} /></View>
          </PopCard>
        )}

        <View style={{ gap: space.lg, marginTop: space.lg }}>
          {visible.map((p) => (
            <ShortlistCard
              key={p.opp.id} opp={p.opp} tier={p.tier} reason={p.reason} flags={p.flags || []}
              pending={pendingIds.has(p.opp.id)} saved={savedIds.has(p.opp.id) && !pendingIds.has(p.opp.id)}
              onToggle={() => onToggle(p.opp.id)} onNotInterested={() => onNotInterested(p.opp.id)} />
          ))}
        </View>
      </ScrollView>

      {pendingIds.size > 0 && (
        <View style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: space.lg, backgroundColor: colors.card, borderTopWidth: 3, borderTopColor: colors.navy, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Txt style={{ fontFamily: fonts.bodyBold, color: colors.ink }}>{pendingIds.size} selected</Txt>
          <PopButton label={submitting ? 'Adding…' : `Add ${pendingIds.size} to my Quest Log`}
            onPress={async () => { setSubmitting(true); try { await onSubmit(); } finally { setSubmitting(false); } }} />
        </View>
      )}
    </View>
  );
}

function ShortlistCard({ opp, tier, reason, flags, pending, saved, onToggle, onNotInterested }: {
  opp: Opportunity; tier?: Tier; reason?: string; flags: string[];
  pending: boolean; saved: boolean; onToggle: () => void; onNotInterested: () => void;
}) {
  const t = tier ? TIER_BADGE[tier] : undefined;
  const whyText = reason || (tier ? 'A strong match for your profile.' : '');
  const rev = opp.review_status ? REVIEW_STATUS_META[String(opp.review_status)] : undefined;
  const [showReview, setShowReview] = useState(false);
  // Fixed-height cards: the description is clamped so every card is the same height; hovering
  // expands the card to reveal the full text (mirrors the pop-card hover interaction).
  const [hovered, setHovered] = useState(false);
  const url = (opp.url as string) || '';
  const openUrl = () => { if (url) void Linking.openURL(url); };
  const hasReview = !!rev && !!opp.review_summary;
  return (
    <PopCard
      onHoverChange={setHovered}
      style={pending ? { backgroundColor: colors.lime100 } : saved ? { backgroundColor: colors.slate50 } : undefined}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, alignItems: 'center' }}>
        {opp.type ? <MiniBadge label={String(opp.type)} bg={colors.violet200} fg={colors.violet900} /> : null}
        {t ? <MiniBadge label={t.label} bg={t.bg} fg={t.fg} /> : null}
        {rev ? (
          hasReview
            ? <Pressable onPress={() => setShowReview(true)}><MiniBadge label={`${rev.label} ⓘ`} bg={rev.bg} fg={rev.fg} /></Pressable>
            : <MiniBadge label={rev.label} bg={rev.bg} fg={rev.fg} />
        ) : null}
      </View>
      <Pressable onPress={openUrl} disabled={!url}>
        <Txt style={{ fontFamily: fonts.display, fontSize: 20, color: colors.navy, marginTop: space.sm, textDecorationLine: url ? 'underline' : 'none' }}>{opp.name}</Txt>
      </Pressable>
      {opp.org ? <Txt style={type_body}>{String(opp.org)}</Txt> : null}

      {whyText ? (
        <View style={{ flexDirection: 'row', marginTop: space.md, gap: space.sm }}>
          <View style={{ width: 4, borderRadius: 2, backgroundColor: colors.yellow300 }} />
          <View style={{ flex: 1 }}>
            <Txt style={type.label}>WHY IT FITS</Txt>
            <Txt numberOfLines={hovered ? undefined : 2} style={{ fontFamily: fonts.bodyBold, color: colors.ink, fontSize: 15 }}>{whyText}</Txt>
          </View>
        </View>
      ) : null}

      {opp.summary ? (
        <Txt numberOfLines={hovered ? undefined : 3} style={{ ...type_body, marginTop: space.md, color: colors.slate500 }}>{String(opp.summary)}</Txt>
      ) : null}

      {flags.length > 0 && (
        <View style={{ marginTop: space.md, gap: space.xs }}>
          {flags.map((f, i) => <Txt key={i} style={{ ...type_body, fontSize: 12, color: colors.slate500 }}>• {f}</Txt>)}
        </View>
      )}

      <View style={{ flexDirection: 'row', gap: space.lg, marginTop: space.lg, alignItems: 'center', justifyContent: 'flex-end' }}>
        {saved ? (
          <Txt style={{ fontFamily: fonts.bodyBold, color: colors.slate400 }}>✓ In Quest Log</Txt>
        ) : (
          <>
            <Pressable onPress={onNotInterested}><Txt style={{ ...type_body, color: colors.slate500 }}>Not interested</Txt></Pressable>
            <PopButton label={pending ? '✓ Saved' : 'Save Match'} onPress={onToggle} />
          </>
        )}
      </View>

      {showReview && hasReview && (
        <Pressable
          onPress={() => setShowReview(false)}
          style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.card, borderRadius: radius.xl, padding: space.xl, justifyContent: 'center' }}>
          <Txt style={type.label}>REVIEWS</Txt>
          {rev ? <Txt style={{ fontFamily: fonts.bodyBold, fontSize: 15, color: rev.fg === colors.white ? colors.ink : rev.fg, marginTop: space.xs }}>{rev.label}</Txt> : null}
          <Txt style={{ ...type_body, marginTop: space.sm, color: colors.ink }}>{String(opp.review_summary)}</Txt>
          <Txt style={{ ...type_body, marginTop: space.lg, fontSize: 12, color: colors.slate400 }}>Tap anywhere to close</Txt>
        </Pressable>
      )}
    </PopCard>
  );
}

// ---------- not-interested modal ----------
export function NotInterestedModal({ name, onPick, onClose }: { name: string; onPick: (reason: string | null) => void; onClose: () => void }) {
  return (
    <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(15,23,42,0.45)', justifyContent: 'center', padding: space.lg }}>
      <PopCard style={{ maxWidth: 460, alignSelf: 'center', width: '100%' }}>
        <Txt style={type_h2}>Not interested in {name}?</Txt>
        <Txt style={{ ...type_body, marginTop: space.xs }}>Optional — tell us why and we’ll show you fewer like this. Nothing is deleted; you can always find it again.</Txt>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.lg }}>
          {NOT_INTERESTED_REASONS.map((r) => (
            <Pressable key={r} onPress={() => onPick(r)} style={{ borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, paddingVertical: space.sm, paddingHorizontal: space.md }}>
              <Txt style={{ fontFamily: fonts.bodyMed, color: colors.ink }}>{r}</Txt>
            </Pressable>
          ))}
        </View>
        <View style={{ flexDirection: 'row', gap: space.lg, marginTop: space.lg, alignItems: 'center' }}>
          <Pressable onPress={() => onPick(null)}><Txt style={{ fontFamily: fonts.bodyBold, color: colors.navy }}>Skip</Txt></Pressable>
          <View style={{ flex: 1 }} />
          <Pressable onPress={onClose}><Txt style={type_body}>Cancel</Txt></Pressable>
        </View>
      </PopCard>
    </View>
  );
}

// ---------- review drawer ----------
export function ReviewDrawer({ open, onClose, sections, onAdjust }: {
  open: boolean; onClose: () => void; sections: ReviewSection[]; onAdjust: () => void;
}) {
  return (
    <RightDrawer open={open} onClose={onClose} width={380} duration={250}
      panelStyle={{ borderLeftWidth: 4, borderLeftColor: colors.navy, backgroundColor: colors.card }}>
      <View style={{ flex: 1 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', padding: space.lg, borderBottomWidth: 2, borderBottomColor: colors.hairline }}>
          <View style={{ flex: 1 }}>
            <Txt style={type_h2}>Your answers</Txt>
            <Txt style={{ ...type_body, marginTop: space.xs }}>What we used to build this list.</Txt>
          </View>
          <Pressable onPress={onClose} hitSlop={10}><Txt style={{ fontFamily: fonts.bodyBold, fontSize: 20, color: colors.slate500 }}>✕</Txt></Pressable>
        </View>
        <ScrollView contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
          {sections.length === 0 ? (
            <Txt style={type_body}>You jumped straight to matches — no filters yet.</Txt>
          ) : sections.map((s) => (
            <View key={s.title} style={{ gap: space.sm }}>
              <Txt style={type.label}>{s.title.toUpperCase()}</Txt>
              {s.items.map((it, i) => (
                <View key={i} style={{ flexDirection: 'row', justifyContent: 'space-between', gap: space.md, alignItems: 'flex-start' }}>
                  <Txt style={{ ...type_body, color: colors.slate500 }}>{it.label}</Txt>
                  <Txt style={{ fontFamily: fonts.bodyBold, color: colors.ink, textAlign: 'right', flexShrink: 1 }}>{it.value}</Txt>
                </View>
              ))}
            </View>
          ))}
          <View style={{ marginTop: space.md }}><PopButton label="Adjust my answers" onPress={onAdjust} /></View>
        </ScrollView>
      </View>
    </RightDrawer>
  );
}
