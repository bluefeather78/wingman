import { extractJSON } from './extractJSON';

// Model call signatures, injected so the salvaged logic stays pure and auth-independent.
export type GeminiCall = (
  system: string,
  userContent: string,
  useWebSearch?: boolean,
) => Promise<string>;

export type ClaudeDetailedCall = (
  system: string,
  userContent: string,
  useWebSearch?: boolean,
  maxTokens?: number,
) => Promise<{ text: string; truncated: boolean }>;

// Mirrors script.js callGeminiJSON / callClaudeJSON: parse the model's JSON, retrying the
// whole request once if the first response can't be parsed (a one-off formatting glitch
// usually clears on a fresh call with the same prompt — it's not something that reliably
// repeats).
export async function callGeminiJSON<T = unknown>(
  callGemini: GeminiCall,
  system: string,
  userContent: string,
  useWebSearch = false,
): Promise<T> {
  try {
    return extractJSON<T>(await callGemini(system, userContent, useWebSearch));
  } catch {
    return extractJSON<T>(await callGemini(system, userContent, useWebSearch));
  }
}
