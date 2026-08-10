"use client";

import { Alert, Button } from "antd";
import { createContext, useContext, type ReactNode } from "react";
import type { CanonicalTenantFeature } from "@/lib/plan-entitlement";

const TenantPlanReadOnlyContext = createContext(false);
const TenantFeaturesContext = createContext<Record<string, boolean> | null>(
  null
);

export function useTenantPlanReadOnly(): boolean {
  return useContext(TenantPlanReadOnlyContext);
}

export function useTenantFeatureEnabled(
  feature: CanonicalTenantFeature
): boolean {
  return useContext(TenantFeaturesContext)?.[feature] === true;
}

interface TenantPlanReadOnlyProps {
  active: boolean;
  enabledFeatures?: Record<string, boolean> | null;
  refreshing: boolean;
  onRefresh: () => void;
  children: ReactNode;
}

export default function TenantPlanReadOnly({
  active,
  enabledFeatures,
  refreshing,
  onRefresh,
  children,
}: TenantPlanReadOnlyProps) {
  return (
    <TenantFeaturesContext.Provider value={enabledFeatures ?? null}>
      <TenantPlanReadOnlyContext.Provider value={active}>
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
        <div data-tenant-plan-read-only={active ? "true" : "false"}>
          {children}
        </div>
      </TenantPlanReadOnlyContext.Provider>
    </TenantFeaturesContext.Provider>
  );
}
