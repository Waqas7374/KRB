"""Phase 1 seed data: units, conversions, materials, truck types, warehouses,
vendors.

The conversion factors here are the ones a land-development company actually
needs and the ones most likely to be argued about later, so each carries a
`basis_note` explaining where the number came from. They are illustrative
defaults: an administrator is expected to replace them with the company's own
measured densities (see N-6 in docs/12-confirmed-decisions.md).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.masterdata.domain.enums import (
    ConversionScope,
    MaterialTracking,
    UnitDimension,
    WarehouseType,
)
from app.modules.masterdata.models import (
    Material,
    MaterialCategory,
    MaterialUnit,
    TruckType,
    Unit,
    UnitConversion,
    Warehouse,
)
from app.modules.org.models import Company, Site
from app.modules.vendors.domain.enums import VendorStatus, VendorType
from app.modules.vendors.models import Vendor, VendorContact, VendorMaterial
from app.platform.numbering import DocumentType, ensure_sequence_at_least
from app.seeds.registry import SeedResult, upsert

EFFECTIVE_FROM = date(2025, 7, 1)

# code, name, symbol, dimension, precision
UNITS: tuple[tuple[str, str, str | None, UnitDimension, int], ...] = (
    ("TON", "Metric Tonne", "t", UnitDimension.MASS, 3),
    ("KG", "Kilogram", "kg", UnitDimension.MASS, 3),
    ("BAG", "Bag", "bag", UnitDimension.COUNT, 0),
    ("CFT", "Cubic Foot", "cft", UnitDimension.VOLUME, 2),
    ("CUM", "Cubic Metre", "m³", UnitDimension.VOLUME, 3),
    ("BRASS", "Brass (100 cft)", "brass", UnitDimension.VOLUME, 3),
    ("NOS", "Number", "nos", UnitDimension.COUNT, 0),
    ("RFT", "Running Foot", "rft", UnitDimension.LENGTH, 2),
    ("MTR", "Metre", "m", UnitDimension.LENGTH, 3),
    ("SFT", "Square Foot", "sft", UnitDimension.AREA, 2),
    ("TRIP", "Truck Trip", "trip", UnitDimension.COUNT, 0),
    ("LTR", "Litre", "L", UnitDimension.VOLUME, 2),
)

# from, to, factor, basis
GLOBAL_CONVERSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("TON", "KG", "1000", "Definition: 1 metric tonne = 1000 kg"),
    ("CUM", "CFT", "35.3146667", "Definition: 1 m³ = 35.3146667 cft"),
    ("BRASS", "CFT", "100", "Definition: 1 brass = 100 cft (South Asian trade unit)"),
    ("MTR", "RFT", "3.280839895", "Definition: 1 m = 3.280839895 ft"),
)

# material sku, from, to, factor, basis — density-dependent, so material-scoped
MATERIAL_CONVERSIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "AGG-CRUSH-20",
        "TON",
        "CFT",
        "22.500000",
        "Bulk density 1.55 t/m³ for 20mm crush; 1 t = 0.645 m³ = 22.5 cft. "
        "REPLACE with the company's own weighbridge and volumetric measurement.",
    ),
    (
        "AGG-CRUSH-12",
        "TON",
        "CFT",
        "23.200000",
        "Bulk density 1.52 t/m³ for 12mm crush. Illustrative — verify locally.",
    ),
    (
        "SAND-RIVER",
        "TON",
        "CFT",
        "24.700000",
        "Bulk density 1.43 t/m³ for damp river sand. Illustrative — verify locally.",
    ),
    (
        "SAND-FINE",
        "TON",
        "CFT",
        "25.900000",
        "Bulk density 1.36 t/m³ for fine sand. Illustrative — verify locally.",
    ),
    (
        "CEM-OPC-50",
        "BAG",
        "KG",
        "50",
        "Standard OPC bag: 50 kg net",
    ),
    (
        "AGG-GRAVEL",
        "TON",
        "CFT",
        "22.900000",
        "Bulk density 1.54 t/m³ for washed gravel. Illustrative — verify locally.",
    ),
)

# code, name, parent code, sequence
CATEGORIES: tuple[tuple[str, str, str | None, int], ...] = (
    ("AGG", "Aggregates", None, 10),
    ("AGG-CRUSH", "Crush", "AGG", 11),
    ("AGG-GRAVEL", "Gravel & Stone", "AGG", 12),
    ("SAND", "Sand", None, 20),
    ("CEMENT", "Cement", None, 30),
    ("STEEL", "Steel", None, 40),
    ("CONCRETE", "Concrete", None, 50),
    ("BRICK", "Bricks & Blocks", None, 60),
    ("PIPE", "Pipes & Fittings", None, 70),
    ("ELEC", "Electrical", None, 80),
    ("FUEL", "Fuel & Lubricants", None, 90),
)

# sku, name, category, base unit, capture unit, min stock, reorder, std rate, rate unit, attrs
MATERIALS: tuple[dict[str, Any], ...] = (
    {
        "sku": "AGG-CRUSH-20",
        "name": "Crush 20mm (Margalla)",
        "category": "AGG-CRUSH",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "200",
        "reorder": "400",
        "rate": "52.000000",
        "rate_unit": "CFT",
        "attributes": {"nominal_size_mm": 20, "source": "Margalla"},
    },
    {
        "sku": "AGG-CRUSH-12",
        "name": "Crush 12mm (Margalla)",
        "category": "AGG-CRUSH",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "150",
        "reorder": "300",
        "rate": "54.000000",
        "rate_unit": "CFT",
        "attributes": {"nominal_size_mm": 12, "source": "Margalla"},
    },
    {
        "sku": "AGG-GRAVEL",
        "name": "Gravel (washed)",
        "category": "AGG-GRAVEL",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "100",
        "reorder": "200",
        "rate": "48.000000",
        "rate_unit": "CFT",
        "attributes": {},
    },
    {
        "sku": "SAND-RIVER",
        "name": "River Sand (Chenab)",
        "category": "SAND",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "150",
        "reorder": "300",
        "rate": "38.000000",
        "rate_unit": "CFT",
        "attributes": {"source": "Chenab"},
    },
    {
        "sku": "SAND-FINE",
        "name": "Fine Sand (plaster grade)",
        "category": "SAND",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "80",
        "reorder": "160",
        "rate": "42.000000",
        "rate_unit": "CFT",
        "attributes": {"grade": "plaster"},
    },
    {
        "sku": "CEM-OPC-50",
        "name": "Cement OPC 50kg Bag",
        "category": "CEMENT",
        "base_unit": "BAG",
        "capture_unit": "BAG",
        "min_stock": "500",
        "reorder": "1500",
        "rate": "1290.000000",
        "rate_unit": "BAG",
        "attributes": {"grade": "OPC", "weight_kg": 50},
    },
    {
        "sku": "STEEL-REBAR-12",
        "name": "Deformed Steel Bar 12mm (Grade 60)",
        "category": "STEEL",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "10",
        "reorder": "25",
        "rate": "268000.000000",
        "rate_unit": "TON",
        "attributes": {"diameter_mm": 12, "grade": "60"},
    },
    {
        "sku": "STEEL-REBAR-16",
        "name": "Deformed Steel Bar 16mm (Grade 60)",
        "category": "STEEL",
        "base_unit": "TON",
        "capture_unit": "TON",
        "min_stock": "10",
        "reorder": "25",
        "rate": "266000.000000",
        "rate_unit": "TON",
        "attributes": {"diameter_mm": 16, "grade": "60"},
    },
    {
        "sku": "CONC-RMC-3000",
        "name": "Ready-Mix Concrete 3000 psi",
        "category": "CONCRETE",
        "base_unit": "CFT",
        "capture_unit": "CFT",
        "min_stock": None,
        "reorder": None,
        "rate": "310.000000",
        "rate_unit": "CFT",
        "attributes": {"strength_psi": 3000},
    },
    {
        "sku": "BRICK-A1",
        "name": "Burnt Clay Brick (A-grade)",
        "category": "BRICK",
        "base_unit": "NOS",
        "capture_unit": "NOS",
        "min_stock": "20000",
        "reorder": "50000",
        "rate": "22.500000",
        "rate_unit": "NOS",
        "attributes": {"grade": "A"},
    },
    {
        "sku": "PIPE-PVC-110",
        "name": "PVC Pipe 110mm (Class B)",
        "category": "PIPE",
        "base_unit": "RFT",
        "capture_unit": "RFT",
        "min_stock": "500",
        "reorder": "1500",
        "rate": "410.000000",
        "rate_unit": "RFT",
        "attributes": {"diameter_mm": 110, "class": "B"},
    },
    {
        "sku": "PIPE-RCC-450",
        "name": "RCC Pipe 450mm (sewerage)",
        "category": "PIPE",
        "base_unit": "RFT",
        "capture_unit": "RFT",
        "min_stock": "200",
        "reorder": "600",
        "rate": "1850.000000",
        "rate_unit": "RFT",
        "attributes": {"diameter_mm": 450},
    },
    {
        "sku": "ELEC-CABLE-4C25",
        "name": "LT Cable 4-Core 25mm² (Cu)",
        "category": "ELEC",
        "base_unit": "MTR",
        "capture_unit": "MTR",
        "min_stock": "300",
        "reorder": "800",
        "rate": "2450.000000",
        "rate_unit": "MTR",
        "attributes": {"cores": 4, "csa_mm2": 25, "conductor": "copper"},
    },
    {
        "sku": "FUEL-HSD",
        "name": "High-Speed Diesel",
        "category": "FUEL",
        "base_unit": "LTR",
        "capture_unit": "LTR",
        "min_stock": "2000",
        "reorder": "5000",
        "rate": "272.500000",
        "rate_unit": "LTR",
        "attributes": {},
    },
)

# code, name, axles, max tonnage, typical cft
TRUCK_TYPES: tuple[tuple[str, str, int, str, str | None], ...] = (
    ("TRACTOR-TROLLEY", "Tractor Trolley", 2, "6.000", "135"),
    ("6-WHEELER", "6-Wheeler Tipper", 2, "10.000", "230"),
    ("10-WHEELER", "10-Wheeler Tipper", 3, "16.000", "360"),
    ("12-WHEELER", "12-Wheeler Tipper", 4, "22.000", "500"),
    ("TRAILER", "Flatbed Trailer", 6, "32.000", None),
    ("MIXER", "Transit Mixer", 3, "14.000", "212"),
    ("BOWSER", "Fuel Bowser", 3, "12.000", None),
)

# site code, warehouse code, name, type, is_default_receiving
WAREHOUSES: tuple[tuple[str, str, str, WarehouseType, bool], ...] = (
    ("GVH-S1", "GVH-S1-YARD", "Green Valley S1 — Aggregate Yard", WarehouseType.SITE_STORE, True),
    ("GVH-S1", "GVH-S1-GODOWN", "Green Valley S1 — Cement Godown", WarehouseType.SITE_STORE, False),
    ("GVH-S2", "GVH-S2-YARD", "Green Valley S2 — Aggregate Yard", WarehouseType.SITE_STORE, True),
    ("RSD-S1", "RSD-S1-YARD", "Riverside S1 — Material Yard", WarehouseType.SITE_STORE, True),
    ("CS-LHR", "CS-LHR-MAIN", "Central Store — Main", WarehouseType.CENTRAL, True),
    ("CS-LHR", "CS-LHR-QUAR", "Central Store — Quarantine", WarehouseType.QUARANTINE, False),
)

# code, legal name, trade name, type, materials, contact
VENDORS: tuple[dict[str, Any], ...] = (
    {
        "code": "VEN-00001",
        "legal_name": "Shree Stone Crushing Company",
        "trade_name": "Shree Stone",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "2233445-6",
        "strn": "0423456789012",
        "is_filer": True,
        "phone": "+924235551001",
        "email": "sales@shreestone.example",
        "city": "Lahore",
        "materials": ["AGG-CRUSH-20", "AGG-CRUSH-12", "AGG-GRAVEL"],
        "contact": ("Shahid Mahmood", "Sales Manager", "+923211001001"),
        "terms": 30,
    },
    {
        "code": "VEN-00002",
        "legal_name": "Yamuna Sand Suppliers",
        "trade_name": "Yamuna Sand",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "3344556-7",
        "strn": "0434567890123",
        "is_filer": True,
        "phone": "+924235551002",
        "email": "orders@yamunasand.example",
        "city": "Lahore",
        "materials": ["SAND-RIVER", "SAND-FINE"],
        "contact": ("Asif Nawaz", "Proprietor", "+923211001002"),
        "terms": 15,
    },
    {
        "code": "VEN-00003",
        "legal_name": "Lucky Cement Distributors (Pvt) Ltd",
        "trade_name": "Lucky Distributors",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "4455667-8",
        "strn": "0445678901234",
        "is_filer": True,
        "phone": "+924235551003",
        "email": "b2b@luckydist.example",
        "city": "Lahore",
        "materials": ["CEM-OPC-50"],
        "contact": ("Usman Tariq", "Key Accounts", "+923211001003"),
        "terms": 30,
    },
    {
        "code": "VEN-00004",
        "legal_name": "Ittefaq Steel Mills",
        "trade_name": "Ittefaq Steel",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "5566778-9",
        "strn": "0456789012345",
        "is_filer": True,
        "phone": "+924235551004",
        "email": "sales@ittefaqsteel.example",
        "city": "Lahore",
        "materials": ["STEEL-REBAR-12", "STEEL-REBAR-16"],
        "contact": ("Nasir Abbas", "Regional Sales", "+923211001004"),
        "terms": 45,
    },
    {
        "code": "VEN-00005",
        "legal_name": "Ravi Ready Mix (Pvt) Ltd",
        "trade_name": "Ravi RMC",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "6677889-0",
        "strn": "0467890123456",
        "is_filer": True,
        "phone": "+924235551005",
        "email": "dispatch@raviremix.example",
        "city": "Lahore",
        "materials": ["CONC-RMC-3000"],
        "contact": ("Waqar Yousaf", "Dispatch Head", "+923211001005"),
        "terms": 15,
    },
    {
        "code": "VEN-00006",
        "legal_name": "Al-Noor Pipe Industries",
        "trade_name": "Al-Noor Pipes",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "7788990-1",
        "is_filer": False,
        "phone": "+924235551006",
        "email": "info@alnoorpipes.example",
        "city": "Gujranwala",
        "materials": ["PIPE-PVC-110", "PIPE-RCC-450"],
        "contact": ("Rizwan Butt", "Manager", "+923211001006"),
        "terms": 30,
    },
    {
        "code": "VEN-00007",
        "legal_name": "Pak Elektron Trading",
        "trade_name": "PEL Trading",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "8899001-2",
        "strn": "0489012345678",
        "is_filer": True,
        "phone": "+924235551007",
        "email": "projects@peltrading.example",
        "city": "Lahore",
        "materials": ["ELEC-CABLE-4C25"],
        "contact": ("Salman Sheikh", "Project Sales", "+923211001007"),
        "terms": 30,
    },
    {
        "code": "VEN-00008",
        "legal_name": "Zafar Earthworks & Civil Contractors",
        "trade_name": "Zafar Civil",
        "type": VendorType.CONTRACTOR,
        "ntn": "9900112-3",
        "is_filer": True,
        "phone": "+924235551008",
        "email": "office@zafarcivil.example",
        "city": "Lahore",
        "materials": [],
        "contact": ("Zafar Iqbal", "Managing Partner", "+923211001008"),
        "terms": 30,
    },
    {
        "code": "VEN-00009",
        "legal_name": "Chenab Transport Services",
        "trade_name": "Chenab Transport",
        "type": VendorType.TRANSPORTER,
        "ntn": "1011223-4",
        "is_filer": False,
        "phone": "+924235551009",
        "email": "booking@chenabtransport.example",
        "city": "Lahore",
        "materials": [],
        "contact": ("Ghulam Haider", "Fleet Manager", "+923211001009"),
        "terms": 7,
    },
    {
        "code": "VEN-00010",
        "legal_name": "Attock Petroleum Dealers",
        "trade_name": "APL Dealer",
        "type": VendorType.MATERIAL_SUPPLIER,
        "ntn": "1122334-5",
        "strn": "0412233445566",
        "is_filer": True,
        "phone": "+924235551010",
        "email": "bulk@apldealer.example",
        "city": "Lahore",
        "materials": ["FUEL-HSD"],
        "contact": ("Kashif Raza", "Bulk Sales", "+923211001010"),
        "terms": 0,
    },
)


async def seed_units(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("units & conversions")

    for code, name, symbol, dimension, precision in UNITS:
        await upsert(
            session,
            Unit,
            match={"company_id": company.id, "code": code},
            values={
                "name": name,
                "symbol": symbol,
                "dimension": dimension.value,
                "precision": precision,
            },
            result=result,
            protected_fields=("name", "symbol", "precision"),
        )
    await session.flush()

    unit_ids = await _unit_ids(session, company)

    for from_code, to_code, factor, basis in GLOBAL_CONVERSIONS:
        await upsert(
            session,
            UnitConversion,
            match={
                "company_id": company.id,
                "from_unit_id": unit_ids[from_code],
                "to_unit_id": unit_ids[to_code],
                "scope_type": ConversionScope.GLOBAL.value,
                "effective_from": EFFECTIVE_FROM,
            },
            values={"factor": Decimal(factor), "basis_note": basis},
            result=result,
            # A corrected factor must never be silently reverted by a re-seed.
            protected_fields=("factor", "basis_note", "effective_to"),
        )

    return result


async def seed_materials(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("materials")
    unit_ids = await _unit_ids(session, company)

    category_ids: dict[str, Any] = {}
    for code, name, parent_code, sequence in CATEGORIES:
        category = await upsert(
            session,
            MaterialCategory,
            match={"company_id": company.id, "code": code},
            values={
                "name": name,
                "parent_id": category_ids.get(parent_code) if parent_code else None,
                "sequence": sequence,
            },
            result=result,
            protected_fields=("name",),
        )
        category_ids[code] = category.id
    await session.flush()

    for spec in MATERIALS:
        material = await upsert(
            session,
            Material,
            match={"company_id": company.id, "sku": spec["sku"]},
            values={
                "name": spec["name"],
                "category_id": category_ids[spec["category"]],
                "base_unit_id": unit_ids[spec["base_unit"]],
                "tracking_type": MaterialTracking.QUANTITY.value,
                "min_stock": Decimal(spec["min_stock"]) if spec["min_stock"] else None,
                "reorder_level": Decimal(spec["reorder"]) if spec["reorder"] else None,
                "standard_rate": Decimal(spec["rate"]),
                "standard_rate_unit_id": unit_ids[spec["rate_unit"]],
                "attributes": spec["attributes"],
                "is_stockable": True,
                "is_purchasable": True,
            },
            result=result,
            protected_fields=("standard_rate", "min_stock", "reorder_level", "name"),
        )
        await session.flush()

        # The unit the mobile form pre-selects, plus the base unit if different.
        for unit_code, is_capture in {
            spec["base_unit"]: spec["base_unit"] == spec["capture_unit"],
            spec["capture_unit"]: True,
            spec["rate_unit"]: False,
        }.items():
            await upsert(
                session,
                MaterialUnit,
                match={"material_id": material.id, "unit_id": unit_ids[unit_code]},
                values={
                    "company_id": company.id,
                    "is_capture_default": is_capture and unit_code == spec["capture_unit"],
                    "is_purchase_default": unit_code == spec["rate_unit"],
                },
                result=result,
                protected_fields=("is_capture_default", "is_purchase_default"),
            )

    await session.flush()
    material_ids = {
        sku: mid
        for mid, sku in (
            await session.execute(
                select(Material.id, Material.sku).where(Material.company_id == company.id)
            )
        ).tuples()
    }

    # Density-dependent conversions: scoped to the material, because tonnes to
    # cubic feet is meaningless without knowing what is in the truck.
    for sku, from_code, to_code, factor, basis in MATERIAL_CONVERSIONS:
        await upsert(
            session,
            UnitConversion,
            match={
                "company_id": company.id,
                "from_unit_id": unit_ids[from_code],
                "to_unit_id": unit_ids[to_code],
                "scope_type": ConversionScope.MATERIAL.value,
                "material_id": material_ids[sku],
                "effective_from": EFFECTIVE_FROM,
            },
            values={"factor": Decimal(factor), "basis_note": basis},
            result=result,
            protected_fields=("factor", "basis_note", "effective_to"),
        )

    for code, name, axles, tonnage, volume in TRUCK_TYPES:
        await upsert(
            session,
            TruckType,
            match={"company_id": company.id, "code": code},
            values={
                "name": name,
                "axle_count": axles,
                "default_max_tonnage": Decimal(tonnage),
                "typical_volume_cft": Decimal(volume) if volume else None,
            },
            result=result,
            protected_fields=("default_max_tonnage", "typical_volume_cft"),
        )

    return result


async def seed_warehouses(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("warehouses")
    site_ids = {
        code: sid
        for sid, code in (
            await session.execute(select(Site.id, Site.code).where(Site.company_id == company.id))
        ).tuples()
    }

    for site_code, code, name, warehouse_type, is_default in WAREHOUSES:
        if site_code not in site_ids:
            continue
        await upsert(
            session,
            Warehouse,
            match={"company_id": company.id, "code": code},
            values={
                "site_id": site_ids[site_code],
                "name": name,
                "warehouse_type": warehouse_type.value,
                "is_default_receiving": is_default,
            },
            result=result,
            protected_fields=("name", "is_default_receiving"),
        )
    return result


async def seed_vendors(session: AsyncSession, company: Company) -> SeedResult:
    result = SeedResult("vendors")
    material_ids = {
        sku: mid
        for mid, sku in (
            await session.execute(
                select(Material.id, Material.sku).where(Material.company_id == company.id)
            )
        ).tuples()
    }

    for spec in VENDORS:
        vendor = await upsert(
            session,
            Vendor,
            match={"company_id": company.id, "code": spec["code"]},
            values={
                "legal_name": spec["legal_name"],
                "trade_name": spec["trade_name"],
                "vendor_type": spec["type"].value,
                "status": VendorStatus.ACTIVE.value,
                "ntn": spec.get("ntn"),
                "strn": spec.get("strn"),
                "is_filer": spec.get("is_filer"),
                "payment_terms_days": spec["terms"],
                "currency_code": company.base_currency,
                "phone": spec["phone"],
                "email": spec["email"],
                "address": {"city": spec["city"], "country": "PK"},
            },
            result=result,
            protected_fields=("status", "payment_terms_days", "legal_name", "trade_name"),
        )
        await session.flush()

        name, designation, phone = spec["contact"]
        await upsert(
            session,
            VendorContact,
            match={"vendor_id": vendor.id, "name": name},
            values={
                "company_id": company.id,
                "designation": designation,
                "phone": phone,
                "is_primary": True,
            },
            result=result,
            protected_fields=("designation", "phone"),
        )

        for sku in spec["materials"]:
            await upsert(
                session,
                VendorMaterial,
                match={"vendor_id": vendor.id, "material_id": material_ids[sku]},
                values={"company_id": company.id, "is_preferred": False},
                result=result,
                protected_fields=("is_preferred", "lead_time_days"),
            )

    # The seeded codes were written by hand, so the counter has to be moved
    # past them or the first server-allocated code would collide.
    highest = max(int(spec["code"].rsplit("-", 1)[-1]) for spec in VENDORS)
    await ensure_sequence_at_least(
        session,
        company_id=company.id,
        doc_type=DocumentType.VENDOR,
        minimum_next=highest + 1,
    )

    await session.flush()
    return result


async def _unit_ids(session: AsyncSession, company: Company) -> dict[str, Any]:
    return {
        code: uid
        for uid, code in (
            await session.execute(select(Unit.id, Unit.code).where(Unit.company_id == company.id))
        ).tuples()
    }
