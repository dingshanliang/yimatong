import { AxiosError, type AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockPost } = vi.hoisted(() => ({ mockPost: vi.fn() }));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    default: { post: mockPost },
    registerPlatformLogoutHandler: vi.fn(),
  };
});

import {
  buildPlatformLoginErrorMessage,
  usePlatformAuth,
} from "../platform-auth";

function axiosErrorWith(
  status: number,
  data: unknown,
  headers: Record<string, string> = {}
): AxiosError {
  const response = {
    status,
    data,
    headers,
  } as unknown as AxiosResponse;
  return new AxiosError(
    "Request failed",
    "ERR_BAD_REQUEST",
    undefined,
    undefined,
    response
  );
}

describe("buildPlatformLoginErrorMessage", () => {
  it("surfaces the backend 429 detail together with the Retry-After window", () => {
    const err = axiosErrorWith(
      429,
      { detail: "尝试过于频繁" },
      { "Retry-After": "300" }
    );

    expect(buildPlatformLoginErrorMessage(err)).toBe(
      "尝试过于频繁，约 300 秒后可重试"
    );
  });

  it("keeps the backend detail when a 429 has no usable Retry-After header", () => {
    const err = axiosErrorWith(429, { detail: "尝试过于频繁" }, {});

    expect(buildPlatformLoginErrorMessage(err)).toBe("尝试过于频繁");
  });

  it("passes through non-429 backend details such as 503 maintenance messages", () => {
    const err = axiosErrorWith(503, {
      detail: "登录服务暂时不可用，请稍后重试",
    });

    expect(buildPlatformLoginErrorMessage(err)).toBe(
      "登录服务暂时不可用，请稍后重试"
    );
  });

  it("passes through plain Error messages", () => {
    expect(buildPlatformLoginErrorMessage(new Error("network down"))).toBe(
      "network down"
    );
  });

  it("falls back to the generic message for non-error values", () => {
    expect(buildPlatformLoginErrorMessage("boom")).toBe(
      "登录失败，请检查邮箱和密码"
    );
  });
});

describe("usePlatformAuth", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    usePlatformAuth.setState({ loading: false });
  });

  it("uses only the non-sensitive server session response and never persists credentials", async () => {
    mockPost.mockResolvedValue({
      data: { authenticated: true, principal: "platform_admin" },
    });

    await usePlatformAuth.getState().login("platform@example.com", "secret");

    expect(mockPost).toHaveBeenCalledWith("/platform/auth/login", {
      email: "platform@example.com",
      password: "secret",
    });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("maps a rate-limited login rejection to a message that includes the retry window", async () => {
    mockPost.mockRejectedValue(
      axiosErrorWith(429, { detail: "尝试过于频繁" }, { "Retry-After": "300" })
    );

    await expect(
      usePlatformAuth.getState().login("platform@example.com", "secret")
    ).rejects.toThrow("尝试过于频繁，约 300 秒后可重试");

    expect(usePlatformAuth.getState().loading).toBe(false);
  });

  it("calls the server to clear the HttpOnly platform session", async () => {
    mockPost.mockResolvedValue({ data: { status: "ok" } });

    await usePlatformAuth.getState().logout();

    expect(mockPost).toHaveBeenCalledWith("/platform/auth/logout");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("propagates logout failure so the UI can keep the session visibly retryable", async () => {
    const failure = new Error("network unavailable");
    mockPost.mockRejectedValue(failure);

    await expect(usePlatformAuth.getState().logout()).rejects.toBe(failure);

    expect(mockPost).toHaveBeenCalledWith("/platform/auth/logout");
    expect(usePlatformAuth.getState().loading).toBe(false);
  });
});
