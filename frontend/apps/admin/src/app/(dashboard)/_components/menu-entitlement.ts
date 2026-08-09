import type { MenuProps } from "antd";

import {
  tenantFeatureEnabled,
  type CanonicalTenantFeature,
} from "@/lib/plan-entitlement";

const MENU_FEATURES: Record<string, CanonicalTenantFeature> = {
  "/ai-assistant": "ai_assistant",
  "/risk-center": "risk_module",
  "/risk": "risk_module",
  "/channel-portal": "channel_portal",
  "/store-portal": "channel_portal",
  "/settings/branding": "white_label",
};

type MenuItem = NonNullable<MenuProps["items"]>[number];
type MenuItemWithChildren = MenuItem & { children?: MenuProps["items"] };

export function filterMenuItemsByFeatures(
  items: MenuProps["items"],
  enabledFeatures: Record<string, boolean> | null | undefined
): MenuProps["items"] {
  if (!items) return items;

  return items
    .map((item) => {
      if (!item || !("key" in item)) return item;
      const key = String(item.key);
      const requiredFeature = MENU_FEATURES[key];
      if (
        requiredFeature &&
        !tenantFeatureEnabled(enabledFeatures, requiredFeature)
      ) {
        return null;
      }

      if (!("children" in item) || !Array.isArray(item.children)) return item;
      const children = filterMenuItemsByFeatures(
        item.children,
        enabledFeatures
      );
      if (!children?.length) return null;
      return { ...item, children } as MenuItemWithChildren;
    })
    .filter((item): item is MenuItem => item !== null);
}
