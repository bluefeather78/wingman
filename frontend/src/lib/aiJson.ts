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

// Mirrors the old callGeminiJSON: parse the model's JSON, retrying the whole request once
// if the first response can't be parsed (a one-off formatting glitch usually clears on a
// fresh call with the same prompt — it's not something that reliably repeats).
export async function callFeatureJSON<T = unknown>(
  call: FeatureCall,
  feature: string,
  inputs: Record<string, unknown>,
): Promise<T> {
  try {
    return extractJSON<T>((await call(feature, inputs)).text);
  } catch {
    return extractJSON<T>((await call(feature, inputs)).text);
  }
}
