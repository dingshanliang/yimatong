import { App } from "antd";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import dayjs from "dayjs";

import {
  ApiKeysTab,
  apiKeyCreatePayload,
  apiKeyFailureMessage,
} from "./ApiKeysTab";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  user: {
    role: "admin",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    delete: mocks.delete,
  },
  extractErrorMessage: (error: unknown, fallback: string) =>
    (error as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail || fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

const listedKey = {
  id: "0198dca0-1234-7abc-8def-0123456789ab",
  name: "CRM 数据同步",
  key_prefix: "ymt_a1b2c3d4",
  role: "data_reader",
  permissions: ["scan:list"],
  revoked: false,
  expires_at: "2027-01-02T03:04:05Z",
  last_used_at: null,
};

function listPage(items = [listedKey], total = items.length) {
  return { items, total, page: 1, page_size: 20 };
}

function renderTab() {
  return render(
    <App>
      <ApiKeysTab />
    </App>
  );
}

describe("ApiKeysTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "admin";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.get.mockResolvedValue({ data: listPage() });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it.each([
    ["operator", "brand", null],
    ["viewer", "brand", null],
    ["admin", "agency", null],
    ["admin", "agency", "client-tenant"],
  ])(
    "does not render or request credentials for %s in %s context",
    async (role, tenantType, actingTenantId) => {
      mocks.user.role = role;
      mocks.user.tenant_type = tenantType;
      mocks.user.acting_tenant_id = actingTenantId;

      renderTab();

      await Promise.resolve();
      expect(screen.queryByText("新建 API 密钥")).not.toBeInTheDocument();
      expect(mocks.get).not.toHaveBeenCalled();
      expect(mocks.post).not.toHaveBeenCalled();
      expect(mocks.delete).not.toHaveBeenCalled();
    }
  );

  it("shows the real non-secret prefix and never renders returned secret fields", async () => {
    mocks.get.mockResolvedValue({
      data: listPage([
        {
          ...listedKey,
          key: "ymt_should_never_be_listed",
          key_digest: "digest_should_never_be_listed",
        },
      ]),
    });

    renderTab();

    expect(await screen.findByText(listedKey.key_prefix)).toBeInTheDocument();
    expect(screen.getByText("数据只读")).toBeInTheDocument();
    expect(
      screen.queryByText("ymt_should_never_be_listed")
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("digest_should_never_be_listed")
    ).not.toBeInTheDocument();
  });

  it("consumes the paginated list contract and requests the selected page", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { items: [listedKey], total: 21, page: 1, page_size: 20 },
      })
      .mockResolvedValueOnce({
        data: { items: [], total: 21, page: 2, page_size: 20 },
      });

    renderTab();

    expect(await screen.findByText(listedKey.key_prefix)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledWith(
      "/webhooks/api-keys?page=1&page_size=20"
    );

    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() =>
      expect(mocks.get).toHaveBeenLastCalledWith(
        "/webhooks/api-keys?page=2&page_size=20"
      )
    );
  });

  it("distinguishes a load failure from an empty list and retries", async () => {
    mocks.get
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({ data: listPage() });

    renderTab();

    expect(await screen.findByText("API 密钥列表加载失败")).toBeInTheDocument();
    expect(screen.queryByText("还没有 API 密钥")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重新加载/ }));
    expect(await screen.findByText(listedKey.key_prefix)).toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("creates a 90-day credential and keeps its secret only in the one-time modal", async () => {
    mocks.get
      .mockResolvedValueOnce({ data: listPage([]) })
      .mockResolvedValueOnce({ data: listPage() });
    mocks.post.mockResolvedValue({
      data: {
        ...listedKey,
        key: "ymt_once_only_secret",
      },
    });

    renderTab();
    fireEvent.click(
      await screen.findByRole("button", { name: "创建第一把密钥" })
    );
    fireEvent.change(screen.getByLabelText("用途名称"), {
      target: { value: "CRM 数据同步" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建密钥" }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledOnce());
    const [, payload] = mocks.post.mock.calls[0] as [
      string,
      { name: string; role: string; expires_at: string },
    ];
    expect(payload).toMatchObject({
      name: "CRM 数据同步",
      role: "data_reader",
    });
    const days = (Date.parse(payload.expires_at) - Date.now()) / 86_400_000;
    expect(days).toBeGreaterThan(89);
    expect(days).toBeLessThanOrEqual(90.01);
    expect(
      await screen.findByDisplayValue("ymt_once_only_secret")
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "我已安全保存，关闭" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "API 密钥已创建" })
      ).not.toBeInTheDocument()
    );
    expect(screen.getByText(listedKey.key_prefix)).toBeInTheDocument();
  });

  it("reuses one idempotency key while retrying the same failed issue attempt", async () => {
    const idempotencyKey = "11111111-1111-4111-8111-111111111111";
    const randomUuid = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue(idempotencyKey);
    mocks.get
      .mockResolvedValueOnce({
        data: { items: [], total: 0, page: 1, page_size: 20 },
      })
      .mockResolvedValueOnce({
        data: { items: [listedKey], total: 1, page: 1, page_size: 20 },
      });
    mocks.post
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({
        data: { ...listedKey, key: "ymt_once_only_secret" },
      });

    renderTab();
    fireEvent.click(
      await screen.findByRole("button", { name: "创建第一把密钥" })
    );
    fireEvent.change(screen.getByLabelText("用途名称"), {
      target: { value: "CRM 数据同步" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建密钥" }));
    expect(await screen.findByText("API 密钥未创建")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建密钥" }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[0]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(mocks.post.mock.calls[1]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(randomUuid).toHaveBeenCalledOnce();
  });

  it("requires risk acknowledgement before building a permanent credential payload", () => {
    const values = {
      name: " 受控旧系统 ",
      role: "data_reader" as const,
      expiry_mode: "never" as const,
      acknowledge_permanent: false,
      permanent_reason: " 设备固件暂不支持自动轮换凭证 ",
    };

    expect(() => apiKeyCreatePayload(values)).toThrow();
    expect(
      apiKeyCreatePayload({ ...values, acknowledge_permanent: true })
    ).toEqual({
      name: "受控旧系统",
      role: "data_reader",
      expires_at: null,
      permanent_acknowledged: true,
      permanent_reason: "设备固件暂不支持自动轮换凭证",
    });
    expect(() =>
      apiKeyCreatePayload({
        ...values,
        acknowledge_permanent: true,
        permanent_reason: "太短",
      })
    ).toThrow();
    expect(() =>
      apiKeyCreatePayload({
        ...values,
        expiry_mode: "custom",
        expires_at: dayjs().subtract(1, "minute"),
      })
    ).toThrow();
  });

  it("allows exactly 365 days and rejects longer finite credential lifetimes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-10T08:00:00.000Z"));
    const baseValues = {
      name: "年度轮换密钥",
      role: "data_reader" as const,
      expiry_mode: "custom" as const,
    };

    try {
      expect(
        apiKeyCreatePayload({
          ...baseValues,
          expires_at: dayjs("2027-08-10T08:00:00.000Z"),
        }).expires_at
      ).toBe("2027-08-10T08:00:00.000Z");
      expect(() =>
        apiKeyCreatePayload({
          ...baseValues,
          expires_at: dayjs("2027-08-10T08:00:00.001Z"),
        })
      ).toThrow();
      expect(() =>
        apiKeyCreatePayload({
          ...baseValues,
          expires_at: dayjs("9999-12-31T23:59:59.999Z"),
        })
      ).toThrow();
    } finally {
      vi.useRealTimers();
    }
  });

  it("turns tenant, plan, permission, and conflict failures into distinct states", () => {
    expect(
      apiKeyFailureMessage({ response: { status: 401 } }, "加载")
    ).toContain("租户状态");
    expect(
      apiKeyFailureMessage(
        {
          response: {
            status: 403,
            data: { code: "TENANT_PLAN_EXPIRED" },
          },
        },
        "创建"
      )
    ).toContain("套餐已到期");
    expect(
      apiKeyFailureMessage({ response: { status: 403 } }, "创建")
    ).toContain("没有管理");
    expect(
      apiKeyFailureMessage({ response: { status: 409 } }, "轮换")
    ).toContain("状态已变化");
    expect(
      apiKeyFailureMessage({ response: { status: 429 } }, "轮换")
    ).toContain("一分钟");
    expect(
      apiKeyFailureMessage({ response: { status: 503 } }, "创建")
    ).toContain("安全服务");
    expect(
      apiKeyFailureMessage(
        {
          response: {
            status: 409,
            data: { detail: "API key active limit reached" },
          },
        },
        "创建"
      )
    ).toContain("20 把");
  });

  it("rotates and revokes only after explicit confirmation", async () => {
    mocks.get
      .mockResolvedValueOnce({ data: listPage() })
      .mockResolvedValue({ data: listPage() });
    mocks.post.mockResolvedValue({
      data: {
        ...listedKey,
        id: "0198dca0-9999-7abc-8def-0123456789ab",
        key: "ymt_rotated_once",
        key_prefix: "ymt_98765432",
      },
    });
    mocks.delete.mockResolvedValue({ data: { revoked: true } });

    renderTab();
    const row = within(
      (await screen.findByText(listedKey.key_prefix)).closest("tr")!
    );
    fireEvent.click(row.getByRole("button", { name: /轮\s*换/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认轮换/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        `/webhooks/api-keys/${listedKey.id}/rotate`,
        undefined,
        { headers: { "Idempotency-Key": expect.any(String) } }
      )
    );
    expect(
      await screen.findByDisplayValue("ymt_rotated_once")
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "我已安全保存，关闭" }));

    await waitFor(() =>
      expect(screen.getByText(listedKey.key_prefix)).toBeInTheDocument()
    );
    const refreshedRow = within(
      screen.getByText(listedKey.key_prefix).closest("tr")!
    );
    fireEvent.click(refreshedRow.getByRole("button", { name: /吊\s*销/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认吊销/ }));

    await waitFor(() =>
      expect(mocks.delete).toHaveBeenCalledWith(
        `/webhooks/api-keys/${listedKey.id}`
      )
    );
  });

  it("reuses one idempotency key while retrying the same failed rotation", async () => {
    const idempotencyKey = "22222222-2222-4222-8222-222222222222";
    const randomUuid = vi
      .spyOn(crypto, "randomUUID")
      .mockReturnValue(idempotencyKey);
    mocks.get.mockResolvedValue({
      data: { items: [listedKey], total: 1, page: 1, page_size: 20 },
    });
    mocks.post
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce({
        data: {
          ...listedKey,
          id: "0198dca0-9999-7abc-8def-0123456789ab",
          key: "ymt_rotated_once",
          key_prefix: "ymt_98765432",
        },
      });

    renderTab();
    const row = within(
      (await screen.findByText(listedKey.key_prefix)).closest("tr")!
    );
    fireEvent.click(row.getByRole("button", { name: /轮\s*换/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认轮换/ }));
    expect(await screen.findByText("API 密钥操作未完成")).toBeInTheDocument();
    fireEvent.click(row.getByRole("button", { name: /轮\s*换/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认轮换/ }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[0]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(mocks.post.mock.calls[1]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(randomUuid).toHaveBeenCalledOnce();
  });
});
