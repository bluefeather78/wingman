import { type ReactNode, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type PressableProps,
  type StyleProp,
  type TextInputProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { APP_MAX_WIDTH, colors, fonts, popShadow, radius, softShadow, space, type } from './theme';

// ---------- Text ----------
type TypeVariant = keyof typeof type;
export function Txt({
  variant = 'body',
  style,
  children,
  numberOfLines,
}: {
  variant?: TypeVariant;
  style?: StyleProp<TextStyle>;
  children: ReactNode;
  numberOfLines?: number;
}) {
  return (
    <Text style={[type[variant], style]} numberOfLines={numberOfLines}>
      {children}
    </Text>
  );
}

// ---------- Screen ----------
// Cream canvas with the app's centered column: max-w-4xl (896px incl. 16px side padding),
// 24px vertical rhythm (space-y-6), 32px top margin under the nav (mt-8).
export function Screen({
  children,
  scroll = true,
  contentStyle,
  maxWidth = APP_MAX_WIDTH,
}: {
  children: ReactNode;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  maxWidth?: number;
}) {
  const inner = <View style={[styles.inner, { maxWidth }, contentStyle]}>{children}</View>;
  if (!scroll) return <View style={styles.screen}>{inner}</View>;
  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.scrollContent}
      keyboardShouldPersistTaps="handled"
      showsVerticalScrollIndicator={false}
    >
      {inner}
    </ScrollView>
  );
}

// ---------- SoftCard (card-soft: white, 22px radius, soft ambient shadow, no border) ----------
export function SoftCard({
  children,
  style,
  color = colors.card,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  color?: string;
}) {
  return <View style={[styles.softCard, { backgroundColor: color }, softShadow(), style]}>{children}</View>;
}

// ---------- PopCard (pop-card: 3px navy border + 4px hard offset shadow) ----------
export function PopCard({
  children,
  color = colors.card,
  style,
  offset = 4,
  borderColor = colors.navy,
  borderWidth = 3,
}: {
  children: ReactNode;
  color?: string;
  style?: StyleProp<ViewStyle>;
  offset?: number;
  borderColor?: string;
  borderWidth?: number;
}) {
  return (
    <View
      style={[
        styles.popCard,
        { backgroundColor: color, borderColor, borderWidth },
        popShadow(offset, borderColor),
        style,
      ]}
    >
      {children}
    </View>
  );
}

// ---------- PopButton ----------
// Matches the live app's button family:
//  - primary: orange #f79256, NO border, pill, white extrabold text, navy pop shadow
//  - primaryDeep: same but #f4791d (landing / gradient-banner CTAs)
//  - secondary: white, 2px navy border, navy text (e.g. "Look for Fresh Finds")
//  - ink: white, 2px #1a2540 border, ink text (profile "Quick add", quiz options)
//  - danger: white, red text
type ButtonVariant = 'primary' | 'primaryDeep' | 'secondary' | 'ink' | 'ghost' | 'danger';
const BG: Record<ButtonVariant, string> = {
  primary: colors.orange,
  primaryDeep: colors.orangeDeep,
  secondary: colors.white,
  ink: colors.white,
  ghost: 'transparent',
  danger: colors.white,
};
const FG: Record<ButtonVariant, string> = {
  primary: colors.white,
  primaryDeep: colors.white,
  secondary: colors.navy,
  ink: colors.ink,
  ghost: colors.navy,
  danger: colors.red,
};
const BORDER: Record<ButtonVariant, string | null> = {
  primary: null,
  primaryDeep: null,
  secondary: colors.navy,
  ink: colors.ink,
  ghost: null,
  danger: colors.red,
};

export function PopButton({
  label,
  onPress,
  variant = 'primary',
  loading,
  disabled,
  style,
  textStyle,
  full,
  small,
  square,
  shadowColor,
}: {
  label: string;
  onPress?: PressableProps['onPress'];
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  full?: boolean;
  small?: boolean;
  square?: boolean; // rounded-xl (12px) instead of pill
  shadowColor?: string;
}) {
  const [pressed, setPressed] = useState(false);
  const off = disabled || loading;
  const ghost = variant === 'ghost';
  const border = BORDER[variant];
  return (
    <Pressable
      onPress={onPress}
      onPressIn={() => setPressed(true)}
      onPressOut={() => setPressed(false)}
      disabled={off}
      style={[
        styles.btn,
        small && styles.btnSmall,
        square && styles.btnSquare,
        { backgroundColor: BG[variant] },
        border ? { borderWidth: 2, borderColor: border } : null,
        ghost ? null : popShadow(pressed ? 1 : 3, shadowColor ?? colors.navy),
        pressed && !ghost ? { transform: [{ translateX: 2 }, { translateY: 2 }] } : null,
        full && styles.btnFull,
        off && styles.btnOff,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={FG[variant]} />
      ) : (
        <Text style={[styles.btnText, small && styles.btnTextSmall, { color: FG[variant] }, textStyle]}>{label}</Text>
      )}
    </Pressable>
  );
}

// ---------- Mini badge (the type / tier / review pills on result & tracker cards) ----------
// border-2 border-slate-900, text-[10px] uppercase, px-3 py-1, rounded-full.
export function MiniBadge({ label, bg, fg, borderColor = colors.slate900 }: { label: string; bg: string; fg: string; borderColor?: string | null }) {
  return (
    <View style={[styles.miniBadge, { backgroundColor: bg }, borderColor ? { borderWidth: 2, borderColor } : null]}>
      <Text style={[styles.miniBadgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

// ---------- Status pill (.status-pill — 2px navy border, 10px uppercase 800) ----------
export type OppStatus = 'not_started' | 'in_progress' | 'completed';
export const PROGRESS_STATUS_LABEL: Record<OppStatus, string> = {
  not_started: 'Future Event',
  in_progress: 'Happening Now',
  completed: 'Past Event',
};
export const ACTION_ITEM_STATUS_LABEL: Record<OppStatus, string> = {
  not_started: 'Not Started',
  in_progress: 'In Progress',
  completed: 'Completed',
};
const OPP_PILL: Record<OppStatus, { bg: string; fg: string }> = {
  in_progress: { bg: colors.statusNowBg, fg: colors.statusNowFg },
  not_started: { bg: colors.statusFutureBg, fg: colors.statusFutureFg },
  completed: { bg: colors.statusPastBg, fg: colors.statusPastFg },
};
const TASK_PILL: Record<OppStatus, { bg: string; fg: string }> = {
  not_started: { bg: colors.peach, fg: colors.statusPastFg },
  in_progress: { bg: colors.statusFutureBg, fg: colors.statusFutureFg },
  completed: { bg: colors.statusNowBg, fg: colors.statusNowFg },
};
export function StatusPill({ status, kind = 'opp', label, onPress }: { status: OppStatus; kind?: 'opp' | 'task'; label?: string; onPress?: () => void }) {
  const c = (kind === 'opp' ? OPP_PILL : TASK_PILL)[status];
  const text = label ?? (kind === 'opp' ? PROGRESS_STATUS_LABEL[status] : ACTION_ITEM_STATUS_LABEL[status]);
  const pill = (
    <View style={[styles.statusPill, { backgroundColor: c.bg }]}>
      <Text style={[styles.statusPillText, { color: c.fg }]}>{text}</Text>
    </View>
  );
  if (!onPress) return pill;
  return <Pressable onPress={onPress}>{pill}</Pressable>;
}

// Legacy Badge kept for compatibility with screens not yet reworked.
export function Badge({ label, bg, fg, outline }: { label: string; bg: string; fg: string; outline?: boolean }) {
  return (
    <View style={[styles.badge, { backgroundColor: outline ? 'transparent' : bg, borderColor: fg, borderWidth: outline ? 1.5 : 0 }]}>
      <Text style={[styles.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

// ---------- Field ----------
// Two input languages in the live app:
//  - "bordered" (login/register, chat input, intake): white bg, 2px slate-900 border, 12px radius
//  - "soft" (finder forms): #eef0fb bg, no border, 16px radius, Poppins
export function Field({
  label,
  hint,
  style,
  soft,
  ...props
}: { label?: string; hint?: string; soft?: boolean } & TextInputProps) {
  return (
    <View style={styles.field}>
      {!!label && <Text style={styles.fieldLabel}>{label}</Text>}
      <TextInput
        placeholderTextColor={soft ? colors.muted : colors.slate400}
        style={[soft ? styles.inputSoft : styles.input, props.multiline && styles.inputMultiline, style]}
        {...props}
      />
      {!!hint && <Text style={styles.fieldHint}>{hint}</Text>}
    </View>
  );
}

// ---------- Progress track (.progress-track: 22px tall, 2px navy border, pill, cream bg) ----------
export function ProgressTrack({ segments }: { segments: { pct: number; color: string }[] }) {
  return (
    <View style={styles.track}>
      {segments.map((s, i) => (
        <View key={i} style={{ width: `${s.pct}%`, backgroundColor: s.color, height: '100%' }} />
      ))}
    </View>
  );
}

export function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <Text style={styles.legendText}>{label}</Text>
    </View>
  );
}

// ---------- Circular icon button (.icon-btn: 30px, white, 2px navy border) ----------
export function IconBtn({ children, onPress, size = 30 }: { children: ReactNode; onPress?: () => void; size?: number }) {
  return (
    <Pressable onPress={onPress} style={[styles.iconBtn, { width: size, height: size, borderRadius: size / 2 }]}>
      {children}
    </Pressable>
  );
}

// ---------- Wingman logo (the favicon bar-chart glyph, drawn with Views) ----------
// Three orange bars rising left→right with a yellow accent dot on the tallest bar.
export function Logo({ size = 32 }: { size?: number }) {
  const u = size / 24; // favicon viewBox is 24x24
  const bar = (x: number, y: number, h: number) => (
    <View
      style={{
        position: 'absolute',
        left: x * u,
        top: y * u,
        width: 4 * u,
        height: h * u,
        borderRadius: u,
        backgroundColor: colors.orange,
      }}
    />
  );
  return (
    <View style={{ width: size, height: size }}>
      {bar(2, 14, 8)}
      {bar(9, 9, 13)}
      {bar(16, 4, 18)}
      <View
        style={{
          position: 'absolute',
          left: 15.5 * u,
          top: 0.5 * u,
          width: 5 * u,
          height: 5 * u,
          borderRadius: 2.5 * u,
          backgroundColor: colors.yellow300,
          borderWidth: Math.max(1, u),
          borderColor: colors.navy,
        }}
      />
    </View>
  );
}

// ---------- Vibe field (.vibe-field: white, 2px #eef0fb border, 16px radius) ----------
export function VibeField({ label, children, style }: { label: string; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[styles.vibeField, style]}>
      <Text style={styles.vibeLabel}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.cream },
  scrollContent: { flexGrow: 1, paddingBottom: 48 },
  inner: { paddingHorizontal: space.lg, paddingTop: space.xxl, gap: space.xl, width: '100%', alignSelf: 'center' },

  softCard: { borderRadius: radius.xl, padding: space.xl },
  popCard: { borderRadius: radius.lg, padding: space.lg },

  btn: {
    borderRadius: radius.pill,
    paddingVertical: 12,
    paddingHorizontal: 24,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
  },
  btnSmall: { paddingVertical: 10, paddingHorizontal: 16 },
  btnSquare: { borderRadius: radius.md },
  btnFull: { alignSelf: 'stretch' },
  btnOff: { opacity: 0.65 },
  btnText: { fontFamily: fonts.bodyXBold, fontSize: 15 },
  btnTextSmall: { fontSize: 13 },

  miniBadge: { borderRadius: radius.pill, paddingVertical: 4, paddingHorizontal: 12, alignSelf: 'flex-start' },
  miniBadgeText: { fontFamily: fonts.bodyXBold, fontSize: 10, letterSpacing: 0.8, textTransform: 'uppercase' },

  statusPill: { borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 10, alignSelf: 'flex-start' },
  statusPillText: { fontFamily: fonts.bodyXBold, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.2 },

  badge: { borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 10, alignSelf: 'flex-start' },
  badgeText: { fontFamily: fonts.bodyBold, fontSize: 10, letterSpacing: 0.5 },

  field: { gap: 8 },
  fieldLabel: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.slate500, letterSpacing: 0.6, textTransform: 'uppercase' },
  input: {
    borderWidth: 2,
    borderColor: colors.slate900,
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: 12,
    fontFamily: fonts.bodyMed,
    fontSize: 16,
    color: colors.slate900,
    backgroundColor: colors.white,
  },
  inputSoft: {
    borderWidth: 0,
    borderRadius: radius.lg,
    paddingVertical: 12,
    paddingHorizontal: 16,
    fontFamily: fonts.bodyMed,
    fontSize: 15,
    color: colors.ink,
    backgroundColor: colors.lavender,
  },
  inputMultiline: { minHeight: 160, textAlignVertical: 'top' },
  fieldHint: { fontFamily: fonts.bodyBold, fontSize: 10, color: colors.muted, textAlign: 'right' },

  track: {
    flexDirection: 'row',
    height: 22,
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.pill,
    backgroundColor: colors.cream,
    overflow: 'hidden',
    width: '100%',
  },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendText: { fontFamily: fonts.bodyBold, fontSize: 12, color: colors.navy },

  iconBtn: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.navy, alignItems: 'center', justifyContent: 'center' },

  vibeField: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.lavender, borderRadius: radius.lg, paddingVertical: 14, paddingHorizontal: 16 },
  vibeLabel: { fontFamily: fonts.bodyXBold, fontSize: 10, color: colors.muted, letterSpacing: 0.3, textTransform: 'uppercase', marginBottom: 6 },
});
