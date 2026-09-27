import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as Location from "expo-location";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { RootStackParamList } from "../shell/navigation";
import { useApp, useCatalogue, useDeliveries } from "../shell/services";
import {
  balanceLine,
  buildDraft,
  emptyForm,
  formFrom,
  plateChips,
  poCandidates,
  poFor,
  topMaterials,
  withMaterial,
  type FormState,
  type Position,
} from "../core/form";
import { saveMessage } from "../core/labels";
import { InvalidEntryError } from "../core/outbox";
import { locationAdvice, POOR_ACCURACY_M, weightAdvice, type Advice } from "../core/precheck";
import type { Catalogue } from "../core/reference";
import { validateDraft } from "../core/validate";
import { Banner, Button, Chip, colors, Field } from "../ui/kit";

type Props = NativeStackScreenProps<RootStackParamList, "NewDelivery">;

type Fix = { kind: "new" } | { kind: "correct"; deliveryId: string; note: string | null } | { kind: "fix"; opId: string; reason: string | null };

/**
 * The 20-30 second screen (docs/06 §2), ordered so the common case is thumb-only: material,
 * plate, vendor, quantity, save. The position is taken on its own while the person fills the
 * form. Save writes to the phone and returns: the network is never on this path.
 *
 * There is no price, factor or rule anywhere on this screen. The server works those out.
 */
export default function NewDeliveryScreen({ navigation, route }: Props) {
  const { services, status, changed, syncNow } = useApp();
  const catalogue = useCatalogue();
  const deliveries = useDeliveries(200);
  const [form, setForm] = useState<FormState | null>(null);
  const [mode, setMode] = useState<Fix>({ kind: "new" });
  const [search, setSearch] = useState({ material: "", vendor: "" });
  const [more, setMore] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const { position, locationState, locate } = useLocation();

  // Start from a blank form, or from the entry being corrected or mended.
  useEffect(() => {
    if (!services || !catalogue || form) return;
    void (async () => {
      const { correctId, fixOpId } = route.params ?? {};
      if (correctId) {
        const d = await services.store.getDelivery(correctId);
        if (d) {
          setForm(formFrom(d.payload));
          setMode({ kind: "correct", deliveryId: correctId, note: d.review?.comments ?? null });
          setMore(true);
          return;
        }
      }
      if (fixOpId) {
        const op = await services.store.getOp(fixOpId);
        if (op) {
          setForm(formFrom(op.payload));
          setMode({ kind: "fix", opId: fixOpId, reason: op.last_error?.message ?? null });
          setMore(true);
          return;
        }
      }
      // Sites: the one this person is assigned to, or the one they used last.
      const last = deliveries[0]?.payload.site_id;
      setForm(emptyForm(catalogue.sites.find((s) => s.id === last)?.id ?? catalogue.sites[0]?.id ?? ""));
    })();
  }, [services, catalogue, form, route.params, deliveries]);

  const set = useCallback((patch: Partial<FormState>) => setForm((f) => (f ? { ...f, ...patch } : f)), []);

  const site = catalogue?.sites.find((s) => s.id === form?.siteId);
  const truck = catalogue?.truckTypes.find((t) => t.id === form?.truckTypeId);
  const unitCode = catalogue?.units.find((u) => u.id === form?.unitId)?.data.code;
  const tonnes = unitCode === "TON" && form && Number(form.quantity) > 0 ? Number(form.quantity) : null;
  const advice: Advice[] = useMemo(
    () => [...locationAdvice(site?.data, position), ...weightAdvice(tonnes, truck?.data)],
    [site, position, tonnes, truck],
  );

  if (!catalogue || !form || !services) return <View style={styles.screen} />;
  const cat: Catalogue = catalogue;
  const pos = poCandidates(form, cat);
  const material = cat.materials.find((m) => m.id === form.materialId);
  const materialPills = search.material
    ? cat.materials.filter((m) => `${m.data.name} ${m.data.sku}`.toLowerCase().includes(search.material.toLowerCase())).slice(0, 12)
    : topMaterials(cat, deliveries, 6);
  const vendorPills = search.vendor
    ? cat.vendors.filter((v) => `${v.data.name} ${v.data.code}`.toLowerCase().includes(search.vendor.toLowerCase())).slice(0, 12)
    : cat.vendors.slice(0, 6);

  async function save() {
    if (!form) return;
    const draft = buildDraft(form, position, cat);
    const issues = validateDraft({ ...draft, id: "check", captured_at: new Date().toISOString() });
    if (issues.length) {
      setErrors(Object.fromEntries(issues.map((i) => [i.field.startsWith("items.0.") ? i.field.slice(8) : i.field, i.message])));
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      if (mode.kind === "correct") {
        await services!.outbox.submitCorrection(mode.deliveryId, { ...draft, id: mode.deliveryId, captured_at: new Date().toISOString() });
      } else if (mode.kind === "fix") {
        const op = await services!.store.getOp(mode.opId);
        await services!.outbox.fixAndRetry(mode.opId, { ...draft, id: op?.entity_id ?? "", captured_at: op?.payload.captured_at ?? new Date().toISOString() });
      } else {
        await services!.outbox.captureDelivery(draft);
      }
      changed();
      void syncNow(); // send it now if there is a way to; never wait for it
      // Truthful either way: with a connection it is on its way, without one it is safe on the phone.
      Alert.alert("Saved", status?.online === true ? "Saved. Sending now…" : saveMessage({ online: false }));
      navigation.goBack();
    } catch (err) {
      if (err instanceof InvalidEntryError) setErrors(Object.fromEntries(err.issues.map((i) => [i.field, i.message])));
      else Alert.alert("Could not save", "Something went wrong saving this entry on the phone. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={styles.screen}>
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
        {mode.kind === "correct" && <Banner tone="warn">Head office asked: {mode.note ?? "please correct this entry."}</Banner>}
        {mode.kind === "fix" && <Banner tone="bad">This was not accepted: {mode.reason ?? "please check it."} Fix it and send again.</Banner>}

        <LocationChip state={locationState} position={position} onRetry={locate} />
        {advice.map((a) => (
          <Banner key={a.code} tone="warn">
            {a.message}
          </Banner>
        ))}

        {cat.sites.length > 1 && (
          <Section title="Site">
            <View style={styles.wrap}>
              {cat.sites.map((s) => (
                <Chip key={s.id} label={s.data.code} selected={s.id === form.siteId} onPress={() => set({ siteId: s.id, poId: "" })} />
              ))}
            </View>
          </Section>
        )}

        <Section title="Material" error={errors.material_id}>
          <View style={styles.wrap}>
            {materialPills.map((m) => (
              <Chip key={m.id} label={m.data.name} selected={m.id === form.materialId} onPress={() => setForm(withMaterial(form, m.id, cat))} />
            ))}
          </View>
          <Field label="Search materials" value={search.material} onChangeText={(t) => setSearch((s) => ({ ...s, material: t }))} autoCorrect={false} />
        </Section>

        <Field
          label="Truck number"
          value={form.truckNumber}
          onChangeText={(t) => set({ truckNumber: t.toUpperCase() })}
          autoCapitalize="characters"
          autoCorrect={false}
        />
        <View style={[styles.wrap, { marginTop: -6, marginBottom: 12 }]}>
          {plateChips(deliveries, form.siteId).slice(0, 8).map((p) => (
            <Chip key={p} label={p} selected={p === form.truckNumber} onPress={() => set({ truckNumber: p })} />
          ))}
        </View>

        <Section title="Vendor" error={errors.vendor_id}>
          <View style={styles.wrap}>
            {vendorPills.map((v) => (
              <Chip
                key={v.id}
                label={v.data.name}
                selected={v.id === form.vendorId}
                onPress={() => {
                  const next = { ...form, vendorId: v.id };
                  set({ vendorId: v.id, poId: poFor(next, cat)?.id ?? "" });
                }}
              />
            ))}
          </View>
          <Field label="Search vendors" value={search.vendor} onChangeText={(t) => setSearch((s) => ({ ...s, vendor: t }))} autoCorrect={false} />
        </Section>

        {form.vendorId !== "" && (
          <Section title="Purchase order (optional)">
            <View style={styles.wrap}>
              <Chip label="No order" selected={form.poId === ""} onPress={() => set({ poId: "" })} />
              {pos.map((p) => (
                <Chip key={p.id} label={p.data.po_number} selected={p.id === form.poId} onPress={() => set({ poId: p.id })} />
              ))}
            </View>
            {form.poId !== "" && form.materialId !== "" && (
              <Text style={styles.hint}>
                {balanceLine(cat.openPos.find((p) => p.id === form.poId)!.data, form.materialId) ?? ""}
              </Text>
            )}
            {pos.length === 0 && <Text style={styles.hint}>No open order for this vendor and material. It will be saved and reviewed at head office.</Text>}
          </Section>
        )}

        <Field
          label={`Quantity${unitCode ? ` (${unitCode})` : ""}`}
          value={form.quantity}
          onChangeText={(t) => set({ quantity: t.replace(",", ".") })}
          keyboardType="decimal-pad"
          error={errors.quantity}
        />
        {material && material.data.unit_ids.length > 1 && (
          <View style={[styles.wrap, { marginTop: -6, marginBottom: 12 }]}>
            {material.data.unit_ids.map((id) => (
              <Chip key={id} label={cat.units.find((u) => u.id === id)?.data.code ?? "unit"} selected={id === form.unitId} onPress={() => set({ unitId: id })} />
            ))}
          </View>
        )}

        <Pressable accessibilityRole="button" onPress={() => setMore((m) => !m)} style={styles.moreToggle}>
          <Text style={styles.moreText}>{more ? "Hide details" : "More details (driver, challan, truck type)"}</Text>
        </Pressable>
        {more && (
          <View>
            <Section title="Truck type">
              <View style={styles.wrap}>
                <Chip label="Not stated" selected={form.truckTypeId === ""} onPress={() => set({ truckTypeId: "" })} />
                {cat.truckTypes.map((t) => (
                  <Chip key={t.id} label={t.data.name} selected={t.id === form.truckTypeId} onPress={() => set({ truckTypeId: t.id })} />
                ))}
              </View>
            </Section>
            <Field label="Driver name" value={form.driverName} onChangeText={(t) => set({ driverName: t })} />
            <Field label="Driver phone" value={form.driverPhone} onChangeText={(t) => set({ driverPhone: t })} keyboardType="phone-pad" />
            <Field label="Challan number" value={form.challanNumber} onChangeText={(t) => set({ challanNumber: t })} />
            <Field label="Remarks" value={form.remarks} onChangeText={(t) => set({ remarks: t })} multiline />
          </View>
        )}
      </ScrollView>
      <View style={styles.saveBar}>
        <Button
          title={mode.kind === "correct" ? "Send correction" : mode.kind === "fix" ? "Fix and send again" : "Save entry"}
          onPress={() => void save()}
          busy={saving}
          style={{ minHeight: 56 }}
        />
      </View>
    </KeyboardAvoidingView>
  );
}

function Section({ title, error, children }: { title: string; error?: string | undefined; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: 14 }}>
      <Text style={styles.label}>{title}</Text>
      {children}
      {error ? <Text style={styles.error} accessibilityRole="alert">{error}</Text> : null}
    </View>
  );
}

type LocationState = "locating" | "captured" | "poor" | "unavailable";

/** Position is taken on its own while the person fills the form (docs/06 §2): Locating → Captured ±8 m. */
function useLocation() {
  const [position, setPosition] = useState<Position | null>(null);
  const [locationState, setState] = useState<LocationState>("locating");
  const locate = useCallback(async () => {
    setState("locating");
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (permission.status !== "granted") {
        setState("unavailable");
        return;
      }
      const reading = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      const accuracy = reading.coords.accuracy ?? null;
      setPosition({ latitude: reading.coords.latitude, longitude: reading.coords.longitude, accuracy, source: "GPS" });
      setState(accuracy != null && accuracy > POOR_ACCURACY_M ? "poor" : "captured");
    } catch {
      setState("unavailable");
    }
  }, []);
  useEffect(() => {
    void locate();
  }, [locate]);
  return { position, locationState, locate };
}

function LocationChip({ state, position, onRetry }: { state: LocationState; position: Position | null; onRetry: () => void }) {
  const text =
    state === "locating"
      ? "Locating…"
      : state === "captured"
        ? `Location captured ±${Math.round(position?.accuracy ?? 0)} m`
        : state === "poor"
          ? `Poor signal ±${Math.round(position?.accuracy ?? 0)} m`
          : "Location unavailable. The entry is still saved.";
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${text}. Tap to try again.`} onPress={onRetry} style={styles.locChip}>
      <Text style={[styles.locText, state === "captured" && { color: colors.good }, (state === "poor" || state === "unavailable") && { color: colors.warn }]}>
        {text}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  wrap: { flexDirection: "row", flexWrap: "wrap" },
  label: { fontSize: 16, fontWeight: "700", color: colors.text, marginBottom: 6 },
  hint: { fontSize: 14, color: colors.muted, marginTop: 4 },
  error: { fontSize: 15, color: colors.bad, marginTop: 4 },
  moreToggle: { minHeight: 48, justifyContent: "center", marginBottom: 8 },
  moreText: { fontSize: 16, fontWeight: "700", color: colors.primary },
  saveBar: { padding: 12, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.bg },
  locChip: { minHeight: 44, justifyContent: "center", marginBottom: 8 },
  locText: { fontSize: 16, fontWeight: "700", color: colors.muted },
});
