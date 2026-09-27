import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import type { RootStackParamList } from "../shell/navigation";
import { useApp, useCatalogue } from "../shell/services";
import { statusLabel } from "../core/labels";
import type { LocalDelivery, OutboxOp } from "../core/types";
import { Banner, Button, colors, StatusPill } from "../ui/kit";

type Props = NativeStackScreenProps<RootStackParamList, "DeliveryDetail">;

/** One entry: where it stands, what head office said, and what the person can do about it (docs/06 §2, §6). */
export default function DeliveryDetailScreen({ navigation, route }: Props) {
  const { services, version, changed, syncNow } = useApp();
  const catalogue = useCatalogue();
  const [d, setD] = useState<LocalDelivery | null>(null);
  const [op, setOp] = useState<OutboxOp | null>(null);

  useEffect(() => {
    if (!services) return;
    void (async () => {
      setD(await services.store.getDelivery(route.params.id));
      const ops = await services.store.listOps();
      setOp([...ops].reverse().find((o) => o.entity_id === route.params.id) ?? null);
    })();
  }, [services, route.params.id, version]);

  if (!d) return <View style={styles.screen} />;
  const label = statusLabel(d);
  const line = d.payload.items[0];
  const material = catalogue?.materials.find((m) => m.id === line?.material_id)?.data.name ?? "Material";
  const unit = catalogue?.units.find((u) => u.id === line?.unit_id)?.data.code ?? "";
  const vendor = catalogue?.vendors.find((v) => v.id === d.payload.vendor_id)?.data.name ?? "Vendor";

  return (
    <ScrollView style={styles.screen} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.plate}>{d.payload.truck_number ?? "No plate"}</Text>
      {d.server_number && <Text style={styles.number}>{d.server_number}</Text>}
      <View style={{ marginVertical: 10 }}>
        <StatusPill text={label.text} tone={label.tone} />
      </View>

      {d.review?.comments && (
        <Banner tone="warn">
          {d.review.reviewer_name ?? "Head office"}: {d.review.comments}
        </Banner>
      )}
      {d.last_error && d.local_status !== "SYNCED" && (
        <Banner tone={d.local_status === "REJECTED" ? "bad" : "warn"}>
          {d.last_error.message}
          {d.last_error.fields?.length ? ` (${d.last_error.fields.map((f) => f.field.replace("_id", "")).join(", ")})` : ""}
        </Banner>
      )}
      {d.open_flags.length > 0 && (
        <View style={{ marginBottom: 12 }}>
          <Text style={styles.heading}>Flags</Text>
          {d.open_flags.map((f, i) => (
            <Text key={i} style={styles.flag}>
              • {f.message}
            </Text>
          ))}
        </View>
      )}

      <Row label="Material" value={line ? `${material} · ${Number(line.quantity)} ${unit}` : "—"} />
      <Row label="Vendor" value={vendor} />
      <Row label="Captured" value={new Date(d.payload.captured_at).toLocaleString()} />
      {d.payload.challan_number && <Row label="Challan" value={d.payload.challan_number} />}
      {d.payload.driver_name && <Row label="Driver" value={d.payload.driver_name} />}
      <Row label="Position" value={d.payload.latitude ? `${d.payload.latitude}, ${d.payload.longitude}` : "Not captured"} />

      <View style={{ marginTop: 20 }}>
        {label.action === "correct" && (
          <Button title="Correct this entry" onPress={() => navigation.navigate("NewDelivery", { correctId: d.id })} />
        )}
        {label.action === "fix" && op && (
          <Button title="Fix and send again" onPress={() => navigation.navigate("NewDelivery", { fixOpId: op.op_id })} />
        )}
        {label.action === "acknowledge" && (
          <Button
            title="I have read this"
            onPress={() => {
              void services?.outbox.acknowledgeConflict(d.id).then(() => {
                changed();
                void syncNow();
              });
            }}
          />
        )}
      </View>
    </ScrollView>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  plate: { fontSize: 28, fontWeight: "800", color: colors.text },
  number: { fontSize: 16, color: colors.muted, fontFamily: "monospace" },
  heading: { fontSize: 16, fontWeight: "700", color: colors.text, marginBottom: 4 },
  flag: { fontSize: 16, color: colors.warn, marginBottom: 2 },
  row: { flexDirection: "row", paddingVertical: 10, borderBottomWidth: 1, borderColor: colors.border },
  rowLabel: { width: 100, fontSize: 16, color: colors.muted },
  rowValue: { flex: 1, fontSize: 17, color: colors.text, fontWeight: "600" },
});
