import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useEffect, useState } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";

import type { RootStackParamList } from "../shell/navigation";
import { useApp } from "../shell/services";
import type { OutboxOp } from "../core/types";
import { Banner, Button, colors, StatusPill } from "../ui/kit";

type Props = NativeStackScreenProps<RootStackParamList, "Queue">;

const STATE: Record<OutboxOp["status"], { text: string; tone: "neutral" | "warn" | "bad" | "good" }> = {
  PENDING: { text: "Waiting to send", tone: "neutral" },
  INFLIGHT: { text: "Sending…", tone: "neutral" },
  FAILED: { text: "Will try again", tone: "warn" },
  DEAD: { text: "Needs attention", tone: "bad" },
  DONE: { text: "Sent", tone: "good" },
};

/**
 * What is waiting to reach head office, with its state and the last thing that went wrong
 * (docs/06 §5, §8). Nothing on this list is ever discarded: an entry the server refused stays
 * here until it is fixed, and one that could not be sent for 72 hours says who to contact.
 */
export default function QueueScreen({ navigation }: Props) {
  const { services, status, version, syncNow } = useApp();
  const [ops, setOps] = useState<OutboxOp[]>([]);

  useEffect(() => {
    if (!services) return;
    void services.store.listOps(["PENDING", "INFLIGHT", "FAILED", "DEAD"]).then(setOps);
  }, [services, version]);

  return (
    <View style={styles.screen}>
      {status?.authRequired && <Banner tone="warn">Sign in again to send these. Nothing has been lost.</Banner>}
      {status?.online === false && <Banner tone="warn">You are offline. These will send when you are back online.</Banner>}
      <FlatList
        data={ops}
        keyExtractor={(o) => o.op_id}
        ListEmptyComponent={<Text style={styles.none}>Everything has been sent.</Text>}
        renderItem={({ item }) => {
          const s = STATE[item.status];
          const dead = item.status === "DEAD";
          const tooOld = item.last_error?.code === "too_old";
          return (
            <View style={styles.row}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Text style={styles.plate}>{item.payload.truck_number ?? "No plate"}</Text>
                <StatusPill text={s.text} tone={s.tone} />
              </View>
              <Text style={styles.meta}>
                {item.entity === "delivery_correction" ? "Correction" : "New delivery"} · saved {new Date(item.created_at).toLocaleString()}
                {item.attempt_count > 0 ? ` · tried ${item.attempt_count}×` : ""}
              </Text>
              {item.last_error && <Text style={[styles.error, dead && { color: colors.bad }]}>{item.last_error.message}</Text>}
              {dead && !tooOld && (
                <Button title="Fix and send again" kind="secondary" onPress={() => navigation.navigate("NewDelivery", { fixOpId: item.op_id })} style={{ marginTop: 8 }} />
              )}
              {dead && tooOld && <Text style={styles.contact}>Contact head office and quote {item.op_id.slice(-8)}.</Text>}
            </View>
          );
        }}
      />
      <Button title="Sync now" onPress={() => void syncNow()} busy={status?.running ?? false} style={{ margin: 12 }} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, paddingTop: 8 },
  none: { fontSize: 17, color: colors.muted, textAlign: "center", marginTop: 40 },
  row: { padding: 16, borderBottomWidth: 1, borderColor: colors.border },
  plate: { fontSize: 18, fontWeight: "800", color: colors.text },
  meta: { fontSize: 14, color: colors.muted, marginTop: 4 },
  error: { fontSize: 15, color: colors.warn, marginTop: 4 },
  contact: { fontSize: 15, fontWeight: "700", color: colors.bad, marginTop: 8 },
});
