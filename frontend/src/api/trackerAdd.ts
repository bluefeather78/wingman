import { httpClient } from './httpClient';
import { addTrackerItemChecked } from './trackerStore';
import type { Opportunity } from './types';
import type { Bucket } from '@/lib/constants';
import {
  findBucketForKind,
  kindForOpp,
  normalizeVerifiedActionItems,
  staticGenericChecklist,
  type TrackerInfo,
} from '@/lib/tracker';
import { isValidDateISO } from '@/lib/dateISO';

export interface AddCatalogResult {
  /** False when the item was already tracked (by id OR url) and nothing was written. */
  added: boolean;
  /** Name of the item that blocked the add, so the caller can say WHAT it collided with. */
  existingName?: string;
}

// Default bucket for a catalog row when the caller has no better signal (the Quest Log
// search has no ranking call to say which kind surfaced the row). Fresh Finds passes an
// explicit bucket instead — the kind that actually surfaced the card.
export function bucketForOpp(opp: Opportunity): Bucket {
  return findBucketForKind(kindForOpp(opp));
}

// Single source of truth for "add a catalog Opportunity to the Quest Log". Kept in step with
// finder.tsx's addOneToTracker so Fresh Finds and the Quest Log's catalog search cannot drift:
// meta/fit from catalog data (no model call), the shared (cached) deadline check, the
// server-verified action-item checklist, then addTrackerItemChecked.
export async function addCatalogOpportunity(
  opp: Opportunity,
  bucket: Bucket,
  reason: string,
): Promise<AddCatalogResult> {
  const url = (opp.url as string) ?? null;
  const type = (opp.type as string) ?? null;
  const reviewStatus = (opp.review_status as string) ?? null;
  const reviewSummary = (opp.review_summary as string) ?? null;
  const summary = (opp.summary as string) || '';

  // meta/fit are built from data already in hand — no model call on the add path. The old
  // meta/fit Gemini call produced only these two cosmetic fields (dates/status/
  // tasks moved to the verified endpoints in P8), and both already had catalog fallbacks that
  // were indistinguishable in practice: `meta` is superseded by buildMetaPills from the facet
  // fields, and `fit` is toggle-only on the Quest Log card. Using the fallbacks directly
  // removes a per-item blocking round trip from every add.
  const meta = [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · ');

  let deadline: Partial<TrackerInfo> | null = null;
  try {
    deadline = await httpClient.getDeadlineCheck(opp.id);
  } catch (err) {
    console.warn(`Deadline check failed for ${opp.name}:`, (err as Error).message);
  }

  // The catalog's checklist, generated and quote-verified server-side (getActionItems
  // never throws — null on failure). The static generic list is the fallback when the
  // endpoint has nothing — it asserts nothing, so it cannot reintroduce the
  // invented-prerequisite failure the old model fallback carried.
  const shared = await httpClient.getActionItems(opp.id);
  const verified = normalizeVerifiedActionItems(shared?.action_items, opp.id);
  const sharedItems = verified.length ? verified : staticGenericChecklist(opp.id, url);

  const status = deadline?.status
    && ['running', 'not_running', 'rolling', 'unknown'].includes(deadline.status)
    ? deadline.status
    : 'unknown';
  const res = await addTrackerItemChecked(bucket, {
    id: opp.id,
    name: opp.name,
    org: (opp.org as string) ?? null,
    url,
    type,
    bucket,
    progressStatus: 'not_started',
    status,
    reviewStatus,
    reviewSummary,
    meta,
    // Structured facets for the Quest Log's meta pills (opp.location is the FORMAT).
    price: (opp.price as string) ?? null,
    format: (opp.location as string) ?? null,
    state: (opp.state as string) ?? null,
    season: (opp.season as string) ?? null,
    fit: reason || summary,
    note: deadline?.important_date_note
      || (deadline
        ? 'Details from the opportunities database — confirm on the official site.'
        : "Live details couldn't be fetched — showing database info only. Check the official site directly."),
    noteType: status === 'not_running' ? 'flag' : deadline ? 'plain' : 'flag',
    importantDates: Array.isArray(deadline?.important_dates)
      ? deadline.important_dates
          // isValidDateISO, not truthiness (Phase 5, finding 11): a stored "TBD" or
          // "2026-13-45" made daysUntil NaN, and every comparison against NaN is false, so
          // the card read HAPPENING NOW and the calendar rendered the literal string NaN.
          .filter((d) => isValidDateISO(d?.date_iso))
          .map((d) => ({
            label: d.label || 'Date',
            dateISO: d.date_iso,
            type: d.type || 'deadline',
            estimated: d.estimated,
            verified: d.verified,
            sourceUrl: d.source_url ?? null,
          }))
          .sort((a, b) => a.dateISO.localeCompare(b.dateISO))
      : [],
    deadlineLabel: 'CHECK SITE',
    wasEstimated: !!deadline?.was_estimated,
    applyUrl: url,
    applyLabel: 'Apply / learn more',
    actionItems: sharedItems,
  });
  return { added: res.added, existingName: res.existing?.name };
}
