# 05 — Business Rules, Vendor Rates & Unit Conversion

Covers §15 (geofencing), §16 (quantity validation), §19 (vendor rates), §20 (conversion engine)
and §44 (everything configurable).

---

## 1. The rule store

One table, `business_rules`, resolves every configurable threshold in the system.

```
rule_type       TONNAGE_MAX | GEOFENCE_RADIUS | QTY_TOLERANCE | PRICE_TOLERANCE |
                APPROVAL_LIMIT | REORDER_LEVEL | LATE_SUBMISSION | DUPLICATE_WINDOW |
                DAILY_DELIVERY_CAP | CLOCK_SKEW_MAX | PURCHASE_LIMIT
scope           jsonb: {"company_id": .., "project_id": .., "site_id": ..,
                        "material_id": .., "material_category_id": .., "vendor_id": ..,
                        "truck_type_id": .., "role_id": .., "doc_type": ".."}
condition       jsonb (optional, same evaluator as the approval engine)
value           jsonb: {"max": 16.0, "unit": "TON"}  |  {"radius_m": 500}  |
                       {"pct": 5, "abs": 0.25}       |  {"limit": 500000, "currency": "PKR"}
priority        integer — higher wins on a tie
effective_from  date NOT NULL
effective_to    date NULL
is_active       boolean
```

### Resolution algorithm

```python
def resolve(rule_type, ctx, at: date) -> Rule | None:
    candidates = [r for r in rules[rule_type]
                  if r.is_active
                  and r.effective_from <= at <= (r.effective_to or date.max)
                  and scope_matches(r.scope, ctx)          # every key present must equal ctx
                  and evaluate(r.condition, ctx)]
    return max(candidates, key=lambda r: (specificity(r.scope), r.priority, r.effective_from),
               default=None)
```

`specificity` = the number of scope keys set. **Most specific wins**, so:

| Rule | Scope | Wins for |
|---|---|---|
| max 16 t | `{truck_type: 10-wheeler}` | any 10-wheeler |
| max 18 t | `{truck_type: 10-wheeler, material: Crush}` | 10-wheelers carrying Crush |
| max 14 t | `{truck_type: 10-wheeler, material: Crush, site: GVH-S2}` | that combination at Site 2 |

Resolution results are cached in Redis keyed by `(rule_type, scope-hash, date)` and invalidated on
any write to `business_rules`. Every evaluation that produces a flag stores `rule_id` **and**
`rule_snapshot jsonb` on the flag, so tightening a threshold next month never rewrites the history
of why something was flagged.

### Seeded defaults

| rule_type | Default | Scope |
|---|---|---|
| TONNAGE_MAX | per `truck_types.default_max_tonnage` | truck type |
| GEOFENCE_RADIUS | 500 m | company (overridden per site by `sites.geofence_radius_m`) |
| QTY_TOLERANCE | 2% or 0.5 unit, whichever greater | company |
| PRICE_TOLERANCE | 0% (invoice rate must equal PO rate) | company |
| CLOCK_SKEW_MAX | 900 s | company |
| DUPLICATE_WINDOW | same truck + material + site within 20 min | company |
| LATE_SUBMISSION | capture date more than 2 days old | company |

---

## 2. Geofence evaluation (§15)

Sites carry either a radius around a centroid or an explicit polygon; polygon wins when present.

```sql
-- at delivery ingest
SELECT
  CASE WHEN s.boundary IS NOT NULL
       THEN ST_Distance(s.boundary, $point)          -- 0 when inside
       ELSE GREATEST(ST_Distance(s.centroid, $point) - s.geofence_radius_m, 0)
  END AS distance_outside_m
FROM sites s WHERE s.id = $site_id;
```

Outcome:

| Condition | Result |
|---|---|
| `distance_outside_m = 0` | `is_inside_geofence = true`, no flag |
| `> 0` but within `gps_accuracy_m` | inside, `INFO` note recorded (the GPS fix cannot distinguish) |
| `> 0` and beyond accuracy | `GEOFENCE_MISMATCH` flag, severity by distance band (<200 m WARNING, else CRITICAL), message `"640m outside site"` |
| No GPS fix at all | `location_source = MANUAL`, `GEOFENCE_MISMATCH` with `actual_value = null` |

**The delivery is always saved** (§15). Distance, captured point, expected site, timestamp and user
are all stored regardless of outcome.

Accuracy guard: fixes worse than 100 m are stored but do not by themselves create a CRITICAL flag —
otherwise every delivery under a shed roof becomes an exception and reviewers learn to click
through.

---

## 3. Quantity validation (§16)

At ingest, in order:

1. **Positive and within material precision** — hard reject, client-side too.
2. **Tonnage vs resolved TONNAGE_MAX** for (truck type, material, vendor, project, site) →
   `TONNAGE_ANOMALY`, `expected 16.0000 / actual 22.0000 / +37.5%`.
3. **PO balance** when the delivery is PO-backed: `received + this <= ordered * (1 + QTY_TOLERANCE)`
   → `PO_QTY_EXCEEDED`.
4. **Daily cap** per vendor per site (`DAILY_DELIVERY_CAP`) → `DAILY_CAP_EXCEEDED`.
5. **Duplicate suspicion** — same truck number + material + site inside `DUPLICATE_WINDOW`, or the
   same client UUID already present → the second is either a true idempotent replay (accepted
   silently, same response replayed) or a `DUPLICATE_SUSPECT` flag.
6. **Clock skew** beyond `CLOCK_SKEW_MAX` → `CLOCK_SKEW` flag.

None of these reject the record. Any flag with severity >= WARNING sets
`status = UNDER_REVIEW`, `has_open_flags = true`, and the delivery appears in the head-office
review queue (§18). A delivery with no flags goes straight to `SUBMITTED` and, if site policy
allows auto-approval, to `APPROVED`.

---

## 4. Vendor rates (§19)

### Storage
`vendor_rates` is append-only and effective-dated, with an exclusion constraint preventing
overlapping periods per (company, vendor, material, unit, project, site). Editing a rate is
modelled as **supersession**:

```
UPDATE vendor_rates SET effective_to = :new_from - 1 day WHERE id = :old_id;
INSERT INTO vendor_rates (..., effective_from = :new_from, ...);
INSERT INTO vendor_rate_history (vendor_rate_id, old_rate, new_rate, changed_by, reason,
                                 superseded_rate_id);
INSERT INTO audit_logs (... summary = 'Crush / Shree Stone: 48.0000 -> 52.0000 eff. 2026-08-12');
```

The API exposes no `PUT /vendor-rates/{id}` for the rate value at all. There is
`POST /vendor-rates` (new period) and `PATCH` limited to non-financial fields (notes). **Historical
financial data cannot be overwritten through the application** (§19), and the database role has no
UPDATE grant on `vendor_rate_history`.

Rate changes can be approval-gated by workflow doc_type `vendor_rate` — typically required when
`change_pct > 10`.

### Resolution
Inputs: vendor, material, unit, site (→ project → company), and the delivery's `captured_at`.
Most specific scope wins, then the row whose effective period contains the capture date. If nothing
matches, the delivery is flagged `RATE_MISSING` and saved with a null rate — it is captured now and
priced later, which is what actually happens on site.

### Head-office rate screen (§19)
Grid of material x vendor with current rate, unit, effective-from, and a sparkline of history.
Row expansion shows the full `Rs 48 → Rs 52, 12 Aug, by Ahmed R., reason "monsoon freight"` trail.

---

## 5. Unit conversion engine (§20)

**No conversion factor is hard-coded anywhere in the codebase.** Every factor is a row in
`unit_conversions`, maintained by an administrator with `units.manage_conversions`.

```python
class UnitConverter:
    async def factor(self, from_unit, to_unit, *, material=None, vendor=None,
                     at: date) -> Decimal: ...
    async def convert(self, qty: Decimal, from_unit, to_unit, **scope) -> ConvertedQty: ...
```

Resolution order: `MATERIAL_VENDOR` → `MATERIAL` → `GLOBAL`, each filtered by effective date.
Inverse lookups are automatic (`1/factor`) when only one direction is stored. Chained conversions
(TON → KG → BAG) are resolved by a two-hop search through the dimension graph; more than two hops
is refused as ambiguous rather than guessed.

Failure to resolve raises `ConversionNotConfigured` — it never falls back to a default. A missing
factor is a configuration gap an administrator must close, not a number the system should invent.

### Where conversion happens
| Situation | Behaviour |
|---|---|
| Truck weighed in tonnes, vendor billed per cft | delivery item stores `quantity=12.5 TON`, `converted_quantity=X CFT`, `conversion_factor`, `conversion_id`, `rate` per CFT, `amount = converted_quantity * rate` |
| Cement counted in bags, stocked in bags | factor 1, no conversion row needed |
| Steel purchased in tonnes, issued in kg | `material_units` lists both; issue converts with the MATERIAL-scoped factor |

Because the factor is snapshotted on the line, correcting a wrong factor next month changes future
documents only — past deliveries keep the arithmetic that was actually used, and the GL stays
reconcilable.

---

## 6. Delivery cost calculation (§20)

At approval:

```
converted_quantity = convert(quantity, from=entry_unit, to=rate_unit,
                             material=..., vendor=..., at=captured_at)
amount             = round(converted_quantity * rate, 4)
```

Rounding is half-up at 4 decimals for the line, and half-up at the currency's minor unit for the
document total, with the difference absorbed on the largest line — so document total always equals
the sum of its lines, exactly.

The computed amount is written to `delivery_items.amount`, carried to `grn_items.amount`, and
becomes the accrual value in the GL. Three tables, one number, one derivation — that is what lets
the system answer "what rate was used?" years later.

---

## 7. Administration UI

A single **Settings → Business Rules** screen lists rules grouped by type, showing scope as
human-readable breadcrumbs ("Green Valley › Site 2 › Crush › 10-wheeler"), effective dates, and a
**simulator**: enter a hypothetical delivery and see which rule resolves and why. Without the
simulator, "most specific wins" becomes folklore and administrators stop trusting the system.
