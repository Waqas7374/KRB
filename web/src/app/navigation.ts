import {
  Boxes,
  Building2,
  Home,
  type LucideIcon,
  MapPin,
  Ruler,
  Scale,
  ShieldCheck,
  Truck,
  Users,
  Warehouse,
  Activity,
  FolderKanban,
  Network,
  Wallet,
  Tags,
  ClipboardCheck,
  ShoppingCart,
  Workflow,
  ArrowLeftRight,
  FileQuestion,
  FileText,
} from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Hidden unless the user holds this permission. */
  permission?: string;
}

export interface NavSection {
  label: string | null;
  items: NavItem[];
}

/**
 * The left navigation. Items the user cannot use are hidden rather than
 * disabled (docs/08 §2) — a site engineer should not scroll past Finance.
 */
export const NAVIGATION: NavSection[] = [
  { label: null, items: [{ label: "Home", to: "/", icon: Home }] },
  {
    label: "Organisation",
    items: [
      { label: "Projects", to: "/projects", icon: FolderKanban, permission: "projects.view" },
      { label: "Sites", to: "/sites", icon: MapPin, permission: "sites.view" },
      { label: "Departments", to: "/departments", icon: Network, permission: "departments.view" },
      { label: "Cost centres", to: "/cost-centers", icon: Wallet, permission: "departments.view" },
    ],
  },
  {
    label: "Procurement",
    items: [
      { label: "Approvals", to: "/approvals", icon: ClipboardCheck, permission: "approvals.view" },
      {
        label: "Purchase requests",
        to: "/purchase-requests",
        icon: ShoppingCart,
        permission: "procurement.pr.view",
      },
      { label: "RFQs", to: "/rfqs", icon: FileQuestion, permission: "procurement.rfq.view" },
      {
        label: "Purchase orders",
        to: "/purchase-orders",
        icon: FileText,
        permission: "procurement.po.view",
      },
      { label: "Vendors", to: "/vendors", icon: Building2, permission: "vendors.view" },
    ],
  },
  {
    label: "Master data",
    items: [
      { label: "Materials", to: "/materials", icon: Boxes, permission: "materials.view" },
      { label: "Categories", to: "/material-categories", icon: Tags, permission: "materials.view" },
      { label: "Units", to: "/units", icon: Ruler, permission: "units.view" },
      {
        label: "Conversions",
        to: "/unit-conversions",
        icon: ArrowLeftRight,
        permission: "units.view",
      },
      { label: "Calibration", to: "/calibration", icon: Scale, permission: "units.view" },
      { label: "Truck types", to: "/truck-types", icon: Truck, permission: "materials.view" },
      { label: "Warehouses", to: "/warehouses", icon: Warehouse, permission: "warehouses.view" },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Users", to: "/users", icon: Users, permission: "users.view" },
      { label: "Roles", to: "/roles", icon: ShieldCheck, permission: "roles.view" },
      {
        label: "Approval workflows",
        to: "/approval-workflows",
        icon: Workflow,
        permission: "approvals.view",
      },
      { label: "System status", to: "/status", icon: Activity },
    ],
  },
];
