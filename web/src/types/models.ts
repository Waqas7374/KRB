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

export type PurchaseRequestListItem = S["PurchaseRequestListItem"];
export type PurchaseRequestRead = S["PurchaseRequestRead"];
export type PurchaseRequestItemRead = S["PurchaseRequestItemRead"];
export type PurchaseRequestCreate = S["PurchaseRequestCreate"];

export type RfqListItem = S["RfqListItem"];
export type RfqRead = S["RfqRead"];
export type RfqItemRead = S["RfqItemRead"];
export type RfqVendorRead = S["RfqVendorRead"];
export type QuotationListItem = S["QuotationListItem"];
export type QuotationRead = S["QuotationRead"];
export type QuotationItemRead = S["QuotationItemRead"];
export type ComparisonRead = S["ComparisonRead"];
export type ComparisonColumnRead = S["ComparisonColumnRead"];
export type ComparisonRowRead = S["ComparisonRowRead"];
export type ComparisonCellRead = S["ComparisonCellRead"];
export type PurchaseOrderListItem = S["PurchaseOrderListItem"];
export type PurchaseOrderRead = S["PurchaseOrderRead"];
export type PurchaseOrderItemRead = S["PurchaseOrderItemRead"];

export type ApprovalRequestRead = S["RequestRead"];
export type ApprovalStepRead = S["RequestStepRead"];
export type ApprovalActionRead = S["ActionRead"];
export type ApprovalInboxItem = S["InboxItemRead"];
export type ApprovalDecisionResponse = S["DecisionResponse"];
export type ApprovalWorkflowRead = S["WorkflowRead"];
export type ApprovalDocumentType = S["DocumentTypeRead"];
export type ApprovalSimulation = S["SimulateResponse"];

export type VendorRateRead = S["RateRead"];
export type VendorRateHistory = S["RateHistoryRead"];
export type ResolvedRate = S["ResolvedRateRead"];

export type DeliveryListItem = S["DeliveryListItem"];
export type DeliveryRead = S["DeliveryRead"];
export type DeliveryItemRead = S["DeliveryItemRead"];
export type DeliveryFlagRead = S["FlagRead"];
export type DeliveryReviewRead = S["ReviewRead"];
export type DeliveryCreate = S["DeliveryCreate"];
export type DeliverySummary = S["DeliverySummaryRead"];
export type VendorRateGridRow = S["RateGridRow"];

export type GrnListItem = S["GrnListItem"];
export type GrnRead = S["GrnRead"];
export type GrnItemRead = S["GrnItemRead"];
export type StockBalance = S["BalanceRead"];
export type StockLineRead = S["LineRead"];
export type AttachmentRead = S["AttachmentRead"];
export type AttachmentUpload = S["PresignUploadResponse"];
export type AttachmentDownload = S["DownloadUrlResponse"];
export type StockIssueListItem = S["IssueListItem"];
export type StockIssueRead = S["IssueRead"];
export type StockIssueCreate = S["IssueCreate"];
export type StockTransferListItem = S["TransferListItem"];
export type StockTransferRead = S["TransferRead"];
export type StockTransferCreate = S["TransferCreate"];
export type StockAdjustmentListItem = S["AdjustmentListItem"];
export type StockAdjustmentRead = S["AdjustmentRead"];
export type StockAdjustmentCreate = S["AdjustmentCreate"];
export type WarehouseOption = S["WarehouseOptionRead"];
export type StockMovement = S["LedgerRead"];

export type BusinessRuleRead = S["RuleRead"];
export type BusinessRuleCreate = S["RuleCreate"];
export type BusinessRuleUpdate = S["RuleUpdate"];
export type BusinessRuleType = S["RuleTypeRead"];
export type RuleResolution = S["ResolveResponse"];
export type RuleVerdict = S["VerdictRead"];

export type NotificationInbox = S["NotificationInbox"];
export type NotificationRead = S["NotificationRead"];

export type AccountType = S["AccountType"];
export type AccountRead = S["AccountRead"];
export type AccountNodeRead = S["AccountNodeRead"];
export type AccountCreate = S["AccountCreate"];
export type AccountEdit = S["AccountEdit"];
export type PeriodRead = S["PeriodRead"];
export type GenerateFiscalYear = S["GenerateFiscalYear"];
export type JournalLineIn = S["JournalLineIn"];
export type JournalLineRead = S["JournalLineRead"];
export type JournalEntryListItem = S["JournalEntryListItem"];
export type JournalEntryRead = S["JournalEntryRead"];
export type JournalEntryCreate = S["JournalEntryCreate"];
export type TrialBalanceRead = S["TrialBalanceRead"];
export type TrialBalanceRowRead = S["TrialBalanceRowRead"];
export type GeneralLedgerRead = S["GeneralLedgerRead"];
export type GeneralLedgerRowRead = S["GeneralLedgerRowRead"];
export type FinanceCancelBody = S["app__modules__finance__schemas__CancelBody"];

export type PostingRuleRead = S["PostingRuleRead"];
export type PostingRuleCreate = S["PostingRuleCreate"];
export type PostingRuleEdit = S["PostingRuleEdit"];
export type BudgetLineIn = S["BudgetLineIn"];
export type BudgetCreate = S["BudgetCreate"];
export type BudgetRevise = S["BudgetRevise"];
export type BudgetLineRead = S["BudgetLineRead"];
export type BudgetListItem = S["BudgetListItem"];
export type BudgetRead = S["BudgetRead"];
export type BudgetCommitmentRead = S["BudgetCommitmentRead"];
