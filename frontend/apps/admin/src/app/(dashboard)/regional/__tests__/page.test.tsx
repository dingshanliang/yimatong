import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RegionalPage from "../page";

const { mockMessage } = vi.hoisted(() => ({ mockMessage: { success: vi.fn(), error: vi.fn() } }));
const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: mockMessage,
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

describe("RegionalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/regional/orgs") {
        return Promise.resolve({
          data: [{
            id: "regional-1",
            name: "赣南脐橙协会",
            org_type: "association",
            org_type_label: "协会组织",
            member_count: 2,
            active_member_count: 1,
            template_count: 1,
            next_action: "配置统一活动",
          }],
        });
      }
      if (String(url).startsWith("/regional/orgs/regional-1/members")) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/tenants") {
        return Promise.resolve({
          data: { items: [{ id: "tenant-1", name: "成员企业A" }] },
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("shows regional organization operating summary before drilling into tabs", async () => {
    render(<RegionalPage />);

    await waitFor(() => {
      expect(screen.getByTestId("regional-org-summary-regional-1")).toHaveTextContent("1/2");
      expect(screen.getByTestId("regional-org-next-action-regional-1")).toHaveTextContent("配置统一活动");
    });
  });

  it("adds a member by selecting a client instead of typing a tenant id", async () => {
    mockPost.mockResolvedValue({ data: { id: "member-1" } });
    render(<RegionalPage />);

    await screen.findByTestId("regional-org-summary-regional-1");
    fireEvent.click(screen.getByText("赣南脐橙协会"));
    fireEvent.click(await screen.findByRole("button", { name: /添加成员/ }));

    await waitFor(() => {
      expect(screen.getByTestId("member-client-select")).toBeInTheDocument();
      expect(screen.queryByLabelText("成员租户 ID")).not.toBeInTheDocument();
    });
  });
});
