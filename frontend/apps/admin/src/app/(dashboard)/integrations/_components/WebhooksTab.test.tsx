import { App } from "antd";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WebhooksTab } from "./WebhooksTab";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    default: {
      get: mocks.get,
      post: mocks.post,
      patch: mocks.patch,
      delete: mocks.delete,
    },
  };
});

const listedEndpoint = {
  id: "0198dca0-1234-7abc-8def-0123456789ab",
  url: "https://callback.example/hook",
  events: ["scan.created"],
  description: "主回调",
  enabled: true,
  config_version: 7,
  batch_mode: false,
  batch_size: null,
};

function renderTab() {
  return render(
    <App>
      <WebhooksTab />
    </App>
  );
}

describe("WebhooksTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: [listedEndpoint] });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("sends the row config_version as If-Match when toggling an endpoint", async () => {
    mocks.patch.mockResolvedValue({
      data: { ...listedEndpoint, enabled: false },
    });

    renderTab();
    fireEvent.click(await screen.findByRole("switch"));

    await waitFor(() =>
      expect(mocks.patch).toHaveBeenCalledWith(
        `/webhooks/endpoints/${listedEndpoint.id}`,
        { enabled: false },
        { headers: { "If-Match": "7" } }
      )
    );
    expect(await screen.findByText("已禁用")).toBeInTheDocument();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
  });

  it("sends the row config_version as If-Match when deleting an endpoint", async () => {
    mocks.delete.mockResolvedValue({ data: { deleted: true } });

    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /删\s*除/ }));

    fireEvent.click(await screen.findByRole("button", { name: /确\s*认/ }));

    await waitFor(() =>
      expect(mocks.delete).toHaveBeenCalledWith(
        `/webhooks/endpoints/${listedEndpoint.id}`,
        { headers: { "If-Match": "7" } }
      )
    );
    expect(await screen.findByText("已删除")).toBeInTheDocument();
  });

  it("warns and refetches when a toggle hits a config version conflict", async () => {
    mocks.patch.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "config version conflict" } },
    });

    renderTab();
    fireEvent.click(await screen.findByRole("switch"));

    expect(
      await screen.findByText("配置已被他人更新，已刷新请重试")
    ).toBeInTheDocument();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
  });

  it("warns and refetches when a delete hits a config version conflict", async () => {
    mocks.delete.mockRejectedValueOnce({
      response: { status: 409, data: { detail: "config version conflict" } },
    });

    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /删\s*除/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确\s*认/ }));

    expect(
      await screen.findByText("配置已被他人更新，已刷新请重试")
    ).toBeInTheDocument();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
  });

  it("never renders [object Object] when a mutation fails with an object detail", async () => {
    mocks.patch.mockRejectedValueOnce({
      response: { status: 500, data: { detail: { reason: "boom" } } },
    });

    renderTab();
    fireEvent.click(await screen.findByRole("switch"));

    expect(await screen.findByText("操作失败")).toBeInTheDocument();
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it("keeps batch_size out of the create form until batch mode is enabled", async () => {
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /新建端点/ }));

    const modal = await screen.findByRole("dialog");
    expect(within(modal).getByRole("switch")).toBeInTheDocument();
    // jsdom 不驱动 antd 弹层动画（opacity 停留 0），这里用表单行的计算 display 断言显隐
    const batchSizeRowDisplay = () =>
      getComputedStyle(
        screen.getByLabelText("批量大小").closest(".ant-form-item")!
      ).display;
    expect(batchSizeRowDisplay()).toBe("none");

    fireEvent.click(within(modal).getByRole("switch"));

    await waitFor(() => expect(batchSizeRowDisplay()).not.toBe("none"));
  });

  it("falls back to a manual-copy hint when the browser blocks clipboard writes", async () => {
    mocks.post.mockResolvedValue({
      data: { ...listedEndpoint, secret: "whsec_once_only" },
    });
    (
      navigator.clipboard.writeText as ReturnType<typeof vi.fn>
    ).mockRejectedValue(new Error("denied"));

    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: /新建端点/ }));
    const modal = await screen.findByRole("dialog");
    fireEvent.change(within(modal).getByLabelText("回调 URL"), {
      target: { value: "https://callback.example/new" },
    });
    fireEvent.mouseDown(within(modal).getByLabelText("订阅事件"));
    fireEvent.click(await screen.findByText("扫码事件"));
    fireEvent.click(within(modal).getByRole("button", { name: /确\s*定/ }));

    fireEvent.click(await screen.findByRole("button", { name: /复\s*制/ }));

    expect(
      await screen.findByText("浏览器未允许复制，请手动选择密钥复制")
    ).toBeInTheDocument();
  });
});
