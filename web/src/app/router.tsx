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

function page<T extends Record<string, ComponentType>>(loader: () => Promise<T>, name: keyof T) {
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
const WorkflowsPage = page(() => import("@/features/approvals/WorkflowsPage"), "WorkflowsPage");
const pr = () => import("@/features/procurement/PurchaseRequestPages");
const PurchaseRequestsListPage = page(pr, "PurchaseRequestsListPage");
const PurchaseRequestFormPage = page(pr, "PurchaseRequestFormPage");
const PurchaseRequestDetailPage = page(pr, "PurchaseRequestDetailPage");

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

      { path: "users", element: gate("users.view", <UsersListPage />) },
      { path: "users/:userId", element: gate("users.view", <UserDetailPage />) },
      { path: "roles", element: gate("roles.view", <RolesListPage />) },
      { path: "roles/:roleId", element: gate("roles.view", <RoleDetailPage />) },

      { path: "*", element: <NotFound /> },
    ],
  },
]);
