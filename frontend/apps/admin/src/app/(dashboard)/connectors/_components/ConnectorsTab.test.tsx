import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectorsTab, callbackUrlFor } from "./ConnectorsTab";
import type { Connector } from "./types";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  message: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: { ...actual.App, useApp: () => ({ message: mocks.message }) },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    post: (...args: unknown[]) => mocks.post(...args),
    patch: (...args: unknown[]) => mocks.patch(...args),
  },
  API_BASE_URL: "http://backend.test",
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

const youzanRow: Connector = {
  id: "conn-youzan-1",
  name: "有赞券",
  connector_type: "youzan",
  config: { client_id: "existing-client-id", shop_alias: "shop-a" },
  enabled: true,
  secrets: {},
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function renderTab() {
  render(
    <ConnectorsTab
      connectors={[youzanRow]}
      loading={false}
      connectorTypes={["youzan", "coupon_pool"]}
      page={1}
      pageSize={20}
      total={1}
      onPageChange={() => {}}
      onRefresh={() => {}}
    />
  );
}

async function pickYouzanType() {
  // 表格类型 Tag 与下拉选项都叫「有赞」，只点下拉选项
  await userEvent.click(screen.getByLabelText("连接器类型"));
  const option = await waitFor(() => {
    const match = screen
      .getAllByText("有赞")
      .find((el) => el.closest(".ant-select-item-option"));
    if (!match) {
      throw new Error("youzan option not rendered yet");
    }
    return match;
  });
  await userEvent.click(option);
}

async function fillYouzanCredentials() {
  await userEvent.type(
    screen.getByLabelText("有赞应用 client_id"),
    "client-id-1"
  );
  await userEvent.type(
    screen.getByLabelText("有赞应用 client_secret"),
    "super-secret"
  );
}

async function clickSave() {
  await userEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
}

async function findCallbackCode(fragment: string) {
  return waitFor(
    () => {
      const code = screen
        .getAllByText((_, el) => el?.tagName === "CODE")
        .find((el) => (el.textContent || "").includes(fragment));
      if (!code) {
        throw new Error(`callback url ${fragment} not rendered yet`);
      }
      return code;
    },
    { timeout: 3000 }
  );
}

describe("ConnectorsTab youzan config form", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("creates a youzan connector with secrets separated from public config", async () => {
    renderTab();
    await userEvent.click(screen.getByRole("button", { name: /新建连接器/ }));
    await userEvent.type(screen.getByLabelText("连接器名称"), "有赞优惠券");
    await pickYouzanType();
    await waitFor(() =>
      expect(screen.getByLabelText("有赞应用 client_id")).toBeInTheDocument()
    );
    await fillYouzanCredentials();
    mocks.post.mockResolvedValue({ data: { id: "conn-new", ...youzanRow } });

    await clickSave();

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [, payload] = mocks.post.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(payload.connector_type).toBe("youzan");
    expect(payload.config).toEqual({
      client_id: "client-id-1",
      shop_alias: "",
    });
    expect(payload.secrets).toEqual({ client_secret: "super-secret" });
    expect(JSON.stringify(payload.config)).not.toContain("super-secret");
  });

  it("shows the callback URL dialog after creating a youzan connector", async () => {
    renderTab();
    await userEvent.click(screen.getByRole("button", { name: /新建连接器/ }));
    await userEvent.type(screen.getByLabelText("连接器名称"), "有赞优惠券");
    await pickYouzanType();
    await waitFor(() =>
      expect(screen.getByLabelText("有赞应用 client_id")).toBeInTheDocument()
    );
    await fillYouzanCredentials();
    mocks.post.mockResolvedValue({ data: { ...youzanRow, id: "conn-new" } });

    await clickSave();

    const code = await findCallbackCode("connectors/conn-new/callback");
    expect(code.textContent).toBe(callbackUrlFor("conn-new"));
  });

  it("patches config and omits secrets when the secret field is left blank", async () => {
    renderTab();
    await userEvent.click(
      screen.getAllByRole("button", { name: /编\s*辑/ })[0]
    );
    await waitFor(() =>
      expect(
        (screen.getByLabelText("有赞应用 client_id") as HTMLInputElement).value
      ).toBe("existing-client-id")
    );
    mocks.patch.mockResolvedValue({ data: {} });

    await clickSave();

    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));
    const [url, payload] = mocks.patch.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ];
    expect(url).toBe("/connectors/connectors/conn-youzan-1");
    expect(payload.secrets).toBeUndefined();
    expect(payload.config).toMatchObject({
      client_id: "existing-client-id",
      shop_alias: "shop-a",
    });
  });

  it("exposes the callback URL action for youzan connectors", async () => {
    renderTab();

    await userEvent.click(screen.getByRole("button", { name: /回调地址/ }));

    const code = await findCallbackCode("connectors/conn-youzan-1/callback");
    expect(code.textContent).toBe(callbackUrlFor("conn-youzan-1"));
  });
});
