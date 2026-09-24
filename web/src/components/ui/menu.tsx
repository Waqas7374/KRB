import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;

export function DropdownMenuContent({
  children,
  align = "end",
  className,
}: {
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
}) {
  return (
    <Menu.Portal>
      <Menu.Content
        align={align}
        sideOffset={4}
        className={cn(
          "z-50 min-w-44 rounded-md border border-border bg-surface-raised p-1 text-sm text-fg shadow-[var(--shadow-float)]",
          className,
        )}
      >
        {children}
      </Menu.Content>
    </Menu.Portal>
  );
}

const itemClass =
  "flex cursor-default select-none items-center gap-2 rounded-sm px-2 py-1.5 outline-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface [&_svg]:size-3.5";

export function DropdownMenuItem({
  children,
  onSelect,
  disabled,
  destructive,
}: {
  children: ReactNode;
  onSelect?: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <Menu.Item
      className={cn(itemClass, destructive && "text-danger")}
      onSelect={onSelect}
      disabled={disabled}
    >
      {children}
    </Menu.Item>
  );
}

export function DropdownMenuCheckboxItem({
  children,
  checked,
  onCheckedChange,
}: {
  children: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <Menu.CheckboxItem
      className={cn(itemClass, "pl-7 relative")}
      checked={checked}
      onCheckedChange={onCheckedChange}
      // Keep the menu open so several columns can be toggled in one go.
      onSelect={(event) => event.preventDefault()}
    >
      <Menu.ItemIndicator className="absolute left-2">
        <Check />
      </Menu.ItemIndicator>
      {children}
    </Menu.CheckboxItem>
  );
}

export function DropdownMenuLabel({ children }: { children: ReactNode }) {
  return (
    <Menu.Label className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">
      {children}
    </Menu.Label>
  );
}

export function DropdownMenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-border" />;
}
