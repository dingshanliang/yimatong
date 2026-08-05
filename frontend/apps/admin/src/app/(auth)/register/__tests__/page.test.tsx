import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RegisterPage from "../page";

const mockPost = vi.fn();
const mockExtractErrorMessage = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("invite_code=INVITE123"),
}));

vi.mock("@/lib/api", () => ({
  default: {
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (...args: unknown[]) => mockExtractErrorMessage(...args),
}));

async function fillRegistrationForm() {
  fireEvent.change(await screen.findByLabelText("品牌或企业名称"), {
    target: { value: "新品牌食品" },
  });
  fireEvent.change(screen.getByLabelText("所属行业（可选）"), {
    target: { value: "食品饮料" },
  });
  fireEvent.change(screen.getByLabelText("管理员姓名"), {
    target: { value: "张三" },
  });
  fireEvent.change(screen.getByLabelText("管理员邮箱"), {
    target: { value: "owner@example.com" },
  });
  fireEvent.change(screen.getByLabelText("设置登录密码"), {
    target: { value: "Secure123" },
  });
  fireEvent.change(screen.getByLabelText("确认登录密码"), {
    target: { value: "Secure123" },
  });
}

describe("RegisterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    mockPost.mockResolvedValue({
      data: {
        tenant_id: "tenant-1",
        tenant_slug: "xin-pin-pai-shi-pin",
        message: "注册成功，租户已开通",
      },
    });
    mockExtractErrorMessage.mockReturnValue("邀请码无效、已过期或已用完");
  });

  it("prefills the delivered invite code and submits the public contract", async () => {
    render(<RegisterPage />);

    expect(await screen.findByLabelText("邀请码")).toHaveValue("INVITE123");
    await fillRegistrationForm();
    fireEvent.click(screen.getByRole("button", { name: "提交并开通品牌账号" }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/invite-codes/register",
        {
          invite_code: "INVITE123",
          name: "新品牌食品",
          admin_email: "owner@example.com",
          admin_name: "张三",
          admin_password: "Secure123",
          industry: "食品饮料",
        },
        { headers: { "Idempotency-Key": expect.any(String) } }
      );
    });
    expect(await screen.findByText("品牌账号已创建")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "核对信息并登录" })
    ).toHaveAttribute("href", "/login");
    expect(
      JSON.parse(sessionStorage.getItem("registration-login-handoff") ?? "{}")
    ).toEqual({
      email: "owner@example.com",
      tenantSlug: "xin-pin-pai-shi-pin",
    });
  });

  it("reuses the idempotency key after an ambiguous failure", async () => {
    mockPost
      .mockRejectedValueOnce(new Error("network disconnected"))
      .mockResolvedValueOnce({
        data: {
          tenant_id: "tenant-1",
          tenant_slug: "retry-tenant",
          message: "注册成功",
        },
      });
    render(<RegisterPage />);
    await fillRegistrationForm();

    fireEvent.click(screen.getByRole("button", { name: "提交并开通品牌账号" }));
    expect(await screen.findByText("暂时无法完成注册")).toBeInTheDocument();
    const firstKey = mockPost.mock.calls[0][2].headers["Idempotency-Key"];

    fireEvent.click(screen.getByRole("button", { name: "提交并开通品牌账号" }));
    await screen.findByText("品牌账号已创建");
    expect(mockPost.mock.calls[1][2].headers["Idempotency-Key"]).toBe(firstKey);
  });

  it("keeps the form recoverable when an invite is invalid or no longer usable", async () => {
    mockPost.mockRejectedValue({ response: { status: 400 } });
    render(<RegisterPage />);

    await fillRegistrationForm();
    fireEvent.click(screen.getByRole("button", { name: "提交并开通品牌账号" }));

    expect(await screen.findByText("暂时无法完成注册")).toBeInTheDocument();
    expect(
      screen.getByText(/邀请码已过期或已用完，请联系发放方重新生成/)
    ).toBeInTheDocument();
    expect(screen.getByLabelText("邀请码")).toHaveValue("INVITE123");
    expect(
      screen.getByRole("button", { name: "提交并开通品牌账号" })
    ).toBeEnabled();

    const failedKey = mockPost.mock.calls[0][2].headers["Idempotency-Key"];
    mockPost.mockResolvedValue({
      data: {
        tenant_id: "tenant-2",
        tenant_slug: "corrected-tenant",
        message: "注册成功",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交并开通品牌账号" }));
    await screen.findByText("品牌账号已创建");
    expect(mockPost.mock.calls[1][2].headers["Idempotency-Key"]).not.toBe(
      failedKey
    );
  });
});
