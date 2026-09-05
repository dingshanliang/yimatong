import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CrmSyncPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
  },
}));

vi.mock("./_components/MappingsTab", () => ({
  MappingsTab: () => <div data-testid="mappings-tab" />,
}));

vi.mock("./_components/LogsTab", () => ({
  LogsTab: () => <div data-testid="logs-tab" />,
}));

function routeResponse(url: string) {
  if (url === "/connectors/connectors") {
    return { data: [] };
  }
  // 后端不存在任何 /crm/* 路由
  throw Object.assign(new Error("Request failed"), {
    response: { status: 404, data: { detail: "Not Found" } },
  });
}

describe("CrmSyncPage honesty contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation((url: string) => routeResponse(url));
  });

  it("states plainly that CRM sync is not integrated yet", async () => {
    render(<CrmSyncPage />);

    expect(await screen.findByText("CRM 同步功能尚未接入")).toBeInTheDocument();
    expect(screen.getByText(/后端 CRM 同步服务尚未上线/)).toBeInTheDocument();
  });

  it("keeps the manual sync button disabled instead of faking a cron trigger", async () => {
    render(<CrmSyncPage />);

    const button = await screen.findByRole("button", {
      name: /手动触发同步/,
    });
    expect(button).toBeDisabled();
    // 历史假提示不能再出现
    expect(screen.queryByText(/cron 周期/)).not.toBeInTheDocument();
  });

  it("surfaces the unavailable notice instead of silently emptying the data tabs", async () => {
    render(<CrmSyncPage />);

    expect(await screen.findByText("同步映射暂不可用")).toBeInTheDocument();
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    expect(mocks.get).toHaveBeenCalledWith("/crm/sync-mappings");

    fireEvent.click(screen.getByRole("tab", { name: "同步日志" }));
    expect(await screen.findByText("同步日志暂不可用")).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith("/crm/sync-logs");
  });

  it("marks the mapping count as unavailable rather than showing a fake zero", async () => {
    render(<CrmSyncPage />);

    expect(await screen.findByText("不可用")).toBeInTheDocument();
    expect(screen.getByText("CRM 连接状态")).toBeInTheDocument();
  });
});
