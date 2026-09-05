import { describe, expect, it } from "vitest";
import { AxiosError, type AxiosResponse } from "axios";

import { extractErrorMessage, refreshPreservesActingContext } from "../api";

function jwt(payload: Record<string, unknown>) {
  const encode = (value: Record<string, unknown>) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode(payload)}.signature`;
}

function axiosErrorWith(data: unknown): AxiosError {
  return new AxiosError(
    "Request failed with status code 400",
    "ERR_BAD_REQUEST",
    undefined,
    undefined,
    {
      data,
      status: 400,
      statusText: "Bad Request",
      headers: {},
      config: {},
    } as AxiosResponse
  );
}

describe("refreshPreservesActingContext", () => {
  it("rejects replay when refresh drops the acting tenant", () => {
    expect(
      refreshPreservesActingContext(
        jwt({ acting_tenant_id: "client-1" }),
        jwt({ tenant_type: "agency" })
      )
    ).toBe(false);
  });

  it("allows replay when the same acting tenant is retained", () => {
    expect(
      refreshPreservesActingContext(
        jwt({ acting_tenant_id: "client-1" }),
        jwt({ acting_tenant_id: "client-1" })
      )
    ).toBe(true);
  });
});

describe("extractErrorMessage", () => {
  it("returns message from structured {code, message} detail", () => {
    const error = axiosErrorWith({
      detail: {
        code: "TENANT_FEATURE_DISABLED",
        message: "当前套餐未开通此功能",
      },
    });
    expect(extractErrorMessage(error)).toBe("当前套餐未开通此功能");
  });

  it("prefers msg over message when object detail has both", () => {
    const error = axiosErrorWith({
      detail: { msg: "msg 字段优先", message: "message 字段备选" },
    });
    expect(extractErrorMessage(error)).toBe("msg 字段优先");
  });

  it("returns message from top-level response data", () => {
    const error = axiosErrorWith({ message: "顶层 message" });
    expect(extractErrorMessage(error)).toBe("顶层 message");
  });

  it("keeps string detail passthrough", () => {
    const error = axiosErrorWith({ detail: "撤销授权失败" });
    expect(extractErrorMessage(error)).toBe("撤销授权失败");
  });

  it("falls back when object detail has neither msg nor message", () => {
    const error = axiosErrorWith({ detail: { code: "SOME_ERROR" } });
    expect(extractErrorMessage(error, "操作失败")).toBe("操作失败");
  });
});
