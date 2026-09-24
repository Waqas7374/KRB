import { Link } from "react-router-dom";

import { NAVIGATION } from "@/app/navigation";
import { PageBody, PageHeader, Section } from "@/components/layout/page";
import { Tag } from "@/components/ui/status-badge";
import { useAuthStore } from "@/features/auth/auth-store";
import { humanize } from "@/lib/utils";

/**
 * Landing page until the role dashboards of Phase 6 exist. It answers the two
 * questions a new user has: "what can I do here?" and "why can't I see X?".
 */
export function HomePage() {
  const me = useAuthStore((s) => s.me);
  if (!me) return null;
  const can = (p?: string) => !p || me.is_superuser || me.permissions.includes(p);
  const sections = NAVIGATION.filter((s) => s.label)
    .map((s) => ({ ...s, items: s.items.filter((i) => can(i.permission)) }))
    .filter((s) => s.items.length > 0);

  return (
    <>
      <PageHeader
        title={`Welcome, ${me.user.full_name.split(" ")[0]}`}
        subtitle={me.company.name}
      />
      <PageBody className="max-w-5xl">
        <Section title="Your access">
          {me.roles.length === 0 ? (
            <p className="px-4 py-4 text-sm text-fg-muted">
              You have no roles yet, so there is nothing to show. Ask an administrator to grant you
              one.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {me.roles.map((r) => (
                <li
                  key={`${r.role_code}-${r.scope_id ?? r.scope_type}`}
                  className="flex items-center gap-3 px-4 py-2 text-sm"
                >
                  <span className="font-medium">{r.role_name}</span>
                  <Tag>{humanize(r.scope_type)}</Tag>
                  {r.scope_label && <span className="text-fg-muted">{r.scope_label}</span>}
                </li>
              ))}
            </ul>
          )}
          {me.is_read_only && (
            <p className="border-t border-border px-4 py-2 text-sm text-info">
              Your access is read-only: you can view and export, but not change anything.
            </p>
          )}
        </Section>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {sections.map((section) => (
            <Section key={section.label} title={section.label ?? ""}>
              <ul className="p-1">
                {section.items.map((item) => (
                  <li key={item.to}>
                    <Link
                      to={item.to}
                      className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm hover:bg-row-hover"
                    >
                      <item.icon className="size-4 text-fg-muted" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          ))}
        </div>
      </PageBody>
    </>
  );
}
