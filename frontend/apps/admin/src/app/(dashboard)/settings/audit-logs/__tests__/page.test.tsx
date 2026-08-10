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
let mockLogs: Array<Record<string, unknown>> = [];

vi.mock("@/lib/hooks", () => ({
  useCrud: (path: string) => {
    expect(path).toBe("/audit-logs");
    return {
      items: mockLogs,
      total: mockLogs.length,
      page: 1,
      pageSize: 20,
      loading: false,
      setPage: mockSetPage,
      setFilter: mockSetFilter,
    };
  },
}));

const disabledAccountLog = {
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
    before: {
      name: "门店账号",
      status: "enabled",
      organization: { id: "org-1", name: "直营网点" },
      roles: [{ id: "role-operator", name: "operator" }],
    },
    after: {
      name: "门店账号",
      status: "disabled",
      organization: { id: "org-1", name: "直营网点" },
      roles: [{ id: "role-operator", name: "operator" }],
    },
  },
};

describe("AuditLogsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLogs = [disabledAccountLog];
  });

  it("uses the backend account-name snapshot for status events", () => {
    mockLogs = [
      {
        ...disabledAccountLog,
        id: "log-2",
        action: "account_enabled",
        resource: "account:opaque-account-id",
        details: {
          resource_name: "华东渠道负责人",
          before: { status: "disabled" },
          after: { status: "enabled" },
        },
      },
    ];

    render(<AuditLogsPage />);

    expect(screen.getByText("华东渠道负责人")).toBeInTheDocument();
    expect(screen.queryByText("opaque-account-id")).not.toBeInTheDocument();
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
    expect(within(drawer).getByText(/"status": "enabled"/)).toBeInTheDocument();
    expect(
      within(drawer).getByText(/"status": "disabled"/)
    ).toBeInTheDocument();
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
