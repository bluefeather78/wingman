import { Platform, type TextStyle, type ViewStyle } from 'react-native';

// BENTO & POP — design tokens matched 1:1 to the live app (styles.css + index.html Tailwind
// classes + the Claude Design "Wingman Design System" project tokens). Cream canvas, navy
// ink, ORANGE primary CTA, soft white content cards, and the "pop" hard offset shadow.
// Display face Space Grotesk, body Plus Jakarta Sans, Poppins for form fields.

export const colors = {
  cream: '#FBF8F3', // page background (body)
  page: '#F4F5F9',
  card: '#FFFFFF',
  lavender: '#EEF0FB', // finder inputs / view-tabs container
  navy: '#1D4E89', // brand navy: borders, nav bar, pop shadow, primary text
  navyDeep: '#0E1830',
  ink: '#1A2540', // navy-text headings
  inkSoft: '#4A6685',
  slate900: '#0F172A', // utility "ink" borders (chat, result cards, login inputs)
  slate500: '#64748B', // Tailwind slate-500 (secondary text)
  slate400: '#94A3B8',
  slate200: '#E2E8F0',
  slate100: '#F1F5F9',
  slate50: '#F8FAFC',
  orange: '#F79256', // PRIMARY CTA (buttons, nav active)
  orangeDeep: '#F4791D',
  teal: '#00B2CA', // avatar, Happening Now, focus outline
  mint: '#7DCFB6', // Future Event
  peach: '#FBD1A2', // Past Event / task Not Started
  lime: '#D7F542',
  lime100: '#ECFCCB', // "Updated today" pill bg (Tailwind lime-100)
  lime700: '#4D7C0F',
  purple: '#6A63E8',
  indigo: '#6366F1',
  indigo200: '#C7D2FE',
  indigo600: '#4F46E5',
  indigo700: '#4338CA',
  violet200: '#DDD6FE',
  violet900: '#4C1D95',
  yellow: '#F4B400',
  yellow300: '#FDE047', // landing badge / strong-fit pill (Tailwind yellow-300)
  amber300: '#FCD34D',
  amber50: '#FFFBEB',
  amber200: '#FDE68A',
  amber700: '#B45309',
  red: '#D64545',
  redSoft: '#FFE4E6',
  rose600: '#E11D48',
  emerald100: '#D1FAE5',
  emerald900: '#064E3B',
  muted: '#8A93A6', // gray-muted
  grayLight: '#C7CDDA',
  grayLighter: '#AAB3C9',
  hairline: '#D9DEEB',
  borderSoft: '#C9C9F0',
  white: '#FFFFFF',
  // Status pill palettes (styles.css .status-pill.*)
  statusNowBg: '#D8F0E9', statusNowFg: '#1A6E58',
  statusFutureBg: '#CDEAF2', statusFutureFg: '#00697A',
  statusPastBg: '#FCE9D0', statusPastFg: '#8A4A0E',
  // Dark CTA banner gradient (the only gradient in the product)
  bannerFrom: '#101C36',
  bannerTo: '#182750',
} as const;

export const fonts = {
  display: 'SpaceGrotesk_700Bold',
  displayMed: 'SpaceGrotesk_500Medium',
  body: 'PlusJakartaSans_400Regular',
  bodyMed: 'PlusJakartaSans_500Medium',
  bodySemi: 'PlusJakartaSans_600SemiBold',
  bodyBold: 'PlusJakartaSans_700Bold',
  bodyXBold: 'PlusJakartaSans_800ExtraBold',
} as const;

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;
// Radii used by the live app: 12 (rounded-xl), 16 (rounded-2xl / inputs), 22 (card-soft), 24 (rounded-3xl).
export const radius = { sm: 8, md: 12, lg: 16, xl: 22, xxl: 24, pill: 999 } as const;

// Tailwind-default sizes the live app renders with (text-xs .. text-3xl).
export const type = {
  hero: { fontFamily: fonts.display, fontSize: 30, lineHeight: 36, color: colors.navy } as TextStyle,
  h1: { fontFamily: fonts.display, fontSize: 24, lineHeight: 32, color: colors.navy } as TextStyle,
  h2: { fontFamily: fonts.display, fontSize: 20, lineHeight: 28, color: colors.navy } as TextStyle,
  h3: { fontFamily: fonts.display, fontSize: 18, lineHeight: 26, color: colors.ink } as TextStyle,
  body: { fontFamily: fonts.bodyMed, fontSize: 14, lineHeight: 22, color: colors.inkSoft } as TextStyle,
  bodyStrong: { fontFamily: fonts.bodyBold, fontSize: 14, lineHeight: 22, color: colors.ink } as TextStyle,
  small: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 17, color: colors.muted } as TextStyle,
  label: { fontFamily: fonts.bodyBold, fontSize: 12, lineHeight: 16, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' } as TextStyle,
} as const;

// The centered app column: max-w-4xl (896px) including 16px side padding.
export const APP_MAX_WIDTH = 896;
// The landing page column: max-w-[1100px].
export const LANDING_MAX_WIDTH = 1100;

// The signature "pop" shadow: a hard, un-blurred offset behind a bordered surface.
export function popShadow(offset = 3, color: string = colors.navy): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: `${offset}px ${offset}px 0px ${color}` } as unknown as ViewStyle,
    ios: { shadowColor: color, shadowOffset: { width: offset, height: offset }, shadowOpacity: 1, shadowRadius: 0 },
    default: { elevation: offset },
  }) as ViewStyle;
}

// Soft ambient shadow for the white content cards (card-soft).
export function softShadow(): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: '0 2px 18px rgba(15,23,42,0.06)' } as unknown as ViewStyle,
    ios: { shadowColor: '#0F172A', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.06, shadowRadius: 18 },
    default: { elevation: 2 },
  }) as ViewStyle;
}

// The nav pill's blue glow: 0 10px 25px -5px rgba(29,78,137,0.45).
export function navShadow(): ViewStyle {
  return Platform.select<ViewStyle>({
    web: { boxShadow: '0 10px 25px -5px rgba(29,78,137,0.45)' } as unknown as ViewStyle,
    ios: { shadowColor: colors.navy, shadowOffset: { width: 0, height: 10 }, shadowOpacity: 0.45, shadowRadius: 20 },
    default: { elevation: 8 },
  }) as ViewStyle;
}
