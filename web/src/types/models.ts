/**
 * Named aliases over the generated OpenAPI types (`npm run api-types`).
 *
 * Screens import from here, never from `api.d.ts` directly, so a regenerated
 * schema that renames or drops a field fails the type check at every use.
 */
import type { components } from "./api";

type S = components["schemas"];

export type MeResponse = S["MeResponse"];
export type UserProfile = S["UserProfile"];
export type TokenResponse = S["TokenResponse"];
export type MessageResponse = S["MessageResponse"];
export type SessionSummary = S["SessionSummary"];

export type ProjectListItem = S["ProjectListItem"];
export type ProjectDetail = S["ProjectDetail"];
export type ProjectRead = S["ProjectRead"];
export type ProjectCreate = S["ProjectCreate"];
export type ProjectUpdate = S["ProjectUpdate"];
export type ProjectPhaseRead = S["ProjectPhaseRead"];
export type ProjectPhaseUpdate = S["ProjectPhaseUpdate"];
export type ProjectStatus = S["ProjectStatus"];
export type ProjectType = S["ProjectType"];
export type PhaseStatus = S["PhaseStatus"];

export type SiteListItem = S["SiteListItem"];
export type SiteRead = S["SiteRead"];
export type SiteCreate = S["SiteCreate"];
export type SiteUpdate = S["SiteUpdate"];
export type SiteType = S["SiteType"];
export type GeofenceUpdate = S["GeofenceUpdate"];
export type Point = S["Point"];

export type DepartmentRead = S["DepartmentRead"];
export type DepartmentCreate = S["DepartmentCreate"];
export type CostCenterRead = S["CostCenterRead"];
export type CostCenterCreate = S["CostCenterCreate"];

export type UserAdminRead = S["UserAdminRead"];
export type UserInvite = S["UserInvite"];
export type UserInviteResponse = S["UserInviteResponse"];
export type UserAdminUpdate = S["UserAdminUpdate"];
export type PasswordResetIssued = S["PasswordResetIssued"];
export type RoleGrantRead = S["RoleGrantRead"];
export type RoleGrantCreate = S["RoleGrantCreate"];
export type ScopeType = S["ScopeType"];
export type RoleRead = S["RoleRead"];
export type RoleDetail = S["RoleDetail"];
export type RoleCreate = S["RoleCreate"];
export type PermissionRead = S["PermissionRead"];

export type UnitRead = S["UnitRead"];
export type UnitCreate = S["UnitCreate"];
export type UnitUpdate = S["UnitUpdate"];
export type UnitDimension = S["UnitDimension"];
export type ConversionRead = S["ConversionRead"];
export type ConversionCreate = S["ConversionCreate"];
export type ConversionScope = S["ConversionScope"];
export type ConversionResolveRequest = S["ConversionResolveRequest"];
export type ConversionResolveResponse = S["ConversionResolveResponse"];
export type CalibrationReadingRead = S["CalibrationReadingRead"];
export type CalibrationReadingCreate = S["CalibrationReadingCreate"];
export type CalibrationStatsResponse = S["CalibrationStatsResponse"];
export type CalibrationConfirm = S["CalibrationConfirm"];

export type MaterialCategoryRead = S["MaterialCategoryRead"];
export type MaterialCategoryCreate = S["MaterialCategoryCreate"];
export type MaterialListItem = S["MaterialListItem"];
export type MaterialDetail = S["MaterialDetail"];
export type MaterialRead = S["MaterialRead"];
export type MaterialCreate = S["MaterialCreate"];
export type MaterialUpdate = S["MaterialUpdate"];
export type MaterialTracking = S["MaterialTracking"];

export type TruckTypeRead = S["TruckTypeRead"];
export type TruckTypeCreate = S["TruckTypeCreate"];
export type WarehouseRead = S["WarehouseRead"];
export type WarehouseCreate = S["WarehouseCreate"];
export type WarehouseType = S["WarehouseType"];

export type VendorListItem = S["VendorListItem"];
export type VendorDetail = S["VendorDetail"];
export type VendorRead = S["VendorRead"];
export type VendorCreate = S["VendorCreate"];
export type VendorUpdate = S["VendorUpdate"];
export type VendorStatus = S["VendorStatus"];
export type VendorType = S["VendorType"];
export type VendorContactIn = S["VendorContactIn"];
export type VendorBankAccountRead = S["VendorBankAccountRead"];
export type VendorBankAccountIn = S["VendorBankAccountIn"];

export type NotificationInbox = S["NotificationInbox"];
export type NotificationRead = S["NotificationRead"];
