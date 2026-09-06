import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
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

describe("LoginPage demo accounts", () => {
  beforeEach(() => {
    mockLogin.mockReset();
    mockLogin.mockResolvedValue(undefined);
    window.sessionStorage.clear();
  });

  it("logs the agency demo button into the demo-agency workspace", async () => {
    render(
      <App>
        <LoginPage />
      </App>
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /代运营工作台/ })
    );

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith(
        "agency_admin@demo.com",
        "demopass",
        { tenantSlug: "demo-agency" }
      );
    });
    expect(screen.getByPlaceholderText("例如 demo")).toHaveValue("demo-agency");
  });

  it("keeps brand demo buttons in the demo workspace", async () => {
    render(
      <App>
        <LoginPage />
      </App>
    );

    fireEvent.click(await screen.findByRole("button", { name: /品牌管理员/ }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("admin@demo.com", "Admin1234", {
        tenantSlug: "demo",
      });
    });
  });
});
