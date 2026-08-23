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
import { SafeAreaView } from 'react-native-safe-area-context';
import { colors, popShadow, radius, space, type } from './theme';

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
// Cream canvas + safe area. `scroll` wraps children in a ScrollView with comfortable padding.
export function Screen({
  children,
  scroll = true,
  contentStyle,
}: {
  children: ReactNode;
  scroll?: boolean;
  contentStyle?: StyleProp<ViewStyle>;
}) {
  const inner = (
    <View style={[styles.screenInner, contentStyle]}>{children}</View>
  );
  return (
    <SafeAreaView style={styles.screen} edges={['top', 'left', 'right']}>
      {scroll ? (
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          {inner}
        </ScrollView>
      ) : (
        inner
      )}
    </SafeAreaView>
  );
}

// ---------- PopCard ----------
export function PopCard({
  children,
  color = colors.card,
  style,
  offset = 4,
}: {
  children: ReactNode;
  color?: string;
  style?: StyleProp<ViewStyle>;
  offset?: number;
}) {
  return (
    <View style={[styles.popCard, { backgroundColor: color }, popShadow(offset), style]}>
      {children}
    </View>
  );
}

// ---------- PopButton ----------
type ButtonVariant = 'primary' | 'secondary' | 'accent' | 'purple' | 'ghost' | 'danger';
const BUTTON_BG: Record<ButtonVariant, string> = {
  primary: colors.lime,
  secondary: colors.white,
  accent: colors.orange,
  purple: colors.purple,
  ghost: 'transparent',
  danger: colors.white,
};
const BUTTON_FG: Record<ButtonVariant, string> = {
  primary: colors.ink,
  secondary: colors.navy,
  accent: colors.white,
  purple: colors.white,
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
}: {
  label: string;
  onPress?: PressableProps['onPress'];
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  full?: boolean;
}) {
  const [pressed, setPressed] = useState(false);
  const isOff = disabled || loading;
  const ghost = variant === 'ghost';
  return (
    <Pressable
      onPress={onPress}
      onPressIn={() => setPressed(true)}
      onPressOut={() => setPressed(false)}
      disabled={isOff}
      style={[
        styles.btn,
        { backgroundColor: BUTTON_BG[variant] },
        ghost ? styles.btnGhost : popShadow(pressed ? 1 : 3),
        pressed && !ghost ? { transform: [{ translateX: 2 }, { translateY: 2 }] } : null,
        full && styles.btnFull,
        isOff && styles.btnOff,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={BUTTON_FG[variant]} />
      ) : (
        <Text style={[styles.btnText, { color: BUTTON_FG[variant] }]}>{label}</Text>
      )}
    </Pressable>
  );
}

// ---------- Chip (selectable pill) ----------
export function Chip({
  label,
  active,
  onPress,
  color = colors.purple,
}: {
  label: string;
  active?: boolean;
  onPress?: () => void;
  color?: string;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.chip,
        { borderColor: colors.navy },
        active ? { backgroundColor: color } : { backgroundColor: colors.white },
      ]}
    >
      <Text style={[styles.chipText, { color: active ? colors.white : colors.navy }]}>{label}</Text>
    </Pressable>
  );
}

// ---------- Badge ----------
export function Badge({ label, bg, fg }: { label: string; bg: string; fg: string }) {
  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={[styles.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

// ---------- Field (labeled input) ----------
export function Field({
  label,
  style,
  hint,
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

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.cream },
  scrollContent: { flexGrow: 1 },
  screenInner: { padding: space.xl, gap: space.lg, width: '100%', maxWidth: 760, alignSelf: 'center' },

  popCard: { borderWidth: 3, borderColor: colors.navy, borderRadius: radius.lg, padding: space.lg },

  btn: {
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.md,
    paddingVertical: 13,
    paddingHorizontal: 20,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnGhost: { borderColor: 'transparent' },
  btnFull: { alignSelf: 'stretch' },
  btnOff: { opacity: 0.5 },
  btnText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 15 },

  chip: { borderWidth: 2, borderRadius: radius.pill, paddingVertical: 7, paddingHorizontal: 14 },
  chipText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13 },

  badge: { borderRadius: radius.pill, paddingVertical: 3, paddingHorizontal: 10, alignSelf: 'flex-start' },
  badgeText: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 11, letterSpacing: 0.3 },

  field: { gap: 6 },
  fieldLabel: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 12, color: colors.navy, letterSpacing: 0.4 },
  input: {
    borderWidth: 2,
    borderColor: colors.navy,
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: 14,
    fontFamily: 'PlusJakartaSans_400Regular',
    fontSize: 15,
    color: colors.ink,
    backgroundColor: colors.white,
  },
  inputMultiline: { minHeight: 120, textAlignVertical: 'top' },
  fieldHint: { fontFamily: 'PlusJakartaSans_400Regular', fontSize: 12, color: colors.muted },
});
