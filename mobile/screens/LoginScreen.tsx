import React, { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import type { RootStackParamList } from "../App";

type Props = NativeStackScreenProps<RootStackParamList, "Login">;

export default function LoginScreen({ navigation }: Props) {
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function handleSignIn() {
    if (!phone.trim() || !password.trim()) {
      setError("Enter phone number and password");
      return;
    }
    setError("");
    // TODO: replace with real auth call to FastAPI backend.
    // Role (site staff vs head office) should come back from the
    // server and drive which screen the user lands on next.
    navigation.replace("TruckEntry");
  }

  return (
    <View style={styles.container}>
      <View style={styles.badge}>
        <Text style={{ fontSize: 20 }}>🚚</Text>
      </View>
      <Text style={styles.title}>Site ledger</Text>
      <Text style={styles.hint}>Sign in to continue</Text>

      <Text style={styles.label}>Phone number</Text>
      <TextInput
        style={styles.input}
        placeholder="98XXXXXXXX"
        keyboardType="phone-pad"
        value={phone}
        onChangeText={setPhone}
      />

      <Text style={styles.label}>Password</Text>
      <TextInput
        style={styles.input}
        placeholder="••••••••"
        secureTextEntry
        value={password}
        onChangeText={setPassword}
      />

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={handleSignIn}>
        <Text style={styles.buttonText}>Sign in</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", padding: 24, backgroundColor: "#fff" },
  badge: {
    width: 48,
    height: 48,
    borderRadius: 12,
    backgroundColor: "#e6f1fb",
    alignItems: "center",
    justifyContent: "center",
    alignSelf: "center",
    marginBottom: 16,
  },
  title: { fontSize: 18, fontWeight: "600", textAlign: "center" },
  hint: { fontSize: 13, color: "#888", textAlign: "center", marginBottom: 24 },
  label: { fontSize: 12, color: "#666", marginBottom: 4 },
  input: {
    borderWidth: 1,
    borderColor: "#ddd",
    borderRadius: 8,
    padding: 10,
    marginBottom: 14,
    fontSize: 14,
  },
  error: { color: "#c0392b", fontSize: 12, marginBottom: 10 },
  button: { backgroundColor: "#222", borderRadius: 8, padding: 14, alignItems: "center" },
  buttonText: { color: "#fff", fontSize: 14, fontWeight: "600" },
});
