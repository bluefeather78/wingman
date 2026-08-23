import { Platform, type TextStyle, type ViewStyle } from 'react-native';

// BENTO & POP — the design system ported from the original app's styles.css so the RN
// rebuild reads as the same product for high-schoolers: a warm cream canvas, navy ink and
// borders, hard offset "pop" shadows, and a few loud accents used sparingly.

export const colors = {
  cream: '#FBF8F3', // app background
  page: '#F4F5F9', // secondary surface
  card: '#FFFFFF',
  navy: '#1D4E89', // primary ink + borders + pop shadow
  ink: '#1A2540', // headings
  inkSoft: '#3A4A6B',
  lime: '#D7F542',
  orange: '#F4791D',
  purple: '#6A63E8',
  yellow: '#F4B400',
  red: '#D64545',
  green: '#1F9D6B',
  greenSoft: '#DCF7EA',
  muted: '#8A93A6',
  hairline: '#D9DEEB',
  borderSoft: '#C9C9F0',
  white: '#FFFFFF',
} as const;

export const fonts = {
  display: 'SpaceGrotesk_700Bold',
  displaySemi: 'SpaceGrotesk_500Medium',
  body: 'PlusJakartaSans_400Regular',
  bodyMed: 'PlusJakartaSans_500Medium',
  bodyBold: 'PlusJakartaSans_700Bold',
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 10,
  md: 14,
  lg: 20,
  xl: 28,
  pill: 999,
} as const;

export const type = {
  hero: { fontFamily: fonts.display, fontSize: 34, lineHeight: 38, color: colors.ink } as TextStyle,
  h1: { fontFamily: fonts.display, fontSize: 26, lineHeight: 30, color: colors.ink } as TextStyle,
  h2: { fontFamily: fonts.display, fontSize: 20, lineHeight: 24, color: colors.ink } as TextStyle,
  h3: { fontFamily: fonts.display, fontSize: 16, lineHeight: 20, color: colors.ink } as TextStyle,
  body: { fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.inkSoft } as TextStyle,
  bodyStrong: { fontFamily: fonts.bodyBold, fontSize: 15, lineHeight: 22, color: colors.ink } as TextStyle,
  small: { fontFamily: fonts.body, fontSize: 13, lineHeight: 18, color: colors.muted } as TextStyle,
  label: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.navy, letterSpacing: 0.4 } as TextStyle,
} as const;

// The signature "pop" shadow: a hard, un-blurred offset in navy behind a bordered surface.
// On web this becomes box-shadow: Xpx Ypx 0 navy; on iOS a zero-radius shadow; on Android
// elevation approximates it (Android can't render a zero-blur colored offset natively).
export function popShadow(offset = 4): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: `${offset}px ${offset}px 0px ${colors.navy}` } as unknown as ViewStyle,
    ios: {
      shadowColor: colors.navy,
      shadowOffset: { width: offset, height: offset },
      shadowOpacity: 1,
      shadowRadius: 0,
    },
    default: { elevation: offset },
  }) as ViewStyle;
}

// A soft ambient shadow for the quieter "card-soft" surfaces.
export function softShadow(): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: '0 2px 18px rgba(15,23,42,0.06)' } as unknown as ViewStyle,
    ios: { shadowColor: '#0F172A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.06, shadowRadius: 18 },
    default: { elevation: 2 },
  }) as ViewStyle;
}
