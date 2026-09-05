import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PrivacyGovernancePage from "./page";

const { mockGet, mockPost, mockPatch, mockSuccess, mockMessage } = vi.hoisted(
  () => ({
    mockGet: vi.fn(),
    mockPost: vi.fn(),
    mockPatch: vi.fn(),
    mockSuccess: vi.fn(),
    mockMessage: { error: vi.fn(), success: vi.fn() },
  })
);

vi.mock("@/lib/api", () => ({
  default: { get: mockGet, post: mockPost, patch: mockPatch },
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (
    selector: (state: { user: { account_id: string } }) => unknown
  ) => selector({ user: { account_id: "requester-account" } }),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  mockMessage.success = mockSuccess;
  return { ...actual, App: { useApp: () => ({ message: mockMessage }) } };
});

describe("PrivacyGovernancePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith("rights-requests")
          ? {
              data: {
                items: [
                  {
                    id: "rights-1",
                    request_number: "PIR-20260820-A1B2C3D4",
                    request_type: "delete",
                    status: "submitted",
                    due_at: "2026-09-10T00:00:00Z",
                    overdue: false,
                  },
                ],
              },
            }
          : {
              data: {
                items: [
                  {
                    id: "export-1",
                    requester_account_id: "another-account",
                    status: "pending_approval",
                    reason: "监管材料",
                    recipient_purpose: "合规检查",
                    requested_fields: ["membership_number", "phone"],
                  },
                ],
              },
            }
      )
    );
    mockPost.mockResolvedValue({ data: { status: "approved" } });
  });

  it("exposes a rights queue and enforces independent sensitive-export approval in the UI", async () => {
    render(<PrivacyGovernancePage />);

    expect(
      await screen.findByText("PIR-20260820-A1B2C3D4")
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "敏感导出" }));
    fireEvent.click(await screen.findByRole("button", { name: "独立审批" }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/privacy/sensitive-exports/export-1/approve",
        {
          reason: "独立复核处理目的、范围和字段后批准",
        }
      )
    );
    expect(screen.queryByText("积分")).not.toBeInTheDocument();
  }, 30_000);
});
