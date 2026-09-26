import React, { useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, Alert } from "react-native";
import * as Location from "expo-location";

export default function TruckEntryScreen() {
  const [material, setMaterial] = useState("Crush");
  const [truckNumber, setTruckNumber] = useState("");
  const [tonnage, setTonnage] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lng: number } | null>(null);
  const [error, setError] = useState("");

  async function captureLocation() {
    const { status } = await Location.requestForegroundPermissionsAsync();
    if (status !== "granted") {
      setError("Location permission is required to log a delivery");
      return;
    }
    const pos = await Location.getCurrentPositionAsync({});
    setCoords({ lat: pos.coords.latitude, lng: pos.coords.longitude });
  }

  function handleSave() {
    if (!truckNumber.trim() || !tonnage.trim() || !coords) {
      setError("Fill in truck number, tonnage, and capture location first");
      return;
    }
    setError("");
    // TODO: write to local SQLite (expo-sqlite) with a client-generated
    // UUID as the record id, status = "unverified". A background sync
    // task pushes queued rows to the FastAPI backend when connectivity
    // returns, and resolves the rate version server-side using the
    // entry's captured timestamp — not whatever rate is cached locally.
    Alert.alert("Saved locally", "This entry will sync when you're back online.");
    setTruckNumber("");
    setTonnage("");
    setCoords(null);
  }

  return (
    <View style={styles.container}>
      <Pressable style={styles.photoBox}>
        <Text style={{ fontSize: 22, color: "#aaa" }}>📷</Text>
        <Text style={{ fontSize: 11, color: "#aaa" }}>Capture photo</Text>
      </Pressable>

      <Text style={styles.label}>Material</Text>
      <View style={styles.pillRow}>
        {["Crush", "Sand", "Cement"].map((m) => (
          <Pressable
            key={m}
            onPress={() => setMaterial(m)}
            style={[styles.pill, material === m && styles.pillActive]}
          >
            <Text style={material === m ? styles.pillTextActive : styles.pillText}>{m}</Text>
          </Pressable>
        ))}
      </View>

      <Text style={styles.label}>Truck number</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. RJ14 GB 4021"
        value={truckNumber}
        onChangeText={setTruckNumber}
        autoCapitalize="characters"
      />

      <Text style={styles.label}>Tonnage</Text>
      <TextInput
        style={styles.input}
        placeholder="e.g. 12.5"
        keyboardType="decimal-pad"
        value={tonnage}
        onChangeText={setTonnage}
      />

      <Pressable style={styles.locationRow} onPress={captureLocation}>
        <Text style={{ color: coords ? "#1d9e75" : "#888", fontSize: 12 }}>
          {coords ? `● Location captured (${coords.lat.toFixed(4)}, ${coords.lng.toFixed(4)})` : "○ Tap to capture location"}
        </Text>
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={handleSave}>
        <Text style={styles.buttonText}>Save entry</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, backgroundColor: "#fff" },
  photoBox: {
    height: 90,
    borderWidth: 1,
    borderColor: "#ddd",
    borderStyle: "dashed",
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 16,
  },
  label: { fontSize: 12, color: "#666", marginBottom: 6, marginTop: 4 },
  pillRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  pill: { borderWidth: 1, borderColor: "#ddd", borderRadius: 20, paddingVertical: 6, paddingHorizontal: 14 },
  pillActive: { backgroundColor: "#222", borderColor: "#222" },
  pillText: { fontSize: 12, color: "#444" },
  pillTextActive: { fontSize: 12, color: "#fff" },
  input: {
    borderWidth: 1,
    borderColor: "#ddd",
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
    fontSize: 14,
  },
  locationRow: { paddingVertical: 10 },
  error: { color: "#c0392b", fontSize: 12, marginBottom: 10 },
  button: { backgroundColor: "#222", borderRadius: 8, padding: 14, alignItems: "center", marginTop: 12 },
  buttonText: { color: "#fff", fontSize: 14, fontWeight: "600" },
});
