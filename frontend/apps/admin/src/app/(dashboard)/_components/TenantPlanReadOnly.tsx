"use client";

import { Alert, Button } from "antd";
import type { ReactNode } from "react";

interface TenantPlanReadOnlyProps {
  active: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  children: ReactNode;
}

export default function TenantPlanReadOnly({
  active,
  refreshing,
  onRefresh,
  children,
}: TenantPlanReadOnlyProps) {
  return (
    <>
      {active && (
        <Alert
          type="warning"
          showIcon
          banner
          message="当前套餐已到期，后台已切换为只读"
          description="为避免产生超出套餐的新增数据，创建、编辑和提交暂不可用；现有数据与页面仍可查看。请联系一码通平台管理员续期，续期完成后刷新套餐状态即可继续操作。"
          action={
            <Button loading={refreshing} onClick={onRefresh}>
              刷新套餐状态
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      <div
        aria-disabled={active}
        data-tenant-plan-read-only={active ? "true" : "false"}
      >
        {children}
      </div>
    </>
  );
}
