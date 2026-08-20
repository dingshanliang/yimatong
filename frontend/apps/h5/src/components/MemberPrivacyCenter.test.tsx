import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiClient: { post } }));

import { MemberPrivacyCenter } from "./MemberPrivacyCenter";

describe("MemberPrivacyCenter", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    post.mockReset();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    container.remove();
  });

  it("submits a brand-scoped rights request without exposing PII", async () => {
    post.mockResolvedValue({
      data: {
        id: "018f0c86-4c11-7b31-aeb7-94a11b229fd0",
        response_sla_workdays: 15,
      },
    });
    const root = createRoot(container);
    await act(async () =>
      root.render(<MemberPrivacyCenter scanToken="scan-token" />)
    );
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "提交申请")
        ?.click();
    });
    const select = container.querySelector("select") as HTMLSelectElement;
    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype,
        "value"
      )?.set?.call(select, "delete");
      select.dispatchEvent(new Event("change", { bubbles: true }));
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value"
      )?.set?.call(textarea, "请删除非必要的会员联系资料");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "确认提交")
        ?.click();
    });

    expect(post).toHaveBeenCalledWith(
      "/consumers/membership/privacy-requests",
      {
        request_type: "delete",
        reason: "请删除非必要的会员联系资料",
        evidence: {},
      },
      { headers: { Authorization: "Bearer scan-token" } }
    );
    expect(container.textContent).toContain("15 个工作日内答复");
    await act(async () => root.unmount());
  });
});
