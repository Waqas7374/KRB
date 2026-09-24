/**
 * Enum option lists for filters and selects. Values must match the backend
 * enums (see the generated types in src/types/api.d.ts); labels are for people.
 */
import { humanize } from "@/lib/utils";
import type {
  ConversionScope,
  MaterialTracking,
  PhaseStatus,
  ProjectStatus,
  ProjectType,
  ScopeType,
  SiteType,
  UnitDimension,
  VendorStatus,
  VendorType,
  WarehouseType,
} from "@/types/models";

function options<T extends string>(values: readonly T[]) {
  return values.map((value) => ({ value, label: humanize(value) }));
}

export const VENDOR_STATUSES = [
  "DRAFT",
  "PENDING_APPROVAL",
  "ACTIVE",
  "SUSPENDED",
  "BLACKLISTED",
  "INACTIVE",
] as const satisfies readonly VendorStatus[];

export const VENDOR_TYPES = [
  "MATERIAL_SUPPLIER",
  "CONTRACTOR",
  "SUBCONTRACTOR",
  "TRANSPORTER",
  "SERVICE_PROVIDER",
  "CONSULTANT",
  "EQUIPMENT_RENTAL",
] as const satisfies readonly VendorType[];

export const PROJECT_STATUSES = [
  "DRAFT",
  "ACTIVE",
  "ON_HOLD",
  "COMPLETED",
  "CLOSED",
  "CANCELLED",
] as const satisfies readonly ProjectStatus[];

export const PROJECT_TYPES = [
  "HOUSING_SCHEME",
  "RESIDENTIAL_COMMUNITY",
  "COMMERCIAL",
  "MIXED_USE",
  "INFRASTRUCTURE",
  "OTHER",
] as const satisfies readonly ProjectType[];

export const PHASE_STATUSES = [
  "NOT_STARTED",
  "IN_PROGRESS",
  "ON_HOLD",
  "COMPLETED",
  "CANCELLED",
] as const satisfies readonly PhaseStatus[];

export const SITE_TYPES = [
  "DEVELOPMENT",
  "CENTRAL_STORE",
  "HEAD_OFFICE",
  "PLANT",
  "DEPOT",
  "OTHER",
] as const satisfies readonly SiteType[];

export const UNIT_DIMENSIONS = [
  "MASS",
  "VOLUME",
  "LENGTH",
  "AREA",
  "COUNT",
  "TIME",
] as const satisfies readonly UnitDimension[];

export const CONVERSION_SCOPES = [
  "GLOBAL",
  "MATERIAL",
  "VENDOR",
  "MATERIAL_VENDOR",
] as const satisfies readonly ConversionScope[];

export const MATERIAL_TRACKING = [
  "QUANTITY",
  "BATCH",
  "SERIAL",
] as const satisfies readonly MaterialTracking[];

export const WAREHOUSE_TYPES = [
  "SITE_STORE",
  "CENTRAL",
  "TRANSIT",
  "QUARANTINE",
] as const satisfies readonly WarehouseType[];

export const SCOPE_TYPES = [
  "GLOBAL",
  "COMPANY",
  "PROJECT",
  "SITE",
  "DEPARTMENT",
] as const satisfies readonly ScopeType[];

/** Mirrors identity/domain/enums.py UserStatus (not in the OpenAPI schema: the API returns it as a plain string). */
export const USER_STATUSES = [
  "INVITED",
  "ACTIVE",
  "PASSWORD_RESET_REQUIRED",
  "SUSPENDED",
  "DEACTIVATED",
] as const;

export const vendorStatusOptions = options(VENDOR_STATUSES);
export const vendorTypeOptions = options(VENDOR_TYPES);
export const projectStatusOptions = options(PROJECT_STATUSES);
export const projectTypeOptions = options(PROJECT_TYPES);
export const phaseStatusOptions = options(PHASE_STATUSES);
export const siteTypeOptions = options(SITE_TYPES);
export const unitDimensionOptions = options(UNIT_DIMENSIONS);
export const conversionScopeOptions = options(CONVERSION_SCOPES);
export const materialTrackingOptions = options(MATERIAL_TRACKING);
export const warehouseTypeOptions = options(WAREHOUSE_TYPES);
export const scopeTypeOptions = options(SCOPE_TYPES);
export const userStatusOptions = options(USER_STATUSES);
