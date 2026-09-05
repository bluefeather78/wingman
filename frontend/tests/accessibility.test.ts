import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';

// frontend_report finding 20: "5 accessibilityLabels in the whole app; every IconBtn
// (refresh, sync, search, star, remove), the avatar, all ✕ close buttons, task delete/add,
// mic/voice are unlabelled; text-glyph checkboxes carry no role/state."
//
// A control whose entire content is an icon or an emoji announces as "button" — or, worse, by
// the emoji's own name ("bust in silhouette"). A ✓ in a <Text> is not a checkbox to anything
// but a sighted reader: no role, no state, no way to tell selected from not.
//
// These are source assertions rather than rendered ones, deliberately. The harness does not
// render (see vitest.config.ts), and the regression this guards is a NEW control shipping
// without a label — which is exactly what a source scan catches.

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8');

describe('IconBtn cannot ship without a label', () => {
  it('requires the prop, so the compiler catches an unlabelled one', () => {
    const src = read('src/ui/components.tsx');
    // IconBtn's own prop block only — other components in this file legitimately take an
    // optional `label` (StatusPill's overriding text, a field's caption), and matching on the
    // whole file would either fail on those or pass on nothing.
    const iconBtn = src.split('export function IconBtn')[1].slice(0, 900);
    // Not `label?:` — an optional prop gets left off, and there is no way to notice from
    // looking at the screen.
    expect(iconBtn).toMatch(/label: string;/);
    expect(iconBtn).not.toMatch(/label\?: string/);
    expect(iconBtn).toMatch(/accessibilityLabel=\{label\}/);
    expect(iconBtn).toMatch(/accessibilityRole="button"/);
  });

  it('announces its disabled state, which is otherwise invisible', () => {
    // These are disabled by passing `undefined` for onPress while a pass is in flight.
    const iconBtn = read('src/ui/components.tsx').split('export function IconBtn')[1].slice(0, 900);
    expect(iconBtn).toMatch(/accessibilityState=\{\{ disabled/);
  });
});

/** Labels reaching a control either directly or through IconBtn's required `label` prop. */
function labelCount(src: string): number {
  return (src.match(/accessibilityLabel/g) ?? []).length
    + (src.match(/\blabel=\{?["'`{]/g) ?? []).length;
}

describe('every icon-only control the audit named now has a label', () => {
  it.each([
    ['app/(app)/tracker.tsx', 5],
    // The per-row star and remove buttons moved here with the card itself (Phase 5, the
    // screen split); the labels moved with them.
    ['src/ui/tracker/ListCard.tsx', 2],
    ['app/(app)/profile.tsx', 5],
    ['app/(app)/index.tsx', 2],
    ['src/ui/NavBar.tsx', 2],
  ])('%s carries at least %i', (path, min) => {
    expect(labelCount(read(path))).toBeGreaterThanOrEqual(min);
  });

  it('every IconBtn in the app names what it does', () => {
    // The compiler already enforces this; the test states the count so a future IconBtn
    // added without one is a red test rather than only a red build.
    const sources = ['app/(app)/tracker.tsx', 'src/ui/tracker/ListCard.tsx'].map(read);
    const buttons = sources.reduce((n, s) => n + (s.match(/<IconBtn\b/g) ?? []).length, 0);
    expect(buttons).toBe(5);
    for (const src of sources) {
      for (const block of src.split('<IconBtn').slice(1)) {
        expect(block.slice(0, 400)).toMatch(/label=/);
      }
    }
  });

  it('names the ITEM, not just the action, on per-row controls', () => {
    // A dozen cards each announcing "Save for later" gives no way to tell which one.
    const src = read('src/ui/tracker/ListCard.tsx');
    expect(src).toMatch(/Save \$\{item\.name\} for later/);
    expect(src).toMatch(/Remove \$\{item\.name\} from your Quest Log/);
    expect(read('app/(app)/index.tsx')).toMatch(/Delete task: \$\{ai\.text\}/);
  });
});

describe('the text-glyph checkbox is a checkbox', () => {
  it('carries a role and a checked state', () => {
    const src = read('app/(app)/tracker.tsx');
    expect(src).toMatch(/accessibilityRole="checkbox"/);
    expect(src).toMatch(/accessibilityState=\{\{ checked: checked \|\| tracked/);
  });
});

describe('toggles announce their state, not just their name', () => {
  it('voice output and dictation are switches', () => {
    const src = read('app/(app)/profile.tsx');
    expect((src.match(/accessibilityRole="switch"/g) ?? []).length).toBe(2);
    expect(src).toMatch(/accessibilityState=\{\{ checked: voiceOn \}\}/);
    expect(src).toMatch(/accessibilityState=\{\{ checked: listening \}\}/);
  });
});

describe('the whole app, counted', () => {
  it('has many more labels than the five the audit found', () => {
    const files = [
      'app/(app)/tracker.tsx', 'app/(app)/profile.tsx', 'app/(app)/index.tsx',
      'app/(app)/finder.tsx', 'app/landing.tsx', 'src/ui/NavBar.tsx',
      'src/ui/components.tsx', 'src/ui/tracker/ListCard.tsx',
    ];
    const total = files.reduce((n, f) => n + labelCount(read(f)), 0);
    expect(total).toBeGreaterThanOrEqual(20);
  });
});
