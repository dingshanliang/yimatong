import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgencyAuthorizationsPage from "../page";

const mockGet = vi.fn();
const mockError = vi.fn();
let mockUser = { role: "admin", tenant_type: "brand" };

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: vi.fn(), error: mockError },
      }),
    },
  };
});

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mockUser }) => unknown) =>
    selector({ user: mockUser }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    delete: vi.fn(),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

describe("AgencyAuthorizationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { role: "admin", tenant_type: "brand" };
    mockGet.mockResolvedValue({ data: { items: [] } });
  });

  it("loads the authorization directory for brand administrators", async () => {
    render(<AgencyAuthorizationsPage />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/authorizations");
    });
    expect(
      screen.getByRole("button", { name: "新增授权" })
    ).toBeInTheDocument();
  });

  it.each([
    { role: "operator", tenant_type: "brand" },
    { role: "viewer", tenant_type: "brand" },
    { role: "admin", tenant_type: "agency" },
  ])(
    "does not request data for an unauthorized principal: %o",
    async (user) => {
      mockUser = user;

      render(<AgencyAuthorizationsPage />);

      expect(
        screen.getByText("当前账户不能管理代运营授权")
      ).toBeInTheDocument();
      await waitFor(() => expect(mockGet).not.toHaveBeenCalled());
      expect(
        screen.queryByRole("button", { name: "新增授权" })
      ).not.toBeInTheDocument();
    }
  );
});
