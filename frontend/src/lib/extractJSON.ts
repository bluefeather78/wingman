// Ported verbatim (behavior-preserving) from script.js `extractJSON`.
// Pulls the first JSON value out of model output via brace/bracket-depth scanning,
// tolerating trailing commentary and best-effort-repairing a response truncated
// mid-structure (e.g. by a token limit). No network, no DOM — pure and unit-testable.
export function extractJSON<T = unknown>(text: string): T {
  const start = text.search(/[{[]/);
  if (start === -1) throw new Error('No JSON found in response');

  const openChar = text[start];
  const closeChar = openChar === '{' ? '}' : ']';
  void closeChar; // kept for parity with the source; matching is depth-based below
  let depth = 0;
  let inString = false;
  let escaped = false;
  let end = -1;

  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === '\\') {
        escaped = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === '{' || ch === '[') {
      depth++;
    } else if (ch === '}' || ch === ']') {
      depth--;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }

  let candidate: string;
  if (end !== -1) {
    candidate = text.slice(start, end + 1);
  } else {
    // Truncated mid-structure — attempt a best-effort repair.
    candidate = text.slice(start);
    if (inString) {
      candidate += '"';
    }
    // Trim a dangling comma or partial key/value before closing.
    candidate = candidate.replace(/,\s*$/, '');
    const stack: string[] = [];
    let scanString = false;
    let scanEscaped = false;
    for (let i = 0; i < candidate.length; i++) {
      const ch = candidate[i];
      if (scanString) {
        if (scanEscaped) {
          scanEscaped = false;
        } else if (ch === '\\') {
          scanEscaped = true;
        } else if (ch === '"') {
          scanString = false;
        }
        continue;
      }
      if (ch === '"') {
        scanString = true;
        continue;
      }
      if (ch === '{' || ch === '[') {
        stack.push(ch);
      } else if (ch === '}' || ch === ']') {
        stack.pop();
      }
    }
    while (stack.length) {
      const opener = stack.pop();
      candidate += opener === '{' ? '}' : ']';
    }
  }

  try {
    return JSON.parse(candidate) as T;
  } catch (e) {
    throw new Error(`Could not parse JSON from response: ${(e as Error).message}`);
  }
}
