import { describe, it, expect } from 'vitest';
import { parseGradeFromText, parseGradeLevel, isGradeEligible } from '@/lib/grade';
import type { Opportunity } from '@/api/types';

// frontend_report finding 8. The parser matched bare `junior`, `senior` and `middle school`
// anywhere in a paragraph, and the number it produced is a HARD filter on both sides: the
// client's preFilter drops rows outside the range, and the same number goes to /api/match
// where the server drops verified-ineligible rows too. So a false positive silently removes
// real matches, with nothing on screen explaining why — while a false negative removes
// nothing at all. That asymmetry is why the fix is a context requirement rather than a
// blocklist of nouns: an unfamiliar phrasing must fail to "no grade", not to a wrong one.

describe('parseGradeFromText — the false positives that were setting a hard filter', () => {
  it.each([
    ['I play junior varsity soccer', 'junior varsity'],
    ['I captain the JV team and play junior varsity tennis', 'junior varsity'],
    ['I volunteer weekly with senior citizens at a care home', 'senior citizens'],
    ['I want to be a senior software engineer one day', 'senior <job title>'],
    ['I tutor middle school kids in algebra', 'middle school kids'],
    ['I run a robotics club for middle school students in my town', 'middle school students'],
    ['My sister is a college freshman', 'somebody else'],
    ['I read The Junior Encyclopedia of Science', 'a title'],
  ])('%s → null (%s)', (text) => {
    expect(parseGradeFromText(text)).toBeNull();
  });
});

describe('parseGradeFromText — the real statements it must still read', () => {
  it.each<[string, number]>([
    ['I am a rising senior interested in neuroscience', 12],
    ['Rising junior, into competitive math', 11],
    ["I'm a sophomore at Roosevelt High", 10],
    ['im a freshman and I love building robots', 9],
    ['I am an incoming freshman', 9],
    ['Going into senior year I want research experience', 12],
    ['During my junior year I founded a nonprofit', 11],
    ['A junior in high school looking for summer programs', 11],
    ['high school senior applying to engineering programs', 12],
    ['I am in 11th grade', 11],
    ['currently grade 10', 10],
    ['9th-grade student, first year of high school', 9],
    ['I am in middle school and love astronomy', 8],
    ['I am a middle schooler who codes', 8],
  ])('%s → grade %i', (text, expected) => {
    expect(parseGradeFromText(text)).toBe(expected);
  });

  it('reads a grade out of a long realistic profile', () => {
    const profile = [
      'Maya is a rising senior with a strong interest in computational biology.',
      'She plays junior varsity volleyball and volunteers with senior citizens',
      'at a local care home, and she tutors middle school kids in algebra.',
    ].join(' ');
    // "rising senior" is the only statement about HER grade; the three decoys that used to
    // win are all still in the text.
    expect(parseGradeFromText(profile)).toBe(12);
  });

  it('prefers a stated grade over nothing, and never invents one from an empty profile', () => {
    expect(parseGradeFromText('')).toBeNull();
    expect(parseGradeFromText(null)).toBeNull();
    expect(parseGradeFromText(undefined)).toBeNull();
    expect(parseGradeFromText('I like chemistry and long walks')).toBeNull();
  });
});

describe('parseGradeLevel — the explicit dropdown values', () => {
  // These are the finder's actual options (finder.tsx SoftSelect).
  it.each<[string, number]>([
    ['Middle School', 8],
    ['9th grade', 9],
    ['10th grade', 10],
    ['11th grade', 11],
    ['12th grade', 12],
  ])('%s → grade %i', (label, expected) => {
    expect(parseGradeLevel(label)).toBe(expected);
  });

  it('accepts a bare word, because a grade field has no sentence for it to be an adjective in', () => {
    expect(parseGradeLevel('Senior')).toBe(12);
    expect(parseGradeLevel('junior')).toBe(11);
    expect(parseGradeLevel('  Rising Sophomore ')).toBe(10);
  });

  it('does not accept a sentence — that is what parseGradeFromText is for', () => {
    expect(parseGradeLevel('I play junior varsity soccer')).toBeNull();
  });

  it('answers null for the opt-out option rather than guessing', () => {
    expect(parseGradeLevel('Prefer not to say')).toBeNull();
    expect(parseGradeLevel('')).toBeNull();
  });
});

describe('parseGradeFromText still understands a dropdown value passed to it', () => {
  // A caller that has not been updated (or a stored value read back) must keep working.
  it('reads an explicit label', () => {
    expect(parseGradeFromText('11th grade')).toBe(11);
    expect(parseGradeFromText('Senior')).toBe(12);
    expect(parseGradeFromText('Middle School')).toBe(8);
  });
});

describe('isGradeEligible — unchanged, and the reason a wrong grade hurts', () => {
  const opp = (min: number | null, max: number | null) =>
    ({ id: 'x', grade_min: min, grade_max: max } as unknown as Opportunity);

  it('filters nothing when the grade is unknown', () => {
    expect(isGradeEligible(opp(9, 12), null)).toBe(true);
    expect(isGradeEligible(opp(6, 8), null)).toBe(true);
  });

  it('filters nothing when the row has no bounds', () => {
    expect(isGradeEligible(opp(null, null), 11)).toBe(true);
  });

  it('excludes outside the range — which is why a mis-parsed grade is silent damage', () => {
    // The "I tutor middle school kids" case: grade 8 would have hidden every 9-12 programme.
    expect(isGradeEligible(opp(9, 12), 8)).toBe(false);
    expect(isGradeEligible(opp(9, 12), 11)).toBe(true);
    expect(isGradeEligible(opp(6, 8), 11)).toBe(false);
  });

  it('honours a one-sided bound', () => {
    expect(isGradeEligible(opp(11, null), 10)).toBe(false);
    expect(isGradeEligible(opp(11, null), 12)).toBe(true);
    expect(isGradeEligible(opp(null, 10), 11)).toBe(false);
  });
});
