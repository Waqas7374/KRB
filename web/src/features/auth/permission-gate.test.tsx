import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { MeResponse } from "@/types/models";

import { useAuthStore } from "./auth-store";
import { PermissionGate, RequirePermission } from "./permission-gate";

function signInAs(me: Partial<MeResponse>): void {
  useAuthStore.setState({
    status: "authenticated",
    me: {
      permissions: [],
      is_superuser: false,
      is_read_only: false,
      roles: [],
      scopes: {},
      ...me,
    } as MeResponse,
  });
}

describe("PermissionGate", () => {
  beforeEach(() => useAuthStore.setState({ status: "anonymous", me: null }));

  it("renders children only when the permission is held", () => {
    signInAs({ permissions: ["vendors.view"] });
    render(
      <>
        <PermissionGate permission="vendors.view">can view</PermissionGate>
        <PermissionGate permission="vendors.create">can create</PermissionGate>
      </>,
    );
    expect(screen.getByText("can view")).toBeInTheDocument();
    expect(screen.queryByText("can create")).not.toBeInTheDocument();
  });

  it("lets a superuser through regardless of the list", () => {
    signInAs({ is_superuser: true });
    render(<PermissionGate permission="finance.gl.post">post</PermissionGate>);
    expect(screen.getByText("post")).toBeInTheDocument();
  });

  it("renders a forbidden panel for a page-level gate", () => {
    signInAs({ permissions: [] });
    render(<RequirePermission permission="users.view">users page</RequirePermission>);
    expect(screen.getByText("You do not have access to this")).toBeInTheDocument();
    expect(screen.queryByText("users page")).not.toBeInTheDocument();
  });

  it("renders nothing when signed out", () => {
    render(<PermissionGate permission="vendors.view">hidden</PermissionGate>);
    expect(screen.queryByText("hidden")).not.toBeInTheDocument();
  });
});
