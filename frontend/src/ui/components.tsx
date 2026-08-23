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
import { colors, popShadow, radius, softShadow, space, type } from './theme';

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
// Cream canvas with a centered content column. The top nav is rendered by the (app) layout,
// so Screen only owns scrolling + padding.
export function Screen({
  children,
  scroll = true,
  contentStyle,
}: {
  children: ReactNode;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
}) {
  const inner = <View style={[styles.inner, contentStyle]}>{children}</View>;
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

// ---------- SoftCard (white content surface) ----------
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

// ---------- PopCard (bordered emphasis surface) ----------
export function PopCard({
  children,
  color = colors.card,
  style,
  offset = 3,
}: {
  children: ReactNode;
  color?: string;
  style?: StyleProp<ViewStyle>;
  offset?: number;
}) {
  return <View style={[styles.popCard, { backgroundColor: color }, popShadow(offset), style]}>{children}</View>;
}

// ---------- PopButton ----------
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
const BG: Record<ButtonVariant, string> = {
  primary: colors.orange,
  secondary: colors.white,
  ghost: 'transparent',
  danger: colors.white,
};
const FG: Record<ButtonVariant, string> = {
  primary: colors.white,
  secondary: colors.navy,
  ghost: colors.navy,
  danger: colors.red,
};

export function PopButton({
  label,
  onPress,
  variant = 'primary',
  loading,
  disabled,
  style,
  full,
  small,
}: {
  label: string;
  onPress?: PressableProps['onPress'];
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  full?: boolean;
  small?: boolean;
}) {
  const [pressed, setPressed] = useState(false);
  const off = disabled || loading;
  const ghost = variant === 'ghost';
  return (
    <Pressable
      onPress={onPress}
      onPressIn={() => setPressed(true)}
      onPressOut={() => setPressed(false)}
      disabled={off}
      style={[
        styles.btn,
        small && styles.btnSmall,
        { backgroundColor: BG[variant] },
        ghost ? styles.btnGhost : popShadow(pressed ? 1 : 3),
        pressed && !ghost ? { transform: [{ translateX: 2 }, { translateY: 2 }] } : null,
        full && styles.btnFull,
        off && styles.btnOff,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={FG[variant]} />
      ) : (
        <Text style={[styles.btnText, small && styles.btnTextSmall, { color: FG[variant] }]}>{label}</Text>
      )}
    </Pressable>
  );
}

// ---------- Chip ----------
export function Chip({
  label,
  active,
  onPress,
  color = colors.orange,
}: {
  label: string;
  active?: boolean;
  onPress?: () => void;
  color?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.chip, active ? { backgroundColor: color, borderColor: color } : { backgroundColor: colors.white, borderColor: colors.navy }]}
    >
      <Text style={[styles.chipText, { color: active ? colors.white : colors.navy }]}>{label}</Text>
    </Pressable>
  );
}

// ---------- Badge ----------
export function Badge({ label, bg, fg, outline }: { label: string; bg: string; fg: string; outline?: boolean }) {
  return (
    <View style={[styles.badge, { backgroundColor: outline ? 'transparent' : bg, borderColor: fg, borderWidth: outline ? 1.5 : 0 }]}>
      <Text style={[styles.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

// ---------- Field ----------
export function Field({
  label,
  hint,
  style,
  ...props
}: { label?: string; hint?: string } & TextInputProps) {
  return (
    <View style={styles.field}>
      {!!label && <Text style={styles.fieldLabel}>{label}</Text>}
      <TextInput
        placeholderTextColor={colors.muted}
        style={[styles.input, props.multiline && styles.inputMultiline, style]}
        {...props}
      />
      {!!hint && <Text style={styles.fieldHint}>{hint}</Text>}
    </View>
  );
}

// ---------- ProgressBar ----------
export function ProgressBar({ value, color = colors.orange }: { value: number; color?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <View style={styles.track}>
      <View style={[styles.fill, { width: `${pct}%`, backgroundColor: color }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.cream },
  scrollContent: { flexGrow: 1, paddingBottom: space.xxl },
  inner: { padding: space.lg, gap: space.lg, width: '100%', maxWidth: 820, alignSelf: 'center' },

  softCard: { borderRadius: radius.xl, padding: space.xl },
  popCard: { borderWidth: 2, borderColor: colors.navy, borderRadius: radius.lg, padding: space.lg },

  btn: {
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.pill,
    paddingVertical: 12,
    paddingHorizontal: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnSmall: { paddingVertical: 8, paddingHorizontal: 14 },
  btnGhost: { borderColor: 'transparent' },
  btnFull: { alignSelf: 'stretch' },
  btnOff: { opacity: 0.5 },
  btnText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 15 },
  btnTextSmall: { fontSize: 13 },

  chip: { borderWidth: 2, borderRadius: radius.pill, paddingVertical: 7, paddingHorizontal: 14 },
  chipText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13 },

  badge: { borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 10, alignSelf: 'flex-start' },
  badgeText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 10, letterSpacing: 0.5 },

  field: { gap: 6 },
  fieldLabel: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 11, color: colors.muted, letterSpacing: 0.6 },
  input: {
    borderWidth: 1,
    borderColor: colors.borderSoft,
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    fontSize: 15,
    color: colors.ink,
    backgroundColor: colors.lavender,
  },
  inputMultiline: { minHeight: 130, textAlignVertical: 'top' },
  fieldHint: { fontFamily: 'PlusJakartaSans_400Regular', fontSize: 12, color: colors.muted, textAlign: 'right' },

  track: { height: 10, borderRadius: radius.pill, backgroundColor: colors.lavender, borderWidth: 1, borderColor: colors.borderSoft, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: radius.pill },
});
