import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LaunchChecklistPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  user: {
    role: "admin",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
    agency_scope: null as string[] | null,
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
      }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

const confirmedRelease = {
  id: "release-1",
  status: "confirmed",
  ready: true,
  content_digest: "a".repeat(64),
  page_version_id: "page-version-1",
  campaign_id: "campaign-1",
  code_batch_id: "batch-1",
  readiness_snapshot: {
    checks: [],
    passed_count: 4,
    total_count: 4,
    ready: true,
  },
  readiness_sample_code: {
    public_id: "LAUNCHCODE001",
    status: "activated",
    ready: true,
  },
};

describe("LaunchChecklistPage authority actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "admin";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.get.mockImplementation((path: string) => {
      if (path === "/launch-releases") {
        return Promise.resolve({
          data: { items: [confirmedRelease], total: 1 },
        });
      }
      return Promise.resolve({ data: { items: [] } });
    });
    mocks.post.mockResolvedValue({
      data: { ...confirmedRelease, status: "live" },
    });
    vi.stubGlobal("crypto", { randomUUID: () => "intent-idempotency-key" });
  });

  it("sends zero launch requests for a base agency principal", async () => {
    mocks.user.tenant_type = "agency";
    mocks.user.acting_tenant_id = null;

    render(<LaunchChecklistPage />);

    expect(await screen.findByText("当前账号没有上线发布权限")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("offers an explicit launch action for a confirmed release", async () => {
    render(<LaunchChecklistPage />);

    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    fireEvent.click(screen.getByRole("button", { name: /执行上线/ }));

    await vi.waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/launch-releases/release-1/launch",
        {
          idempotency_key: "intent-idempotency-key",
        }
      )
    );
    expect(
      screen.queryByRole("button", { name: /确认并上线/ })
    ).not.toBeInTheDocument();
  });

  it("presents the server-selected sample code as readiness without claiming a pre-live scan succeeded", async () => {
    render(<LaunchChecklistPage />);

    fireEvent.click(await screen.findByRole("button", { name: "查看" }));

    expect(screen.getByText("LAUNCHCODE001")).toBeVisible();
    expect(screen.getByText("上线样本码已准备")).toBeVisible();
    expect(screen.getByText(/正式上线前不会签发权益凭证/)).toBeVisible();
    expect(screen.queryByText(/扫码成功/)).not.toBeInTheDocument();
    expect(screen.queryByText("0/0 项通过")).not.toBeInTheDocument();
  });
});
