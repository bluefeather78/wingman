import { Platform, type TextStyle, type ViewStyle } from 'react-native';

// BENTO & POP — design tokens matched to the live app (styles.css + screenshots of :8000).
// Cream canvas, navy ink, ORANGE primary CTA, lavender inputs/secondary surfaces, soft white
// content cards, and the "pop" hard offset shadow reserved for buttons/emphasis. Display face
// Space Grotesk, body Plus Jakarta Sans.

export const colors = {
  cream: '#FBF8F3', // app background
  page: '#F4F5F9',
  card: '#FFFFFF',
  lavender: '#EEF0FB', // inputs, kind cards, secondary surfaces
  lavenderDeep: '#DFE4F7',
  navy: '#1D4E89', // primary ink, borders, nav bar, pop shadow
  navyDeep: '#0E1830',
  ink: '#1A2540', // headings
  inkSoft: '#4A6685',
  orange: '#F79256', // PRIMARY accent / CTA
  orangeDeep: '#F4791D',
  teal: '#00B2CA', // avatar, "completed" accents
  lime: '#D7F542',
  purple: '#6A63E8',
  yellow: '#F4B400', // BETA badge
  red: '#D64545',
  redSoft: '#FFE4E6',
  green: '#1F9D6B',
  greenSoft: '#DCF7EA',
  muted: '#8A93A6',
  hairline: '#D9DEEB',
  borderSoft: '#C9C9F0',
  white: '#FFFFFF',
  // Blue promo-banner gradient endpoints.
  bannerFrom: '#1D6FB8',
  bannerTo: '#153E6E',
} as const;

export const fonts = {
  display: 'SpaceGrotesk_700Bold',
  displayMed: 'SpaceGrotesk_500Medium',
  body: 'PlusJakartaSans_400Regular',
  bodyMed: 'PlusJakartaSans_500Medium',
  bodyBold: 'PlusJakartaSans_700Bold',
} as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
export const radius = { sm: 10, md: 14, lg: 20, xl: 26, pill: 999 } as const;

export const type = {
  hero: { fontFamily: fonts.display, fontSize: 30, lineHeight: 34, color: colors.navy } as TextStyle,
  h1: { fontFamily: fonts.display, fontSize: 24, lineHeight: 28, color: colors.navy } as TextStyle,
  h2: { fontFamily: fonts.display, fontSize: 20, lineHeight: 24, color: colors.navy } as TextStyle,
  h3: { fontFamily: fonts.display, fontSize: 16, lineHeight: 20, color: colors.ink } as TextStyle,
  body: { fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.inkSoft } as TextStyle,
  bodyStrong: { fontFamily: fonts.bodyBold, fontSize: 15, lineHeight: 22, color: colors.ink } as TextStyle,
  small: { fontFamily: fonts.body, fontSize: 13, lineHeight: 18, color: colors.muted } as TextStyle,
  label: { fontFamily: fonts.bodyBold, fontSize: 11, lineHeight: 15, color: colors.muted, letterSpacing: 0.6 } as TextStyle,
} as const;

// The signature "pop" shadow: a hard, un-blurred navy offset behind a bordered surface.
export function popShadow(offset = 3, color: string = colors.navy): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: `${offset}px ${offset}px 0px ${color}` } as unknown as ViewStyle,
    ios: { shadowColor: color, shadowOffset: { width: offset, height: offset }, shadowOpacity: 1, shadowRadius: 0 },
    default: { elevation: offset },
  }) as ViewStyle;
}

// Soft ambient shadow for the white content cards.
export function softShadow(): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: '0 2px 18px rgba(15,23,42,0.06)' } as unknown as ViewStyle,
    ios: { shadowColor: '#0F172A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.06, shadowRadius: 18 },
    default: { elevation: 2 },
  }) as ViewStyle;
}
