import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";

import { useApp } from "../shell/services";
import { ApiError, NetworkError } from "../core/api";
import { Banner, Button, colors, Field } from "../ui/kit";

/**
 * Phone number (or email) and password — the site staff sign in with the number they were given
 * (docs/06 §2). Signing in needs a connection the first time; after that the phone works
 * without one, and a session that lapses never costs anyone their unsent entries.
 */
export default function LoginScreen() {
  const { signIn, status } = useApp();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const waiting = (status?.pending ?? 0) + (status?.needsAttention ?? 0);

  async function submit() {
    setError(null);
    if (!identifier.trim() || !password) {
      setError("Enter your phone number and password.");
      return;
    }
    setBusy(true);
    try {
      await signIn(identifier, password);
    } catch (err) {
      if (err instanceof NetworkError) setError("No connection. You need one to sign in.");
      else if (err instanceof ApiError && err.status === 401) setError("That phone number or password is not right.");
      else if (err instanceof ApiError && err.status === 423) setError("This account is locked for a few minutes after too many tries.");
      else if (err instanceof ApiError) setError(err.message);
      else setError("Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Site Ledger</Text>
        <Text style={styles.subtitle}>Sign in to record deliveries</Text>
        {waiting > 0 && (
          <Banner tone="good">
            {waiting} {waiting === 1 ? "entry is" : "entries are"} saved on this phone and will be sent once you sign in.
          </Banner>
        )}
        {error && <Banner tone="bad">{error}</Banner>}
        <View>
          <Field
            label="Phone number or email"
            value={identifier}
            onChangeText={setIdentifier}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            textContentType="username"
          />
          <Field
            label="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            textContentType="password"
            onSubmitEditing={() => void submit()}
          />
          <Button title="Sign in" onPress={() => void submit()} busy={busy} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 24, paddingTop: 80 },
  title: { fontSize: 32, fontWeight: "800", color: colors.text },
  subtitle: { fontSize: 17, color: colors.muted, marginBottom: 28, marginTop: 4 },
});
