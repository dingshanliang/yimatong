import { fireEvent, render, screen } from "@testing-library/react";
import Link from "next/link";
import { describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly, {
  useTenantPlanReadOnly,
} from "./TenantPlanReadOnly";

function CatalogMutationState() {
  const readOnly = useTenantPlanReadOnly();
  return <button disabled={readOnly}>保存产品</button>;
}

describe("TenantPlanReadOnly", () => {
  it("keeps read navigation visible while explaining that writes are centrally blocked", () => {
    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <Link href="/campaigns">查看活动</Link>
        <button type="button">新建活动</button>
      </TenantPlanReadOnly>
    );

    const readLink = screen.getByRole("link", { name: "查看活动" });
    const childButton = screen.getByRole("button", { name: "新建活动" });

    expect(readLink).toHaveAttribute("href", "/campaigns");
    expect(readLink).toBeEnabled();
    expect(childButton).toBeEnabled();
    expect(readLink.parentElement).toHaveAttribute(
      "data-tenant-plan-read-only",
      "true"
    );
    expect(readLink.parentElement).not.toHaveAttribute("aria-disabled");
    expect(screen.getByText(/请联系一码通平台管理员续期/)).toBeVisible();
  });

  it("lets the user refresh the entitlement after renewal", () => {
    const onRefresh = vi.fn();
    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={onRefresh}>
        <div>只读数据</div>
      </TenantPlanReadOnly>
    );

    fireEvent.click(screen.getByRole("button", { name: "刷新套餐状态" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("exposes the plan state so catalog forms can disable writes without losing state", () => {
    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <CatalogMutationState />
      </TenantPlanReadOnly>
    );

    expect(screen.getByRole("button", { name: "保存产品" })).toBeDisabled();
  });
});
