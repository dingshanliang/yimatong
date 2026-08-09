import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockPost } = vi.hoisted(() => ({ mockPost: vi.fn() }));

vi.mock("../api", () => ({
  default: { post: mockPost },
  registerPlatformLogoutHandler: vi.fn(),
}));

import { usePlatformAuth } from "../platform-auth";

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
