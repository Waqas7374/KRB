import React, { type ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type StyleProp,
  type TextInputProps,
  type ViewStyle,
} from "react-native";

import type { Tone } from "../core/labels";

/**
 * A small kit built to the field rules (docs/06 §2): 48 dp touch targets, 16 pt text at least,
 * high contrast for daylight, nothing that needs two hands.
 */
export const colors = {
  bg: "#ffffff",
  surface: "#f4f6f8",
  text: "#111827",
  muted: "#4b5563",
  border: "#c7ccd4",
  primary: "#0b4f9c",
  primaryText: "#ffffff",
  good: "#0a6b34",
  warn: "#8a5300",
  bad: "#b00020",
};

export const TOUCH = 48;

export function Button({
  title,
  onPress,
  kind = "primary",
  busy,
  disabled,
  style,
}: {
  title: string;
  onPress: () => void;
  kind?: "primary" | "secondary" | "danger";
  busy?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const inactive = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      disabled={inactive}
      onPress={onPress}
      style={[
        styles.button,
        kind === "primary" && { backgroundColor: colors.primary },
        kind === "danger" && { backgroundColor: colors.bad },
        kind === "secondary" && { backgroundColor: colors.bg, borderWidth: 2, borderColor: colors.primary },
        inactive && { opacity: 0.5 },
        style,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={kind === "secondary" ? colors.primary : colors.primaryText} />
      ) : (
        <Text style={[styles.buttonText, kind === "secondary" && { color: colors.primary }]}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Chip({
  label,
  selected,
  onPress,
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: Boolean(selected) }}
      onPress={onPress}
      style={[styles.chip, selected && { backgroundColor: colors.primary, borderColor: colors.primary }]}
    >
      <Text style={[styles.chipText, selected && { color: colors.primaryText }]}>{label}</Text>
    </Pressable>
  );
}

export function Field({
  label,
  error,
  hint,
  ...input
}: { label: string; error?: string | undefined; hint?: string } & TextInputProps) {
  return (
    <View style={{ marginBottom: 14 }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor="#8a93a0"
        style={[styles.input, error ? { borderColor: colors.bad } : null]}
        {...input}
      />
      {error ? (
        <Text style={styles.error} accessibilityRole="alert">
          {error}
        </Text>
      ) : hint ? (
        <Text style={styles.hint}>{hint}</Text>
      ) : null}
    </View>
  );
}

export function StatusPill({ text, tone }: { text: string; tone: Tone }) {
  const color = tone === "good" ? colors.good : tone === "warn" ? colors.warn : tone === "bad" ? colors.bad : colors.muted;
  return (
    <View style={[styles.pill, { borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{text}</Text>
    </View>
  );
}

export function Banner({ tone, children }: { tone: Tone; children: ReactNode }) {
  const color = tone === "good" ? colors.good : tone === "warn" ? colors.warn : tone === "bad" ? colors.bad : colors.muted;
  return (
    <View style={[styles.banner, { borderColor: color }]}>
      <Text style={[styles.bannerText, { color }]}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  button: { minHeight: TOUCH, borderRadius: 8, alignItems: "center", justifyContent: "center", paddingHorizontal: 18 },
  buttonText: { color: colors.primaryText, fontSize: 17, fontWeight: "700" },
  chip: {
    minHeight: 44,
    borderRadius: 22,
    borderWidth: 2,
    borderColor: colors.border,
    paddingHorizontal: 16,
    justifyContent: "center",
    marginRight: 8,
    marginBottom: 8,
    backgroundColor: colors.bg,
  },
  chipText: { fontSize: 16, color: colors.text, fontWeight: "600" },
  label: { fontSize: 16, fontWeight: "700", color: colors.text, marginBottom: 6 },
  input: {
    minHeight: TOUCH,
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: 12,
    fontSize: 18,
    color: colors.text,
    backgroundColor: colors.bg,
  },
  error: { color: colors.bad, fontSize: 15, marginTop: 4 },
  hint: { color: colors.muted, fontSize: 14, marginTop: 4 },
  pill: { borderWidth: 2, borderRadius: 14, paddingHorizontal: 10, paddingVertical: 3, alignSelf: "flex-start" },
  pillText: { fontSize: 14, fontWeight: "700" },
  banner: { borderWidth: 2, borderRadius: 8, padding: 12, marginBottom: 12, backgroundColor: colors.surface },
  bannerText: { fontSize: 16, fontWeight: "600" },
});
