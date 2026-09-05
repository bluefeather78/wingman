import { extractJSON } from './extractJSON';

// The ONE way this app reaches a model, as of S1-1 (finding C1.2).
//
// It used to be `callGemini(system, userContent, useWebSearch, maxTokens)` — the client
// composed the prompt, chose the provider by picking an endpoint, and named its own token
// budget, and the server forwarded all of it. Every prompt therefore shipped in the web
// bundle, and the contract any account holder could see was "send any prompt, any input,
// search on, 8k output".
//
// Now the client names a FEATURE and hands it inputs. The prompt text, the provider, the
// tool config and the budget all live in app/services/prompts.py, and the server refuses a
// feature it does not know. Injected, exactly as the old call was, so this module stays
// pure and the salvaged logic stays testable without auth.
export type FeatureCall = (
  feature: string,
  inputs: Record<string, unknown>,
) => Promise<FeatureResult>;

// Named FeatureResult, not AiResult: src/api/types.ts already exports an AiResult of the
// same shape, and src/lib/ deliberately does not import from src/api/ — these two are
// structurally identical, which is all TypeScript needs, and two different names make it
// obvious which layer each belongs to.
export interface FeatureResult {
  text: string;
  // True when the model stopped on max_tokens. Only profile synthesis reads it, and the
  // retry it used to drive now happens server-side with the budget it belongs to — this
  // survives so the caller can still refuse to save a fragment over a complete profile.
  truncated: boolean;
}

// MARQUEE M9 (Phase 5, frontend_report finding 7): THIS IS THE ONLY RETRY.
//
// Approved by Shama on 2026-09-05 (decision 13 in PRODUCTION_READINESS_PLAN.md). It edits a
// code path that makes paid API calls, and it strictly REDUCES what they cost — no prompt text
// moves, so it is not an M8 change.
//
// The finding: "parse-retry inside callGeminiJSON × outer retry = up to 4 paid calls per search
// and per tracker add; enrichment up to 4 rounds; no global budget." Three call sites wrapped
// this function in a second try/retry — finder.tsx's why-it-fits reasoning, trackerAdd's
// meta/fit extraction, and the enrichment rounds — each believing it was covering "a transient
// network/API error" that the retry here did not handle.
//
// It always did. The catch below wraps the CALL as well as the parse, so a network failure, a
// 502 and a malformed body all take the same path. The outer retries were therefore pure
// multiplication: 2 × 2 = four billed calls for one student pressing one button, and a student
// on a bad connection paid the most.
//
// So: one retry, here, with the backoff the outer retries were carrying. Every caller that had
// its own is now written against this one. If a future call site wants a retry, it belongs in
// this function — a second one anywhere else re-creates the multiplication.
export const RETRY_BACKOFF_MS = 1200;

// The absolute ceiling on billed attempts for one callFeatureJSON. Named so the number is
// assertable from a test rather than living only inside a try/catch shape that a later edit
// could quietly change.
export const MAX_PAID_ATTEMPTS = 2;

export async function callFeatureJSON<T = unknown>(
  call: FeatureCall,
  feature: string,
  inputs: Record<string, unknown>,
): Promise<T> {
  try {
    return extractJSON<T>((await call(feature, inputs)).text);
  } catch (first) {
    // A one-off formatting glitch usually clears on a fresh call with the same prompt, and a
    // transient network error clears on a fresh connection — but neither clears in the same
    // millisecond, which is why the backoff moved in here from the call sites that had it.
    await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS));
    try {
      return extractJSON<T>((await call(feature, inputs)).text);
    } catch (second) {
      // The SECOND failure is what the caller sees. The first is logged rather than swallowed
      // so a repeating first-attempt failure (a bad prompt, a model change) is still visible
      // in the console instead of being hidden behind whatever the retry happened to hit.
      console.warn(`${feature}: first attempt failed (${(first as Error).message}), retry also failed`);
      throw second;
    }
  }
}
