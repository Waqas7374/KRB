"""Business rules: every configurable threshold in one store (docs/05 §1).

Tonnage limits, geofence radii, tolerances, approval limits... a rule is a
typed value, a scope saying where it applies, an optional condition and an
effective period. `resolve` picks the most specific active rule for a context.
Callers snapshot the rule they used, so tightening a threshold later never
rewrites the history of why something was flagged.
"""
