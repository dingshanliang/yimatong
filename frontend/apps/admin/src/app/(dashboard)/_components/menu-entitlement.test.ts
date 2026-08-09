import { describe, expect, it } from "vitest";

import { filterMenuItemsByFeatures } from "./menu-entitlement";

function visibleKeys(features?: Record<string, boolean>) {
  const items = filterMenuItemsByFeatures(
    [
      { key: "/", label: "dashboard" },
      { key: "/ai-assistant", label: "ai" },
      { key: "/risk-center", label: "risk" },
      {
        key: "settings-group",
        label: "settings",
        children: [
          { key: "/settings/brand-profile", label: "brand" },
          { key: "/settings/branding", label: "white-label" },
        ],
      },
      {
        key: "portal-group",
        label: "portal",
        children: [{ key: "/channel-portal", label: "channel-portal" }],
      },
    ],
    features
  );

  return JSON.stringify(items);
}

describe("Admin menu entitlement filtering", () => {
  it("hides paid capabilities when the live payload is absent", () => {
    const menu = visibleKeys();
    expect(menu).toContain("/settings/brand-profile");
    expect(menu).not.toContain("/settings/branding");
    expect(menu).not.toContain("/ai-assistant");
    expect(menu).not.toContain("/risk-center");
    expect(menu).not.toContain("/channel-portal");
    expect(menu).not.toContain("portal-group");
  });

  it("enables only the capabilities granted by canonical feature keys", () => {
    const menu = visibleKeys({
      ai_assistant: true,
      risk_module: false,
      channel_portal: true,
      white_label: true,
    });
    expect(menu).toContain("/ai-assistant");
    expect(menu).not.toContain("/risk-center");
    expect(menu).toContain("/channel-portal");
    expect(menu).toContain("/settings/branding");
    expect(menu).toContain("/settings/brand-profile");
  });
});
