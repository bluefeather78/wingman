import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  Modal,
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
import Svg, { Circle, Rect } from 'react-native-svg';
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
  scrollRef,
}: {
  children: ReactNode;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
  maxWidth?: number;
  scrollRef?: React.Ref<ScrollView>;
}) {
  const inner = <View style={[styles.inner, { maxWidth }, contentStyle]}>{children}</View>;
  if (!scroll) return <View style={styles.screen}>{inner}</View>;
  return (
    <ScrollView
      ref={scrollRef}
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
// `hoverTint`: Home base and My Vibe treat every card-soft as a hover-highlighted container
// (#page-home/#page-profile .card-soft:hover { background-color: #FBF3E9 } in the live app) —
// other pages' cards stay plain, so this is opt-in per instance rather than baked into the
// base style.
// `onPress` makes the whole card the same affordance as its own CTA (Home Base's cards each
// open the thing they summarise). Inner buttons must stopPropagation() — on RN-web a nested
// Pressable's click bubbles to this one, which would fire both destinations.
export function SoftCard({
  children,
  style,
  color = colors.card,
  onLayout,
  hoverTint,
  onPress,
}: {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  color?: string;
  onLayout?: React.ComponentProps<typeof View>['onLayout'];
  hoverTint?: boolean;
  onPress?: PressableProps['onPress'];
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <Pressable
      onHoverIn={hoverTint ? () => setHovered(true) : undefined}
      onHoverOut={hoverTint ? () => setHovered(false) : undefined}
      onPress={onPress}
      style={[
        styles.softCard,
        { backgroundColor: hoverTint && hovered ? '#FBF3E9' : color },
        softShadow(),
        onPress ? styles.clickable : null,
        style,
      ]}
      onLayout={onLayout}
    >
      {children}
    </Pressable>
  );
}

// ---------- Hover/press "pop" interaction ----------
// Every bordered, hard-offset-shadow surface lifts on hover and depresses on press in the
// live app (styles.css .pop-card:hover / .pop-btn:hover+:active) — Pressable's
// onHoverIn/onHoverOut only fire on web (mouse), so `hovered` simply never turns true on
// native, which is correct: touch has no hover equivalent there.
export function usePopInteraction(baseOffset: number, color: string, lift: number) {
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);
  const offset = pressed ? Math.max(baseOffset - lift * 2, 1) : hovered ? baseOffset + lift : baseOffset;
  const shift = pressed ? lift * 2 : hovered ? -lift : 0;
  return {
    hovered,
    pressed,
    handlers: {
      onHoverIn: () => setHovered(true),
      onHoverOut: () => { setHovered(false); setPressed(false); },
      onPressIn: () => setPressed(true),
      onPressOut: () => setPressed(false),
    },
    shadowStyle: [
      popShadow(offset, color),
      shift ? { transform: [{ translateX: shift }, { translateY: shift }] } : null,
    ] as StyleProp<ViewStyle>,
  };
}

// ---------- PopCard (pop-card: 3px navy border + 4px hard offset shadow) ----------
export function PopCard({
  children,
  color = colors.card,
  style,
  offset = 4,
  borderColor = colors.navy,
  borderWidth = 3,
  onHoverChange,
}: {
  children: ReactNode;
  color?: string;
  style?: StyleProp<ViewStyle>;
  offset?: number;
  borderColor?: string;
  borderWidth?: number;
  onHoverChange?: (hovered: boolean) => void; // fires alongside the internal hover-lift
}) {
  const { handlers, shadowStyle } = usePopInteraction(offset, borderColor, 2);
  const composed = onHoverChange
    ? {
        ...handlers,
        onHoverIn: () => { handlers.onHoverIn(); onHoverChange(true); },
        onHoverOut: () => { handlers.onHoverOut(); onHoverChange(false); },
      }
    : handlers;
  return (
    <Pressable
      {...composed}
      style={[
        styles.popCard,
        { backgroundColor: color, borderColor, borderWidth },
        shadowStyle,
        style,
      ]}
    >
      {children}
    </Pressable>
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
  const off = disabled || loading;
  const ghost = variant === 'ghost';
  const { handlers, shadowStyle } = usePopInteraction(3, shadowColor ?? colors.navy, 1);
  // The live app's rule: pill CTAs set `border: none` inline, but square (rounded-xl)
  // orange buttons keep .pop-btn's 2px navy border. Measured via computed-style diff.
  const border = BORDER[variant] ?? (square && (variant === 'primary' || variant === 'primaryDeep') ? colors.navy : null);
  return (
    <Pressable
      onPress={onPress}
      {...(off ? null : handlers)}
      disabled={off}
      style={[
        styles.btn,
        small && styles.btnSmall,
        square && styles.btnSquare,
        { backgroundColor: BG[variant] },
        border ? { borderWidth: 2, borderColor: border } : null,
        ghost ? null : shadowStyle,
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

// ---------- Review status badge (check_reviews.py's review_status / review_summary) ----------
// Click-to-reveal, ported from the retired SPA's reviewBadgeHTML()/toggleReviewInfo(): the pill
// alone signals the verdict, and the full review_summary appears only once the student taps it,
// as a floating popover anchored to the pill. Absolutely positioned on purpose, so it OVERLAPS
// the card content below rather than making the card taller and shoving every card after it
// down the page.
//
// The RN port rendered these as inert MiniBadges, which dropped the summary with no way to
// reach it. That is the half worth keeping: the badge is check_reviews.py's verdict and the
// summary is the evidence behind it, and a verdict a student cannot interrogate is exactly what
// the two-phase review agent exists to avoid. `negative` was dropped in that port too and is
// restored here — "Reported issues" is the one verdict a student most needs to see.
export const REVIEW_STATUS_META: Record<string, { label: string; bg: string; fg: string }> = {
  positive: { label: 'Well reviewed', bg: colors.emerald100, fg: colors.emerald900 },
  mixed: { label: 'Mixed reviews', bg: '#FFEDD5', fg: '#7C2D12' },
  negative: { label: 'Reported issues', bg: '#FFE4E6', fg: '#881337' },
  // insufficient_data / null / undefined: no independent evidence either way — nothing worth
  // surfacing, so no badge at all rather than a hollow "insufficient data" pill.
};

// `open`/`onToggle` are lifted to the screen rather than held here so only one popover can be
// open at a time across a list of cards — the same rule toggleReviewInfo() enforced by closing
// every other panel before opening one.
//
// STACKING — this popover cannot lift itself, and the reason is a react-native-web quirk worth
// knowing before touching any overlay in this app. RN-web renders every <View> as
// `position: relative; z-index: 0` — not `z-index: auto`. A z-index of 0 on a POSITIONED
// element creates a stacking context, so each row between this badge and the card traps
// whatever is inside it: the popover's own z-index only ranks it among its siblings inside the
// badge row, and it is the BADGE ROW that then competes with the card's later children. Those
// are all z-index 0 too, and at an equal z-index the later sibling paints on top — so the
// popover rendered UNDER the card's own "WHY IT FITS" text and meta pills no matter how high
// its z-index went. Every ancestor up to the card therefore carries a raise of its own; see
// cardTopRow/badgeRow in finder.tsx and cardTop/badgeRow in tracker.tsx. Do not "simplify"
// those away, and add the same raise to any new screen that renders a ReviewBadge.
export function ReviewBadge({
  status,
  summary,
  open,
  onToggle,
}: {
  status?: string | null;
  summary?: string | null;
  open: boolean;
  onToggle: () => void;
}) {
  const meta = status ? REVIEW_STATUS_META[status] : undefined;
  if (!meta) return null;
  return (
    <View style={[styles.reviewWrap, open && styles.reviewWrapOpen]}>
      <Pressable
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`${meta.label}. Tap to see why.`}
        accessibilityHint="Shows the independent-review summary for this opportunity"
      >
        <View style={[styles.miniBadge, styles.reviewBadge, { backgroundColor: meta.bg }]}>
          <Text style={[styles.miniBadgeText, { color: meta.fg }]}>{meta.label}</Text>
        </View>
      </Pressable>
      {open && (
        // A press inside the panel closes it too, and — more importantly — never reaches the
        // card underneath, which on the Quest Log would otherwise open the opportunity's link.
        <Pressable style={[styles.reviewPopover, popShadow(4)]} onPress={onToggle}>
          <Text style={styles.reviewPopoverText}>{summary || 'No further detail available.'}</Text>
        </Pressable>
      )}
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
// A task carries one state MORE than an opportunity does: "Not Needed", the student saying
// this step does not apply to them. It is a state rather than a delete because the checklist
// is shared catalog data and is re-pulled on every refresh — a removed task would come
// straight back, and the control would read as broken. Kept off OppStatus deliberately:
// computeProgressStatus can never return it, and widening that union would force every
// opportunity-side lookup to handle a case it does not have.
export type TaskStatus = OppStatus | 'not_needed';
export const ACTION_ITEM_STATUS_LABEL: Record<TaskStatus, string> = {
  not_started: 'Not Started',
  in_progress: 'In Progress',
  completed: 'Completed',
  not_needed: 'Not Needed',
};
// OPPORTUNITY pills got new styling (2026-08-24): an OUTLINED pill with a leading dot,
// all three of the border, the dot and the label carrying the status accent. The TASK
// pills (the action-item state pills, including the tappable ones that cycle a task)
// keep the original solid-fill + navy-border look on purpose.
const OPP_PILL: Record<OppStatus, { accent: string }> = {
  in_progress: { accent: colors.statusNowFg },
  not_started: { accent: colors.statusFutureFg },
  completed: { accent: colors.statusPastFg },
};
const TASK_PILL: Record<TaskStatus, { bg: string; fg: string }> = {
  not_started: { bg: colors.peach, fg: colors.statusPastFg },
  in_progress: { bg: colors.statusFutureBg, fg: colors.statusFutureFg },
  completed: { bg: colors.statusNowBg, fg: colors.statusNowFg },
  // Deliberately the only neutral one: "not needed" is the student stepping a task out of
  // the way, not an achievement, and giving it a status colour would read as progress.
  not_needed: { bg: colors.slate200, fg: colors.slate500 },
};
export function StatusPill({ status, kind = 'opp', label, onPress }: { status: TaskStatus; kind?: 'opp' | 'task'; label?: string; onPress?: () => void }) {
  // 'not_needed' exists only on the task side; nothing can hand it to an opportunity pill,
  // but narrowing here beats indexing an OppStatus table with a key it has no entry for.
  const oppStatus: OppStatus = status === 'not_needed' ? 'not_started' : status;
  const text = label ?? (kind === 'opp' ? PROGRESS_STATUS_LABEL[oppStatus] : ACTION_ITEM_STATUS_LABEL[status]);
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);

  // Opportunity pills: outlined + dot, never tappable.
  if (kind === 'opp') {
    const { accent } = OPP_PILL[oppStatus];
    return (
      <View style={[styles.statusPill, styles.statusPillOutlined, { borderColor: accent }]}>
        <View style={[styles.statusPillDot, { backgroundColor: accent }]} />
        <Text style={[styles.statusPillText, { color: accent }]}>{text}</Text>
      </View>
    );
  }

  // Task pills: original solid-fill + navy-border look.
  const c = TASK_PILL[status];
  if (!onPress) {
    return (
      <View style={[styles.statusPill, { backgroundColor: c.bg }]}>
        <Text style={[styles.statusPillText, { color: c.fg }]}>{text}</Text>
      </View>
    );
  }
  // A tappable pill cycles the task's state, so it has to read as a control rather than a
  // label: it carries a small pop shadow at rest, lifts on hover and depresses on press —
  // the same language PopButton uses, scaled down to pill size.
  const offset = pressed ? 0 : hovered ? 3 : 2;
  const shift = pressed ? 2 : hovered ? -1 : 0;
  return (
    <Pressable
      onPress={onPress}
      onHoverIn={() => setHovered(true)}
      onHoverOut={() => {
        setHovered(false);
        setPressed(false);
      }}
      onPressIn={() => setPressed(true)}
      onPressOut={() => setPressed(false)}
      accessibilityRole="button"
      accessibilityHint="Cycles this task between not started, in progress and completed"
      style={[
        styles.statusPill,
        styles.statusPillPressable,
        { backgroundColor: c.bg },
        popShadow(offset, colors.navy),
        shift ? { transform: [{ translateX: shift }, { translateY: shift }] } : null,
      ]}
    >
      <Text style={[styles.statusPillText, { color: c.fg }]}>{text}</Text>
    </Pressable>
  );
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

// ---------- Wingman logo (favicon.svg, drawn as real SVG) ----------
// FOUR orange bars rising left→right, plus a glowing yellow dot above the tallest bar
// (a 35%-opacity halo behind a solid dot). Geometry is favicon.svg's 100-unit viewBox
// verbatim: bars at x 24/38/52/66, width 10 (so pitch 14, gap 4), bottom edge y=76,
// heights 16/24/32/40 — an even 8-unit step per bar. Dot at (71,32) r 6.5, halo r 13.
// Colors are the file's own #F97316 / #FACC15.
//
// This is drawn with react-native-svg, NOT with absolutely-positioned Views. The View
// version put every edge on a fractional pixel (at size 32 the bars start at 7.68 /
// 12.16 / 16.64 / 21.12 and are 3.2 wide), and both RN-web and native snap a View's box
// to whole device pixels — so the four identical 4-unit gaps rendered as a mix of 1px
// and 2px and the bars visibly failed to sit on an even grid. An SVG renderer
// antialiases fractional geometry instead of snapping it, so the grid survives at any
// size. Keep the numbers below in sync with favicon.svg — they are the same mark.
const LOGO_BARS: Array<[x: number, y: number, h: number]> = [
  [24, 60, 16],
  [38, 52, 24],
  [52, 44, 32],
  [66, 36, 40],
];

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 100 100">
      <Circle cx={71} cy={32} r={13} fill="#FACC15" opacity={0.35} />
      {LOGO_BARS.map(([x, y, h]) => (
        <Rect key={x} x={x} y={y} width={10} height={h} rx={2} fill="#F97316" />
      ))}
      <Circle cx={71} cy={32} r={6.5} fill="#FACC15" />
    </Svg>
  );
}

// ---------- Right-hand slide-in drawer ----------
// The live app's drawers (#profilePanel, .story-drawer) slide in from the right —
// translate-x-full → 0 over ~300ms with a fading scrim. RN's Modal only fades or slides
// from the bottom, so this animates the panel itself.
export function RightDrawer({
  open,
  onClose,
  width = 320,
  duration = 300,
  panelStyle,
  children,
}: {
  open: boolean;
  onClose: () => void;
  width?: number;
  duration?: number;
  panelStyle?: StyleProp<ViewStyle>;
  children: ReactNode;
}) {
  const [mounted, setMounted] = useState(open);
  const slide = useRef(new Animated.Value(open ? 0 : 1)).current; // 0 = shown, 1 = off-screen

  const animateTo = useCallback(
    (to: number, done?: () => void) => {
      Animated.timing(slide, {
        toValue: to,
        duration,
        easing: to === 0 ? Easing.out(Easing.cubic) : Easing.in(Easing.cubic),
        useNativeDriver: false,
      }).start(done);
    },
    [slide, duration],
  );

  useEffect(() => {
    if (open) {
      setMounted(true);
      slide.setValue(1);
      requestAnimationFrame(() => animateTo(0));
    } else if (mounted) {
      animateTo(1, () => setMounted(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!mounted) return null;
  const translateX = slide.interpolate({ inputRange: [0, 1], outputRange: [0, width] });
  const scrimOpacity = slide.interpolate({ inputRange: [0, 1], outputRange: [1, 0] });

  return (
    <Modal visible transparent animationType="none" onRequestClose={onClose}>
      <Animated.View style={[styles.drawerScrim, { opacity: scrimOpacity }]}>
        <Pressable style={styles.drawerScrimPress} onPress={onClose} />
      </Animated.View>
      <Animated.View style={[styles.drawerPanel, { width }, { transform: [{ translateX }] }, panelStyle]}>
        {children}
      </Animated.View>
    </Modal>
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
  // .pop-btn is font-weight 700, text-base (16/24). Small buttons are text-sm (14/20).
  btnText: { fontFamily: fonts.bodyBold, fontSize: 16, lineHeight: 24 },
  btnTextSmall: { fontSize: 14, lineHeight: 20 },

  miniBadge: { borderRadius: radius.pill, paddingVertical: 4, paddingHorizontal: 12, alignSelf: 'flex-start' },
  miniBadgeText: { fontFamily: fonts.bodyXBold, fontSize: 10, letterSpacing: 0.8, textTransform: 'uppercase' },
  reviewWrap: { position: 'relative', alignSelf: 'flex-start' },
  // Only the open one is raised, so a closed badge never steals presses from the card it sits on.
  reviewWrapOpen: { zIndex: 40 },
  reviewBadge: { borderWidth: 2, borderColor: colors.slate900 },
  reviewPopover: {
    position: 'absolute',
    top: '100%',
    left: 0,
    marginTop: 6,
    minWidth: 220,
    maxWidth: 320,
    backgroundColor: colors.white,
    borderWidth: 2,
    borderColor: colors.slate900,
    borderRadius: radius.md,
    padding: 12,
  },
  reviewPopoverText: { fontFamily: fonts.bodyMed, fontSize: 12, lineHeight: 18, color: colors.slate500 },

  statusPill: { borderWidth: 2, borderColor: colors.navy, borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 10, alignSelf: 'flex-start' },
  statusPillOutlined: { flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1.5, backgroundColor: colors.white, paddingVertical: 4, paddingHorizontal: 11 },
  statusPillPressable: { cursor: 'pointer' },
  statusPillDot: { width: 6, height: 6, borderRadius: 3 },
  clickable: { cursor: 'pointer' },
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

  drawerScrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(15,23,42,0.4)' },
  drawerScrimPress: { flex: 1 },
  drawerPanel: { position: 'absolute', top: 0, bottom: 0, right: 0, backgroundColor: colors.white, maxWidth: '100%' },

  vibeField: { backgroundColor: colors.white, borderWidth: 2, borderColor: colors.lavender, borderRadius: radius.lg, paddingVertical: 14, paddingHorizontal: 16 },
  vibeLabel: { fontFamily: fonts.bodyXBold, fontSize: 10, color: colors.muted, letterSpacing: 0.3, textTransform: 'uppercase', marginBottom: 6 },
});
