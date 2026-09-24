import {
  KeyRound,
  LogOut,
  Menu,
  Monitor,
  Moon,
  PanelLeftClose,
  Sun,
  UserRound,
} from "lucide-react";
import { Suspense, useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { ErrorBoundary } from "@/components/error-boundary";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/menu";
import { PageSkeleton } from "@/components/ui/states";
import { Toaster } from "@/components/ui/toast";
import { useAuthStore } from "@/features/auth/auth-store";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { applyTheme, useUiStore } from "@/lib/ui-store";
import { cn } from "@/lib/utils";

import { NAVIGATION } from "./navigation";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function AppShell() {
  const me = useAuthStore((s) => s.me);
  const logout = useAuthStore((s) => s.logout);
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const navigate = useNavigate();

  useEffect(() => applyTheme(theme), [theme]);

  if (!me) return null;
  const can = (permission?: string) =>
    !permission || me.is_superuser || me.permissions.includes(permission);

  return (
    <div className="flex h-screen overflow-hidden bg-bg">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-1.5 focus:text-primary-fg"
      >
        Skip to content
      </a>

      <aside
        className={cn(
          "flex shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150",
          collapsed ? "w-14" : "w-56",
        )}
      >
        <div className="flex h-12 items-center gap-2 border-b border-border px-3">
          <Button
            size="icon"
            variant="ghost"
            className="size-8 shrink-0"
            onClick={toggleSidebar}
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            aria-expanded={!collapsed}
          >
            {collapsed ? <Menu /> : <PanelLeftClose />}
          </Button>
          {!collapsed && (
            <span className="truncate text-sm font-semibold tracking-tight">
              {import.meta.env.VITE_APP_NAME ?? "KRB ERP"}
            </span>
          )}
        </div>
        <nav className="flex-1 overflow-y-auto py-2" aria-label="Main navigation">
          {NAVIGATION.map((section) => {
            const items = section.items.filter((item) => can(item.permission));
            if (items.length === 0) return null;
            return (
              <div key={section.label ?? "root"} className="mb-2">
                {section.label && !collapsed && (
                  <p className="px-4 pb-1 pt-2 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                    {section.label}
                  </p>
                )}
                <ul>
                  {items.map((item) => (
                    <li key={item.to}>
                      <NavLink
                        to={item.to}
                        end={item.to === "/"}
                        title={collapsed ? item.label : undefined}
                        className={({ isActive }) =>
                          cn(
                            "mx-2 flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-fg-muted hover:bg-bg hover:text-fg",
                            isActive &&
                              "bg-bg font-medium text-fg shadow-[inset_2px_0_0_var(--primary)]",
                            collapsed && "justify-center",
                          )
                        }
                      >
                        <item.icon className="size-4 shrink-0" aria-hidden />
                        {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-border bg-bg px-4">
          <p className="truncate text-sm text-fg-muted">{me.company.name}</p>
          <div className="flex items-center gap-1">
            {me.is_read_only && (
              <span className="mr-2 rounded-sm bg-info-bg px-2 py-0.5 text-xs font-medium text-info">
                Read-only access
              </span>
            )}
            <NotificationBell />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="gap-2 px-2" aria-label="Account menu">
                  <span className="flex size-6 items-center justify-center rounded-full bg-primary text-2xs font-semibold text-primary-fg">
                    {initials(me.user.full_name)}
                  </span>
                  <span className="hidden max-w-40 truncate text-sm sm:inline">
                    {me.user.full_name}
                  </span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="w-56">
                <DropdownMenuLabel>
                  <span className="block truncate normal-case tracking-normal text-fg">
                    {me.user.full_name}
                  </span>
                  <span className="block truncate font-normal normal-case tracking-normal">
                    {me.roles.map((r) => r.role_name).join(", ") || "No roles"}
                  </span>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => navigate("/account")}>
                  <UserRound /> My account
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => navigate("/change-password")}>
                  <KeyRound /> Change password
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Theme</DropdownMenuLabel>
                {(
                  [
                    ["system", "System", Monitor],
                    ["light", "Light", Sun],
                    ["dark", "Dark", Moon],
                  ] as const
                ).map(([value, label, Icon]) => (
                  <DropdownMenuCheckboxItem
                    key={value}
                    checked={theme === value}
                    onCheckedChange={() => setTheme(value)}
                  >
                    <Icon /> {label}
                  </DropdownMenuCheckboxItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => void logout()}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          {/* Route-level boundary: a crashed screen never takes down the shell. */}
          <ErrorBoundary label="This screen">
            <Suspense fallback={<PageSkeleton />}>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
      <Toaster />
    </div>
  );
}
