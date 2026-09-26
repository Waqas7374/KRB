"""Vendor rates: effective-dated, append-only, resolved by scope (docs/05 §4).

What a vendor charges for a material, from when, at which project or site.
A rate is never overwritten: a change is a new period that supersedes the old
one, with the reason and the old value kept in a history nobody can edit.
Deliveries (Phase 3) and purchase orders read the resolved rate and snapshot
it, so a later change never restates the past.
"""
