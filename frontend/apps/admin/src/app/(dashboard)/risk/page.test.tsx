import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  user: { tenant_type: "brand", role: "viewer", acting_tenant_id: null } as {
    tenant_type: string;
    role: string;
    acting_tenant_id: string | null;
  },
  rules: vi.fn(() => <div>rules mounted</div>),
  interceptions: vi.fn(() => <div>interceptions mounted</div>),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));
vi.mock("./_components/RulesTab", () => ({ RulesTab: mocks.rules }));
vi.mock("./_components/InterceptionsTab", () => ({
  InterceptionsTab: mocks.interceptions,
}));
vi.mock("./_components/AlertsTab", () => ({ AlertsTab: () => null }));
vi.mock("./_components/NotificationsTab", () => ({
  NotificationsTab: () => null,
}));
vi.mock("./_components/PausesTab", () => ({ PausesTab: () => null }));

import RiskPage from "./page";

describe("RiskPage access boundary", () => {
  beforeEach(() => {
    mocks.rules.mockClear();
    mocks.interceptions.mockClear();
  });

  it("does not mount data-fetching workspaces for a viewer", () => {
    mocks.user.role = "viewer";
    render(<RiskPage />);
    expect(screen.getByText("当前账号无权访问风控中心")).toBeInTheDocument();
    expect(mocks.rules).not.toHaveBeenCalled();
    expect(mocks.interceptions).not.toHaveBeenCalled();
  });

  it("mounts the workspace for a direct brand operator", () => {
    mocks.user.role = "operator";
    render(<RiskPage />);
    expect(mocks.rules).toHaveBeenCalled();
  });
});
