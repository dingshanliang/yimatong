import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginPage from "../page";

const mockLogin = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/auth", () => {
  const useAuthStore = (
    selector: (state: { login: typeof mockLogin }) => unknown
  ) => selector({ login: mockLogin });
  useAuthStore.getState = () => ({ user: null });
  return { useAuthStore };
});

describe("LoginPage registration handoff", () => {
  beforeEach(() => {
    window.sessionStorage.setItem(
      "registration-login-handoff",
      JSON.stringify({
        email: "owner@example.com",
        tenantSlug: "new-brand",
      })
    );
  });

  it("prefills the registered email and workspace for verification", async () => {
    render(<LoginPage />);

    expect(await screen.findByPlaceholderText("邮箱")).toHaveValue(
      "owner@example.com"
    );
    expect(screen.getByPlaceholderText("例如 demo")).toHaveValue("new-brand");
    expect(screen.getByPlaceholderText("密码")).toHaveValue("");
    expect(sessionStorage.getItem("registration-login-handoff")).toBeNull();
  });
});
