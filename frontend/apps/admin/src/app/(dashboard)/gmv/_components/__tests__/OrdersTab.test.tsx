import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrdersTab } from "../OrdersTab";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  useCrud: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  mutate: vi.fn(),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: {
      success: (...args: unknown[]) => mocks.success(...args),
      warning: (...args: unknown[]) => mocks.warning(...args),
      error: (...args: unknown[]) => mocks.error(...args),
    },
  };
});

// 保留真实 extractErrorMessage，仅替换 axios 实例
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    default: {
      get: (...args: unknown[]) => mocks.get(...args),
      post: (...args: unknown[]) => mocks.post(...args),
    },
  };
});

vi.mock("@/lib/hooks", () => ({
  useCrud: (...args: unknown[]) => mocks.useCrud(...args),
}));

const ORDERS_JSON = JSON.stringify([
  {
    external_id: "ORD001",
    amount: 99.9,
    order_time: "2026-09-01T10:00:00+08:00",
    phone: "13800138000",
    channel: "taobao",
  },
]);

async function openModalAndSubmit() {
  fireEvent.click(screen.getByRole("button", { name: /导入订单/ }));
  const textarea = await screen.findByRole("textbox");
  fireEvent.change(textarea, { target: { value: ORDERS_JSON } });
  const footer = document.querySelector(".ant-modal-footer") as HTMLElement;
  fireEvent.click(within(footer).getByRole("button", { name: /OK|确/i }));
}

describe("OrdersTab 订单导入", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", {
      randomUUID: () => "019e887f-0000-7000-a000-00000000abcd",
    });
    mocks.useCrud.mockReturnValue({
      items: [],
      total: 0,
      page: 1,
      loading: false,
      setPage: vi.fn(),
      mutate: mocks.mutate,
      setFilter: vi.fn(),
    });
  });

  it("导入请求携带 Idempotency-Key 与顶层 source_system=manual_upload", async () => {
    mocks.post.mockResolvedValue({
      data: { created: 1, replayed: 0, failed: 0, errors: [], items: [] },
    });

    render(<OrdersTab />);
    await openModalAndSubmit();

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [url, body, config] = mocks.post.mock.calls[0];
    expect(url).toBe("/gmv/orders/import");
    expect(body).toEqual({
      source_system: "manual_upload",
      orders: [
        {
          external_id: "ORD001",
          amount: 99.9,
          order_time: "2026-09-01T10:00:00+08:00",
          phone: "13800138000",
          channel: "taobao",
        },
      ],
    });
    expect(config).toEqual({
      headers: { "Idempotency-Key": "019e887f-0000-7000-a000-00000000abcd" },
    });
    expect(mocks.success).toHaveBeenCalledWith("导入成功");
  });

  it("部分失败（HTTP 200 且 failed>0）提示成功/失败条数与失败示例", async () => {
    mocks.post.mockResolvedValue({
      data: {
        created: 1,
        replayed: 0,
        failed: 2,
        errors: [
          {
            row: 1,
            external_id: "ORD002",
            status_code: 409,
            reason: "重复订单",
            retry_after: null,
          },
          {
            row: 2,
            external_id: "ORD003",
            status_code: 422,
            reason: "字段缺失",
            retry_after: null,
          },
        ],
        items: [],
      },
    });

    render(<OrdersTab />);
    await openModalAndSubmit();

    await waitFor(() => expect(mocks.warning).toHaveBeenCalledTimes(1));
    const warningText = mocks.warning.mock.calls[0][0] as string;
    expect(warningText).toContain("导入完成：成功 1 条，失败 2 条");
    expect(warningText).toContain("ORD002: 重复订单");
    // 列表仍应刷新
    expect(mocks.mutate).toHaveBeenCalled();
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("请求级失败走 extractErrorMessage，而不是直接渲染 pydantic detail 数组", async () => {
    mocks.post.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 422,
        data: {
          detail: [
            { msg: "Input should be a valid datetime", type: "value_error" },
          ],
        },
      },
    });

    render(<OrdersTab />);
    await openModalAndSubmit();

    await waitFor(() => expect(mocks.error).toHaveBeenCalledTimes(1));
    expect(mocks.error).toHaveBeenCalledWith(
      "提交的信息格式有误，请检查各填写项后重试"
    );
  });
});
