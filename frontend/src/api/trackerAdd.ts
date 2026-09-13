import { addTrackerItemChecked } from './trackerStore';
import type { Opportunity } from './types';
import type { Bucket } from '@/lib/constants';
import {
  findBucketForKind,
  kindForOpp,
  staticGenericChecklist,
} from '@/lib/tracker';

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

const VALID_STATUS = ['running', 'not_running', 'rolling', 'unknown'];

// Single source of truth for "add a catalog Opportunity to the Quest Log". Kept in step with
// finder.tsx's addOneToTracker so Fresh Finds and the Quest Log's catalog search cannot drift.
//
// OPTIMISTIC ADD (2026-09-12): the add makes NO network calls. The card is built entirely from
// catalog data the client already has in memory, so adding is instant even for a whole
// selection at once. Dates, the verified checklist and the important-date note are filled in
// afterwards by the Quest Log's free catalog sync (cached rows) and its parallel per-card fresh
// check (never-checked rows) — the two freshness paths that already own that data. This removed
// three serial per-item round trips from every add (a Gemini meta/fit call, the paid deadline
// check, the checklist fetch); the deadline check in particular used to run inline here and
// frequently timed out, freezing "Live details couldn't be fetched" onto the card.
export async function addCatalogOpportunity(
  opp: Opportunity,
  bucket: Bucket,
  reason: string,
): Promise<AddCatalogResult> {
  const url = (opp.url as string) ?? null;
  const summary = (opp.summary as string) || '';
  // status is the ONE deadline-ish field the catalog payload carries, so the status pill and
  // the rolling/over banners are correct on first paint. important_dates is NOT in the payload,
  // so it starts empty and the sync/fresh-check fills it.
  const catalogStatus = (opp.status as string) || '';
  const status = VALID_STATUS.includes(catalogStatus) ? catalogStatus : 'unknown';

  // meta/fit from data already in hand (see the note above the file's other add copy): `meta`
  // is superseded by buildMetaPills from the facet fields, `fit` is toggle-only on the card.
  const meta = [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · ');

  const res = await addTrackerItemChecked(bucket, {
    id: opp.id,
    name: opp.name,
    org: (opp.org as string) ?? null,
    url,
    type: (opp.type as string) ?? null,
    bucket,
    progressStatus: 'not_started',
    status,
    reviewStatus: (opp.review_status as string) ?? null,
    reviewSummary: (opp.review_summary as string) ?? null,
    meta,
    // Structured facets for the Quest Log's meta pills (opp.location is the FORMAT).
    price: (opp.price as string) ?? null,
    format: (opp.location as string) ?? null,
    state: (opp.state as string) ?? null,
    season: (opp.season as string) ?? null,
    fit: reason || summary,
    // No note at add: the deadline check has not run, so there is nothing to caveat yet. The
    // free sync / fresh check fills important_date_note when it has one. Leaving it undefined
    // (rather than a placeholder) is what stops the old "Live details couldn't be fetched"
    // string ever being frozen onto a freshly-added card.
    noteType: status === 'not_running' ? 'flag' : 'plain',
    // Filled by the Quest Log's catalog sync (cached rows) or its per-card fresh check.
    importantDates: [],
    deadlineLabel: 'CHECK SITE',
    wasEstimated: false,
    applyUrl: url,
    applyLabel: 'Apply / learn more',
    // The honest placeholder until the verified checklist arrives via sync / fresh check. It
    // asserts nothing, so it cannot reintroduce the invented-prerequisite failure; mergeAction
    // Items replaces it (different text, non-user origin) the moment verified tasks land.
    actionItems: staticGenericChecklist(opp.id, url),
  });
  return { added: res.added, existingName: res.existing?.name };
}
