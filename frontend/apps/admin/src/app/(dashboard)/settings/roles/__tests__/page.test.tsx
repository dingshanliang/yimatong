import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RolesPage from "../page";

const mockMutate = vi.fn();
const mockUseSWR = vi.fn();
let mockRole = "admin";

vi.mock("swr", () => ({
  default: (key: string | null) => mockUseSWR(key),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: { role: string } }) => unknown) =>
    selector({ user: { role: mockRole } }),
}));

vi.mock("@/lib/api", () => ({
  extractErrorMessage: (error: unknown, fallback: string) =>
    (error as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail || fallback,
}));

describe("RolesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRole = "admin";
    mockUseSWR.mockReturnValue({
      data: [
        {
          id: "role-admin",
          name: "admin",
          description: "管理租户",
          permissions: [],
        },
      ],
      error: undefined,
      isLoading: false,
      mutate: mockMutate,
    });
  });

  it("does not request the role directory for viewers", () => {
    mockRole = "viewer";

    render(<RolesPage />);

    expect(mockUseSWR).toHaveBeenCalledWith(null);
    expect(screen.getByText("当前角色不能查看角色目录")).toBeInTheDocument();
    expect(
      screen.queryByText("当前使用经过验证的内置角色")
    ).not.toBeInTheDocument();
  });

  it("shows a 403 boundary as an error and retries without a false empty state", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: {
        response: { status: 403, data: { detail: "无权查看角色目录" } },
      },
      isLoading: false,
      mutate: mockMutate,
    });

    render(<RolesPage />);

    expect(screen.getByText("角色目录加载失败")).toBeInTheDocument();
    expect(screen.getByText("无权查看角色目录")).toBeInTheDocument();
    expect(
      screen.queryByText("当前没有可分配的内置角色")
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(mockMutate).toHaveBeenCalledTimes(1);
  });

  it("distinguishes a successful empty role directory from loading failure", () => {
    mockUseSWR.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
      mutate: mockMutate,
    });

    render(<RolesPage />);

    expect(screen.getByText("当前没有可分配的内置角色")).toBeInTheDocument();
    expect(screen.queryByText("角色目录加载失败")).not.toBeInTheDocument();
  });
});
