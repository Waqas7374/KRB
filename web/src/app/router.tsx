import { lazy, type ComponentType, type ReactNode } from "react";
import { createBrowserRouter } from "react-router-dom";

import { RequirePermission } from "@/features/auth/permission-gate";
import { RequireAuth } from "@/features/auth/RequireAuth";

import { AppShell } from "./AppShell";
import { NotFound, RouteError } from "./route-states";

/**
 * Routes are declared centrally so each one carries the permission it needs:
 * the page renders a Forbidden panel instead of firing a query that would
 * 403. Feature screens are code-split per module (docs/08 §1 budget: the
 * shell loads under 200 KB gzipped).
 */

// `ComponentType<any>`, not the bare (props-less) `ComponentType`: a lazily
// loaded module may also export a plain helper component with required props
// (e.g. RateGridPage's `Sparkline`) alongside the page itself, and the
// constraint only needs to admit that shape — the cast below already narrows
// to the one export this actually renders.
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- see the comment above
function page<T extends Record<string, ComponentType<any>>>(
  loader: () => Promise<T>,
  name: keyof T,
) {
  return lazy(async () => ({ default: (await loader())[name] as ComponentType }));
}

const LoginPage = page(() => import("@/features/auth/LoginPage"), "LoginPage");
const ChangePasswordPage = page(
  () => import("@/features/auth/ChangePasswordPage"),
  "ChangePasswordPage",
);
const ForgotPasswordPage = page(
  () => import("@/features/auth/PasswordResetPages"),
  "ForgotPasswordPage",
);
const ResetPasswordPage = page(
  () => import("@/features/auth/PasswordResetPages"),
  "ResetPasswordPage",
);
const AccountPage = page(() => import("@/features/auth/AccountPage"), "AccountPage");
const HomePage = page(() => import("@/features/home/HomePage"), "HomePage");
const SystemStatusPage = page(
  () => import("@/features/system/SystemStatusPage"),
  "SystemStatusPage",
);

const ProjectsListPage = page(
  () => import("@/features/projects/ProjectsListPage"),
  "ProjectsListPage",
);
const ProjectFormPage = page(
  () => import("@/features/projects/ProjectFormPage"),
  "ProjectFormPage",
);
const ProjectDetailPage = page(
  () => import("@/features/projects/ProjectDetailPage"),
  "ProjectDetailPage",
);
const SitesListPage = page(() => import("@/features/sites/SitesListPage"), "SitesListPage");
const SiteFormPage = page(() => import("@/features/sites/SiteFormPage"), "SiteFormPage");
const SiteDetailPage = page(() => import("@/features/sites/SiteDetailPage"), "SiteDetailPage");

const VendorsListPage = page(() => import("@/features/vendors/VendorsListPage"), "VendorsListPage");
const VendorFormPage = page(() => import("@/features/vendors/VendorFormPage"), "VendorFormPage");
const VendorDetailPage = page(
  () => import("@/features/vendors/VendorDetailPage"),
  "VendorDetailPage",
);

const UsersListPage = page(() => import("@/features/users/UsersListPage"), "UsersListPage");
const UserDetailPage = page(() => import("@/features/users/UserDetailPage"), "UserDetailPage");
const RolesListPage = page(() => import("@/features/roles/RolesListPage"), "RolesListPage");
const RoleDetailPage = page(() => import("@/features/roles/RoleDetailPage"), "RoleDetailPage");

const MaterialsListPage = page(
  () => import("@/features/materials/MaterialPages"),
  "MaterialsListPage",
);
const MaterialFormPage = page(
  () => import("@/features/materials/MaterialPages"),
  "MaterialFormPage",
);
const MaterialDetailPage = page(
  () => import("@/features/materials/MaterialPages"),
  "MaterialDetailPage",
);
const ConversionsPage = page(
  () => import("@/features/masterdata/ConversionsPage"),
  "ConversionsPage",
);
const CalibrationPage = page(
  () => import("@/features/masterdata/CalibrationPage"),
  "CalibrationPage",
);
const ref = () => import("@/features/masterdata/ReferencePages");
const DepartmentsPage = page(ref, "DepartmentsPage");
const CostCentersPage = page(ref, "CostCentersPage");
const MaterialCategoriesPage = page(ref, "MaterialCategoriesPage");
const UnitsPage = page(ref, "UnitsPage");
const TruckTypesPage = page(ref, "TruckTypesPage");
const WarehousesPage = page(ref, "WarehousesPage");

const ApprovalInboxPage = page(
  () => import("@/features/approvals/ApprovalInboxPage"),
  "ApprovalInboxPage",
);
const ApprovalDocumentPage = page(
  () => import("@/features/approvals/ApprovalDocumentPage"),
  "ApprovalDocumentPage",
);
const WorkflowsPage = page(() => import("@/features/approvals/WorkflowsPage"), "WorkflowsPage");
const pr = () => import("@/features/procurement/PurchaseRequestPages");
const PurchaseRequestsListPage = page(pr, "PurchaseRequestsListPage");
const PurchaseRequestFormPage = page(pr, "PurchaseRequestFormPage");
const PurchaseRequestDetailPage = page(pr, "PurchaseRequestDetailPage");

const rfqPages = () => import("@/features/procurement/RfqPages");
const RfqsListPage = page(rfqPages, "RfqsListPage");
const RfqFormPage = page(rfqPages, "RfqFormPage");
const RfqDetailPage = page(rfqPages, "RfqDetailPage");
const quotationPages = () => import("@/features/procurement/QuotationPages");
const QuotationFormPage = page(quotationPages, "QuotationFormPage");
const QuotationDetailPage = page(quotationPages, "QuotationDetailPage");
const ComparisonPage = page(quotationPages, "ComparisonPage");
const poPages = () => import("@/features/procurement/PurchaseOrderPages");
const PurchaseOrdersListPage = page(poPages, "PurchaseOrdersListPage");
const PurchaseOrderFormPage = page(poPages, "PurchaseOrderFormPage");
const PurchaseOrderDetailPage = page(poPages, "PurchaseOrderDetailPage");

const BusinessRulesPage = page(
  () => import("@/features/rules/BusinessRulesPage"),
  "BusinessRulesPage",
);

const VendorRatesPage = page(() => import("@/features/rates/VendorRatesPage"), "VendorRatesPage");
const RateGridPage = page(() => import("@/features/rates/RateGridPage"), "RateGridPage");
const DeliveryDashboardPage = page(
  () => import("@/features/deliveries/DeliveryDashboardPage"),
  "DeliveryDashboardPage",
);

const dl = () => import("@/features/deliveries/DeliveryPages");
const DeliveriesListPage = page(dl, "DeliveriesListPage");
const ReviewQueuePage = page(dl, "ReviewQueuePage");
const DeliveryFormPage = page(dl, "DeliveryFormPage");
const DeliveryDetailPage = page(dl, "DeliveryDetailPage");

const grnPages = () => import("@/features/grn/GrnPages");
const GrnListPage = page(grnPages, "GrnListPage");
const GrnDetailPage = page(grnPages, "GrnDetailPage");
const counterPages = () => import("@/features/grn/CounterPurchasePage");
const CounterPurchasePage = page(counterPages, "CounterPurchasePage");
const invPages = () => import("@/features/inventory/InventoryPages");
const StockBalancesPage = page(invPages, "StockBalancesPage");
const StockLedgerPage = page(invPages, "StockLedgerPage");
const issuePages = () => import("@/features/stock/IssuePages");
const IssueListPage = page(issuePages, "IssueListPage");
const IssueFormPage = page(issuePages, "IssueFormPage");
const IssueDetailPage = page(issuePages, "IssueDetailPage");
const transferPages = () => import("@/features/stock/TransferPages");
const TransferListPage = page(transferPages, "TransferListPage");
const TransferFormPage = page(transferPages, "TransferFormPage");
const TransferDetailPage = page(transferPages, "TransferDetailPage");
const adjustmentPages = () => import("@/features/stock/AdjustmentPages");
const AdjustmentListPage = page(adjustmentPages, "AdjustmentListPage");
const AdjustmentFormPage = page(adjustmentPages, "AdjustmentFormPage");
const AdjustmentDetailPage = page(adjustmentPages, "AdjustmentDetailPage");

const AccountsPage = page(() => import("@/features/finance/AccountsPage"), "AccountsPage");
const PeriodsPage = page(() => import("@/features/finance/PeriodsPage"), "PeriodsPage");
const jePages = () => import("@/features/finance/JournalEntryPages");
const JournalEntryListPage = page(jePages, "JournalEntryListPage");
const JournalEntryFormPage = page(jePages, "JournalEntryFormPage");
const JournalEntryDetailPage = page(jePages, "JournalEntryDetailPage");
const financeReportPages = () => import("@/features/finance/ReportsPages");
const TrialBalancePage = page(financeReportPages, "TrialBalancePage");
const GeneralLedgerPage = page(financeReportPages, "GeneralLedgerPage");
const PostingRulesPage = page(
  () => import("@/features/finance/PostingRulesPage"),
  "PostingRulesPage",
);
const budgetPages = () => import("@/features/finance/BudgetPages");
const BudgetListPage = page(budgetPages, "BudgetListPage");
const BudgetFormPage = page(budgetPages, "BudgetFormPage");
const BudgetDetailPage = page(budgetPages, "BudgetDetailPage");
const TaxCodesPage = page(() => import("@/features/finance/TaxCodesPage"), "TaxCodesPage");
const viPages = () => import("@/features/finance/VendorInvoicePages");
const VendorInvoiceListPage = page(viPages, "VendorInvoiceListPage");
const VendorInvoiceFormPage = page(viPages, "VendorInvoiceFormPage");
const VendorInvoiceDetailPage = page(viPages, "VendorInvoiceDetailPage");
const PayablesAgingPage = page(
  () => import("@/features/finance/PayablesAgingPage"),
  "PayablesAgingPage",
);
const BankAccountsPage = page(
  () => import("@/features/finance/BankAccountsPage"),
  "BankAccountsPage",
);
const prPages = () => import("@/features/finance/PaymentRequestPages");
const PaymentRequestListPage = page(prPages, "PaymentRequestListPage");
const PaymentRequestFormPage = page(prPages, "PaymentRequestFormPage");
const PaymentRequestDetailPage = page(prPages, "PaymentRequestDetailPage");
const paymentPages = () => import("@/features/finance/PaymentPages");
const PaymentListPage = page(paymentPages, "PaymentListPage");
const PaymentFormPage = page(paymentPages, "PaymentFormPage");
const PaymentDetailPage = page(paymentPages, "PaymentDetailPage");

const gate = (permission: string, element: ReactNode) => (
  <RequirePermission permission={permission}>{element}</RequirePermission>
);

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },
  {
    path: "/change-password",
    element: (
      <RequireAuth>
        <ChangePasswordPage />
      </RequireAuth>
    ),
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    errorElement: <RouteError />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "account", element: <AccountPage /> },
      { path: "status", element: <SystemStatusPage /> },

      { path: "projects", element: gate("projects.view", <ProjectsListPage />) },
      { path: "projects/new", element: gate("projects.create", <ProjectFormPage />) },
      { path: "projects/:projectId", element: gate("projects.view", <ProjectDetailPage />) },
      { path: "projects/:projectId/edit", element: gate("projects.update", <ProjectFormPage />) },

      { path: "sites", element: gate("sites.view", <SitesListPage />) },
      { path: "sites/new", element: gate("sites.create", <SiteFormPage />) },
      { path: "sites/:siteId", element: gate("sites.view", <SiteDetailPage />) },
      { path: "sites/:siteId/edit", element: gate("sites.update", <SiteFormPage />) },

      { path: "departments", element: gate("departments.view", <DepartmentsPage />) },
      { path: "cost-centers", element: gate("departments.view", <CostCentersPage />) },

      { path: "approvals", element: gate("approvals.view", <ApprovalInboxPage />) },
      {
        path: "approvals/document/:docType/:docId",
        element: gate("approvals.view", <ApprovalDocumentPage />),
      },
      { path: "approval-workflows", element: gate("approvals.view", <WorkflowsPage />) },
      {
        path: "purchase-requests",
        element: gate("procurement.pr.view", <PurchaseRequestsListPage />),
      },
      {
        path: "purchase-requests/new",
        element: gate("procurement.pr.create", <PurchaseRequestFormPage />),
      },
      {
        path: "purchase-requests/:requestId",
        element: gate("procurement.pr.view", <PurchaseRequestDetailPage />),
      },
      {
        path: "purchase-requests/:requestId/edit",
        element: gate("procurement.pr.create", <PurchaseRequestFormPage />),
      },

      { path: "vendor-rates", element: gate("rates.view", <VendorRatesPage />) },
      { path: "vendor-rates/grid", element: gate("rates.view", <RateGridPage />) },
      { path: "deliveries", element: gate("deliveries.view", <DeliveriesListPage />) },
      { path: "deliveries/new", element: gate("deliveries.create", <DeliveryFormPage />) },
      { path: "deliveries/review", element: gate("deliveries.review", <ReviewQueuePage />) },
      { path: "deliveries/dashboard", element: gate("deliveries.view", <DeliveryDashboardPage />) },
      { path: "deliveries/:deliveryId", element: gate("deliveries.view", <DeliveryDetailPage />) },
      {
        path: "deliveries/:deliveryId/edit",
        element: gate("deliveries.create", <DeliveryFormPage />),
      },
      { path: "grns", element: gate("grn.view", <GrnListPage />) },
      { path: "grns/new", element: gate("grn.create", <CounterPurchasePage />) },
      { path: "grns/:grnId", element: gate("grn.view", <GrnDetailPage />) },
      { path: "inventory", element: gate("inventory.view", <StockBalancesPage />) },
      { path: "inventory/ledger", element: gate("inventory.view", <StockLedgerPage />) },
      { path: "inventory/issues", element: gate("inventory.view", <IssueListPage />) },
      { path: "inventory/issues/new", element: gate("inventory.issue", <IssueFormPage />) },
      { path: "inventory/issues/:issueId", element: gate("inventory.view", <IssueDetailPage />) },
      { path: "inventory/transfers", element: gate("inventory.view", <TransferListPage />) },
      {
        path: "inventory/transfers/new",
        element: gate("inventory.transfer", <TransferFormPage />),
      },
      {
        path: "inventory/transfers/:transferId",
        element: gate("inventory.view", <TransferDetailPage />),
      },
      { path: "inventory/adjustments", element: gate("inventory.view", <AdjustmentListPage />) },
      {
        path: "inventory/adjustments/new",
        element: gate("inventory.adjust", <AdjustmentFormPage />),
      },
      {
        path: "inventory/adjustments/:adjustmentId",
        element: gate("inventory.view", <AdjustmentDetailPage />),
      },
      {
        path: "inventory/adjustments/:adjustmentId/edit",
        element: gate("inventory.adjust", <AdjustmentFormPage />),
      },

      { path: "finance/accounts", element: gate("finance.coa.view", <AccountsPage />) },
      { path: "finance/periods", element: gate("finance.gl.view", <PeriodsPage />) },
      {
        path: "finance/journal-entries",
        element: gate("finance.gl.view", <JournalEntryListPage />),
      },
      {
        path: "finance/journal-entries/new",
        element: gate("finance.gl.create", <JournalEntryFormPage />),
      },
      {
        path: "finance/journal-entries/:jeId",
        element: gate("finance.gl.view", <JournalEntryDetailPage />),
      },
      {
        path: "finance/journal-entries/:jeId/edit",
        element: gate("finance.gl.create", <JournalEntryFormPage />),
      },
      { path: "finance/trial-balance", element: gate("finance.gl.view", <TrialBalancePage />) },
      { path: "finance/general-ledger", element: gate("finance.gl.view", <GeneralLedgerPage />) },
      {
        path: "finance/posting-rules",
        element: gate("finance.coa.view", <PostingRulesPage />),
      },
      { path: "finance/budgets", element: gate("finance.budget.view", <BudgetListPage />) },
      { path: "finance/budgets/new", element: gate("finance.budget.create", <BudgetFormPage />) },
      {
        path: "finance/budgets/:budgetId",
        element: gate("finance.budget.view", <BudgetDetailPage />),
      },
      {
        path: "finance/budgets/:budgetId/edit",
        element: gate("finance.budget.create", <BudgetFormPage />),
      },
      { path: "finance/tax-codes", element: gate("finance.coa.view", <TaxCodesPage />) },
      {
        path: "finance/vendor-invoices",
        element: gate("finance.ap.view", <VendorInvoiceListPage />),
      },
      {
        path: "finance/vendor-invoices/new",
        element: gate("finance.ap.create", <VendorInvoiceFormPage />),
      },
      {
        path: "finance/vendor-invoices/:invoiceId",
        element: gate("finance.ap.view", <VendorInvoiceDetailPage />),
      },
      {
        path: "finance/vendor-invoices/:invoiceId/edit",
        element: gate("finance.ap.create", <VendorInvoiceFormPage />),
      },
      {
        path: "finance/payables/aging",
        element: gate("finance.ap.view", <PayablesAgingPage />),
      },
      {
        path: "finance/bank-accounts",
        element: gate("finance.coa.view", <BankAccountsPage />),
      },
      {
        path: "finance/payment-requests",
        element: gate("finance.payment.view", <PaymentRequestListPage />),
      },
      {
        path: "finance/payment-requests/new",
        element: gate("finance.payment.request", <PaymentRequestFormPage />),
      },
      {
        path: "finance/payment-requests/:requestId",
        element: gate("finance.payment.view", <PaymentRequestDetailPage />),
      },
      {
        path: "finance/payment-requests/:requestId/edit",
        element: gate("finance.payment.request", <PaymentRequestFormPage />),
      },
      { path: "finance/payments", element: gate("finance.payment.view", <PaymentListPage />) },
      {
        path: "finance/payments/new",
        element: gate("finance.payment.execute", <PaymentFormPage />),
      },
      {
        path: "finance/payments/:paymentId",
        element: gate("finance.payment.view", <PaymentDetailPage />),
      },
      { path: "rfqs", element: gate("procurement.rfq.view", <RfqsListPage />) },
      { path: "rfqs/new", element: gate("procurement.rfq.create", <RfqFormPage />) },
      { path: "rfqs/:rfqId", element: gate("procurement.rfq.view", <RfqDetailPage />) },
      { path: "rfqs/:rfqId/edit", element: gate("procurement.rfq.create", <RfqFormPage />) },
      {
        path: "rfqs/:rfqId/comparison",
        element: gate("procurement.quotation.view", <ComparisonPage />),
      },
      {
        path: "rfqs/:rfqId/quotations/new",
        element: gate("procurement.quotation.record", <QuotationFormPage />),
      },
      {
        path: "quotations/:quotationId",
        element: gate("procurement.quotation.view", <QuotationDetailPage />),
      },
      {
        path: "quotations/:quotationId/edit",
        element: gate("procurement.quotation.record", <QuotationFormPage />),
      },
      { path: "purchase-orders", element: gate("procurement.po.view", <PurchaseOrdersListPage />) },
      {
        path: "purchase-orders/new",
        element: gate("procurement.po.create", <PurchaseOrderFormPage />),
      },
      {
        path: "purchase-orders/:poId",
        element: gate("procurement.po.view", <PurchaseOrderDetailPage />),
      },
      {
        path: "purchase-orders/:poId/edit",
        element: gate("procurement.po.create", <PurchaseOrderFormPage />),
      },

      { path: "vendors", element: gate("vendors.view", <VendorsListPage />) },
      { path: "vendors/new", element: gate("vendors.create", <VendorFormPage />) },
      { path: "vendors/:vendorId", element: gate("vendors.view", <VendorDetailPage />) },
      { path: "vendors/:vendorId/edit", element: gate("vendors.update", <VendorFormPage />) },

      { path: "materials", element: gate("materials.view", <MaterialsListPage />) },
      { path: "materials/new", element: gate("materials.create", <MaterialFormPage />) },
      { path: "materials/:materialId", element: gate("materials.view", <MaterialDetailPage />) },
      {
        path: "materials/:materialId/edit",
        element: gate("materials.update", <MaterialFormPage />),
      },
      { path: "material-categories", element: gate("materials.view", <MaterialCategoriesPage />) },
      { path: "units", element: gate("units.view", <UnitsPage />) },
      { path: "unit-conversions", element: gate("units.view", <ConversionsPage />) },
      { path: "calibration", element: gate("units.view", <CalibrationPage />) },
      { path: "truck-types", element: gate("materials.view", <TruckTypesPage />) },
      { path: "warehouses", element: gate("warehouses.view", <WarehousesPage />) },

      { path: "business-rules", element: gate("settings.view", <BusinessRulesPage />) },

      { path: "users", element: gate("users.view", <UsersListPage />) },
      { path: "users/:userId", element: gate("users.view", <UserDetailPage />) },
      { path: "roles", element: gate("roles.view", <RolesListPage />) },
      { path: "roles/:roleId", element: gate("roles.view", <RoleDetailPage />) },

      { path: "*", element: <NotFound /> },
    ],
  },
]);
