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
  SlidersHorizontal,
  BadgeDollarSign,
  PackageCheck,
  ClipboardList,
  Boxes as StockIcon,
  ReceiptText,
  History,
  PackageMinus,
  ArrowRightLeft,
  LayoutDashboard,
  TrendingUp,
  Landmark,
  CalendarRange,
  BookText,
  Table2,
  NotebookText,
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
      {
        label: "Vendor rates",
        to: "/vendor-rates",
        icon: BadgeDollarSign,
        permission: "rates.view",
      },
      {
        label: "Rate overview",
        to: "/vendor-rates/grid",
        icon: TrendingUp,
        permission: "rates.view",
      },
    ],
  },
  {
    label: "Material tracking",
    items: [
      {
        label: "Delivery dashboard",
        to: "/deliveries/dashboard",
        icon: LayoutDashboard,
        permission: "deliveries.view",
      },
      {
        label: "Deliveries",
        to: "/deliveries",
        icon: PackageCheck,
        permission: "deliveries.view",
      },
      {
        label: "Delivery review",
        to: "/deliveries/review",
        icon: ClipboardList,
        permission: "deliveries.review",
      },
      { label: "Goods received", to: "/grns", icon: ReceiptText, permission: "grn.view" },
      { label: "Stock", to: "/inventory", icon: StockIcon, permission: "inventory.view" },
      {
        label: "Stock ledger",
        to: "/inventory/ledger",
        icon: History,
        permission: "inventory.view",
      },
      {
        label: "Stock issues",
        to: "/inventory/issues",
        icon: PackageMinus,
        permission: "inventory.view",
      },
      {
        label: "Stock transfers",
        to: "/inventory/transfers",
        icon: ArrowRightLeft,
        permission: "inventory.view",
      },
      {
        label: "Stock adjustments",
        to: "/inventory/adjustments",
        icon: SlidersHorizontal,
        permission: "inventory.view",
      },
    ],
  },
  {
    label: "Finance",
    items: [
      {
        label: "Chart of accounts",
        to: "/finance/accounts",
        icon: Landmark,
        permission: "finance.coa.view",
      },
      {
        label: "Accounting periods",
        to: "/finance/periods",
        icon: CalendarRange,
        permission: "finance.gl.view",
      },
      {
        label: "Journal entries",
        to: "/finance/journal-entries",
        icon: BookText,
        permission: "finance.gl.view",
      },
      {
        label: "Trial balance",
        to: "/finance/trial-balance",
        icon: Table2,
        permission: "finance.gl.view",
      },
      {
        label: "General ledger",
        to: "/finance/general-ledger",
        icon: NotebookText,
        permission: "finance.gl.view",
      },
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
      {
        label: "Business rules",
        to: "/business-rules",
        icon: SlidersHorizontal,
        permission: "settings.view",
      },
      { label: "System status", to: "/status", icon: Activity },
    ],
  },
];
