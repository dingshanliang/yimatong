import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RiskAccess } from "@/lib/risk-access";
import { RulesTab } from "./RulesTab";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  mutate: vi.fn(),
  useCrud: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: {
          success: mocks.success,
          error: mocks.error,
          info: mocks.info,
        },
      }),
    },
  };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    default: {
      post: (...args: unknown[]) => mocks.post(...args),
      patch: (...args: unknown[]) => mocks.patch(...args),
      delete: (...args: unknown[]) => mocks.delete(...args),
    },
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
        id: "rule-1",
        name: "IP 高频拦截",
        rule_type: "ip_frequency",
        action: "block",
        enabled: true,
        version: 4,
        config: { window_minutes: 30, max_requests: 8 },
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    setPage: vi.fn(),
    mutate: mocks.mutate,
  };
}

describe("RulesTab edit, delete and config templates", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.patch.mockReset();
    mocks.delete.mockReset();
    mocks.mutate.mockReset().mockResolvedValue(undefined);
    mocks.success.mockClear();
    mocks.error.mockClear();
    mocks.info.mockClear();
    mocks.useCrud.mockReturnValue(crudState());
  });

  it("fills the type-specific config template when a rule type is picked", async () => {
    render(<RulesTab access={MANAGE_ACCESS} />);

    fireEvent.click(await screen.findByRole("button", { name: /新建规则/ }));
    fireEvent.mouseDown(screen.getByLabelText("规则类型"));
    fireEvent.click(await screen.findByText("手机号频率限制"));

    await waitFor(() => {
      const textarea = screen.getByLabelText(
        "规则配置 (JSON)"
      ) as HTMLTextAreaElement;
      expect(JSON.parse(textarea.value)).toEqual({
        window_minutes: 60,
        max_requests: 3,
      });
    });
  });

  it("submits edits as a PATCH carrying expected_version", async () => {
    mocks.patch.mockResolvedValue({ data: {} });
    render(<RulesTab access={MANAGE_ACCESS} />);

    fireEvent.click(await screen.findByRole("button", { name: /编辑/ }));

    const nameInput = screen.getByLabelText("规则名称") as HTMLInputElement;
    expect(nameInput.value).toBe("IP 高频拦截");
    fireEvent.change(nameInput, { target: { value: "IP 高频拦截 v2" } });

    fireEvent.click(screen.getByRole("button", { name: /^OK$|^确\s*定$/ }));

    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));
    expect(mocks.patch).toHaveBeenCalledWith(
      "/risk-rules/rule-1",
      {
        name: "IP 高频拦截 v2",
        action: "block",
        config: { window_minutes: 30, max_requests: 8 },
        expected_version: 4,
      },
      expect.objectContaining({ headers: expect.anything() })
    );
    // 编辑态下规则类型不可改
    expect(screen.getByLabelText("规则类型")).toHaveAttribute("disabled");
    expect(mocks.mutate).toHaveBeenCalled();
  });

  it("soft-deletes with expected_version and explains the retention honestly", async () => {
    mocks.delete.mockResolvedValue({
      data: { deleted: false, disabled: true },
    });
    render(<RulesTab access={MANAGE_ACCESS} />);

    fireEvent.click(await screen.findByRole("button", { name: /删除/ }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(mocks.delete).toHaveBeenCalledTimes(1));
    expect(mocks.delete).toHaveBeenCalledWith(
      "/risk-rules/rule-1?expected_version=4",
      expect.objectContaining({ headers: expect.anything() })
    );
    await waitFor(() =>
      expect(mocks.info).toHaveBeenCalledWith(
        expect.stringContaining("保留历史记录")
      )
    );
    expect(mocks.mutate).toHaveBeenCalled();
  });

  it("hides edit and delete actions without manage access", () => {
    mocks.useCrud.mockReturnValue({
      ...crudState(),
    });
    render(
      <RulesTab
        access={{ canRead: true, canManage: false, canEvaluate: false }}
      />
    );

    expect(screen.queryByRole("button", { name: /编辑/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /删除/ })).toBeNull();
  });
});
