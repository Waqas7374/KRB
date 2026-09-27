import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useMemo } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import type { RootStackParamList } from "../shell/navigation";
import { useApp, useCatalogue, useDeliveries } from "../shell/services";
import { statusLabel } from "../core/labels";
import { Banner, Button, colors, StatusPill } from "../ui/kit";

type Props = NativeStackScreenProps<RootStackParamList, "Home">;

/** Site Home (docs/06 §2): today's count, the sync badge, and the one big button. */
export default function HomeScreen({ navigation }: Props) {
  const { profile, status, syncNow, signOut } = useApp();
  const deliveries = useDeliveries(100);
  const catalogue = useCatalogue();

  const today = new Date().toDateString();
  const todays = useMemo(
    () => deliveries.filter((d) => new Date(d.created_at).toDateString() === today),
    [deliveries, today],
  );
  const material = (id: string) => catalogue?.materials.find((m) => m.id === id)?.data.name ?? "Material";
  const unit = (id: string) => catalogue?.units.find((u) => u.id === id)?.data.code ?? "";

  const waiting = status?.pending ?? 0;
  const attention = status?.needsAttention ?? 0;
  const offline = status?.online === false;
  const empty = !catalogue || catalogue.sites.length === 0;

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.hello}>{profile?.full_name ?? "Site Ledger"}</Text>
          <Text style={styles.sub}>
            {todays.length} today · {status?.running ? "Syncing…" : offline ? "Offline" : "Online"}
          </Text>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Queue" onPress={() => navigation.navigate("Queue")} style={styles.badge}>
          <Text style={[styles.badgeText, attention > 0 && { color: colors.bad }]}>
            {attention > 0 ? `${attention} needs attention` : waiting > 0 ? `${waiting} pending` : "All synced"}
          </Text>
        </Pressable>
      </View>

      {status?.deviceRevoked && (
        <Banner tone="bad">This phone has been switched off by head office. Your entries are safe; contact them.</Banner>
      )}
      {offline && waiting > 0 && (
        <Banner tone="warn">You are offline. {waiting} {waiting === 1 ? "entry is" : "entries are"} saved on this phone and will send when you are back online.</Banner>
      )}
      {empty && !offline && (
        <Banner tone="warn">Loading the site's materials and vendors… If this stays, tap Sync now.</Banner>
      )}

      <Button
        title="+ New delivery"
        onPress={() => navigation.navigate("NewDelivery")}
        disabled={empty}
        style={styles.big}
      />

      <FlatList
        data={deliveries}
        keyExtractor={(d) => d.id}
        ListEmptyComponent={<Text style={styles.none}>No deliveries recorded on this phone yet.</Text>}
        renderItem={({ item }) => {
          const label = statusLabel(item);
          const line = item.payload.items[0];
          return (
            <Pressable
              accessibilityRole="button"
              onPress={() => navigation.navigate("DeliveryDetail", { id: item.id })}
              style={styles.row}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.plate}>{item.payload.truck_number ?? "No plate"}</Text>
                <Text style={styles.detail}>
                  {line ? `${material(line.material_id)} · ${Number(line.quantity)} ${unit(line.unit_id)}` : "—"}
                </Text>
                {item.server_number && <Text style={styles.number}>{item.server_number}</Text>}
              </View>
              <StatusPill text={label.text} tone={label.tone} />
            </Pressable>
          );
        }}
      />

      <View style={styles.footer}>
        <Button title="Sync now" kind="secondary" onPress={() => void syncNow()} busy={status?.running ?? false} style={{ flex: 1, marginRight: 8 }} />
        <Button title="Sign out" kind="secondary" onPress={() => void signOut()} style={{ flex: 1 }} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, padding: 16, paddingTop: 48 },
  header: { flexDirection: "row", alignItems: "center", marginBottom: 12 },
  hello: { fontSize: 22, fontWeight: "800", color: colors.text },
  sub: { fontSize: 16, color: colors.muted, marginTop: 2 },
  badge: { minHeight: 44, borderWidth: 2, borderColor: colors.border, borderRadius: 22, paddingHorizontal: 14, justifyContent: "center" },
  badgeText: { fontSize: 15, fontWeight: "700", color: colors.text },
  big: { minHeight: 64, marginBottom: 12 },
  none: { fontSize: 16, color: colors.muted, textAlign: "center", marginTop: 32 },
  row: { flexDirection: "row", alignItems: "center", minHeight: 64, borderBottomWidth: 1, borderColor: colors.border, paddingVertical: 8 },
  plate: { fontSize: 18, fontWeight: "800", color: colors.text },
  detail: { fontSize: 16, color: colors.muted },
  number: { fontSize: 14, color: colors.muted, fontFamily: "monospace" },
  footer: { flexDirection: "row", paddingTop: 8 },
});
