import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RiskAccess } from "@/lib/risk-access";
import { PausesTab } from "./PausesTab";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  mutate: vi.fn(),
  useCrud: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: mocks.success, error: mocks.error },
      }),
    },
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    default: { post: (...args: unknown[]) => mocks.post(...args) },
  };
});

vi.mock("@/lib/hooks", () => ({
  useCrud: (...args: unknown[]) => mocks.useCrud(...args),
}));

const MANAGE_ACCESS: RiskAccess = {
  canRead: true,
  canManage: true,
  canEvaluate: true,
};

function crudState() {
  return {
    items: [
      {
        id: "pause-1",
        campaign_id: "campaign-1",
        risk_rule_id: "rule-1",
        prior_status: "active",
        status: "active",
        version: 3,
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    setPage: vi.fn(),
    mutate: mocks.mutate,
  };
}

async function submitResume(reason: string) {
  fireEvent.click(await screen.findByRole("button", { name: "恢复活动" }));
  fireEvent.change(screen.getByLabelText("恢复原因"), {
    target: { value: reason },
  });
  fireEvent.click(screen.getByRole("button", { name: "OK" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
}

describe("PausesTab resume outcome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useCrud.mockReturnValue(crudState());
    mocks.mutate.mockResolvedValue(undefined);
  });

  it("reports when the campaign is restored to its prior state", async () => {
    mocks.post.mockResolvedValue({ data: { campaign_status: "active" } });
    render(<PausesTab access={MANAGE_ACCESS} />);

    await submitResume("风险复核通过");

    expect(mocks.post).toHaveBeenCalledWith(
      "/risk-rules/pauses/pause-1/resume",
      { expected_version: 3, reason: "风险复核通过" },
      expect.objectContaining({
        headers: { "Idempotency-Key": expect.any(String) },
      })
    );
    expect(mocks.success).toHaveBeenCalledWith(
      "风控暂停已解除，活动已恢复为进行中"
    );
  });

  it("reports a later operator status without claiming it was overwritten", async () => {
    mocks.post.mockResolvedValue({ data: { campaign_status: "ended" } });
    render(<PausesTab access={MANAGE_ACCESS} />);

    await submitResume("仅解除风控暂停");

    expect(mocks.success).toHaveBeenCalledWith(
      "风控暂停已解除；活动保留后续人工设置的已结束状态"
    );
  });

  it("does not expose or invoke resume without manage access", async () => {
    render(
      <PausesTab
        access={{ canRead: true, canManage: false, canEvaluate: false }}
      />
    );

    expect(await screen.findByText("campaign-1")).toBeVisible();
    expect(screen.getByText("进行中")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "恢复活动" })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
