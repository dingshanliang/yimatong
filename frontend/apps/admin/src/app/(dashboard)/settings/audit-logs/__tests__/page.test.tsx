import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuditLogsPage from "../page";

const mockSetFilter = vi.fn();
const mockSetPage = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useCrud: (path: string) => {
    expect(path).toBe("/audit-logs");
    return {
      items: [
        {
          id: "log-1",
          tenant_id: "tenant-1",
          timestamp: "2026-07-30T08:00:00Z",
          operator: {
            id: "account-1",
            name: "品牌管理员",
            email: "admin@demo.com",
          },
          action: "account_disabled",
          resource: "account:account-2",
          result: "success",
          details: {
            resource_name: "门店账号",
            reason: "员工离职",
            before: "enabled",
            after: "disabled",
          },
        },
      ],
      total: 1,
      page: 1,
      pageSize: 20,
      loading: false,
      setPage: mockSetPage,
      setFilter: mockSetFilter,
    };
  },
}));

describe("AuditLogsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows readable tenant audit fields and dangerous-operation details", async () => {
    render(<AuditLogsPage />);

    expect(screen.getByText("品牌管理员")).toBeInTheDocument();
    expect(screen.getByText("admin@demo.com")).toBeInTheDocument();
    expect(screen.getByText("停用账户")).toBeInTheDocument();
    expect(screen.getByText("门店账号")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();
    expect(screen.getByText("员工离职")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText(/"before": "enabled"/)).toBeInTheDocument();
    expect(within(drawer).getByText(/"after": "disabled"/)).toBeInTheDocument();
  });

  it("combines action and keyword filters instead of dropping the previous filter", async () => {
    render(<AuditLogsPage />);

    fireEvent.mouseDown(screen.getByLabelText("业务动作"));
    const actionOptions = await screen.findAllByText("停用账户");
    fireEvent.click(actionOptions.at(-1)!);

    fireEvent.change(screen.getByLabelText("搜索操作日志"), {
      target: { value: "门店" },
    });
    fireEvent.keyDown(screen.getByLabelText("搜索操作日志"), {
      key: "Enter",
      code: "Enter",
    });

    await waitFor(() => {
      expect(mockSetFilter).toHaveBeenLastCalledWith({
        action: "account_disabled",
        keyword: "门店",
      });
    });
  });
});
