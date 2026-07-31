import { theme as antdTheme, type ThemeConfig } from "antd";

import { primitives } from "./primitives.ts";
import { themes, type ThemeMode } from "./themes.ts";

export function getAntdTheme(mode: ThemeMode): ThemeConfig {
  const selected = themes[mode];
  const isDark = mode === "dark";

  return {
    ...(isDark ? { algorithm: antdTheme.darkAlgorithm } : {}),
    cssVar: { key: `yimatong-${mode}`, prefix: "ymt" },
    token: {
      colorPrimary: selected.color.action.primary,
      colorPrimaryHover: selected.color.action.primaryHover,
      colorPrimaryActive: selected.color.action.primaryActive,
      colorLink: selected.color.action.link,
      colorLinkHover: selected.color.action.primaryHover,
      colorTextLightSolid: selected.color.action.onPrimary,
      colorBgBase: selected.color.bg.canvas,
      colorBgLayout: selected.color.bg.canvas,
      colorBgContainer: selected.color.bg.surface,
      colorBgElevated: selected.color.bg.elevated,
      colorFillAlter: selected.color.bg.muted,
      colorText: selected.color.text.primary,
      colorTextSecondary: selected.color.text.secondary,
      colorTextTertiary: selected.color.text.tertiary,
      colorBorder: selected.color.border.strong,
      colorBorderSecondary: selected.color.border.base,
      colorSuccess: selected.color.feedback.success,
      colorWarning: selected.color.feedback.warning,
      colorError: selected.color.feedback.danger,
      colorInfo: selected.color.feedback.info,
      controlOutline: selected.color.focus.ring,
      controlOutlineWidth: primitives.focus.width,
      lineWidthFocus: primitives.focus.width,
      controlHeight: primitives.target.desktop,
      borderRadius: primitives.radius.sm,
      borderRadiusLG: primitives.radius.md,
      fontSize: primitives.font.size.base,
      fontFamily: primitives.font.family.body,
      fontWeightStrong: primitives.font.weight.semibold,
      boxShadow: selected.shadow.surface,
      boxShadowSecondary: selected.shadow.overlay,
    },
    components: {
      Layout: {
        bodyBg: selected.color.bg.canvas,
        headerBg: selected.color.chrome.header,
        siderBg: selected.color.chrome.sider,
        triggerBg: selected.color.chrome.sider,
        triggerColor: selected.color.text.inverse,
      },
      Menu: {
        darkItemBg: selected.color.chrome.sider,
        darkSubMenuItemBg: selected.color.chrome.sider,
        darkItemSelectedBg: selected.color.action.primary,
        darkItemSelectedColor: selected.color.action.onPrimary,
        // Sider 始终是深绿色（chrome.sider 在 light/dark 都接近黑），Menu 通过
        // theme="dark" 固定到 sider。若让 darkItemColor 走算法派生，dark 模式下会
        // 从 text.inverse（dark=#07130b 近黑）推导，导致非高亮项文字在深色 sider 上
        // 不可见。sider 是固定深色容器，菜单文字必须始终白色，与 app 主题解耦。
        darkItemColor: "#ffffff",
        darkItemHoverColor: "#ffffff",
        darkItemDisabledColor: "rgba(255, 255, 255, 0.35)",
      },
      Card: {
        headerBg: "transparent",
      },
      Table: {
        headerBg: selected.color.chrome.tableHeader,
        rowHoverBg: selected.color.chrome.rowHover,
        rowSelectedBg: selected.color.chrome.rowSelected,
      },
      Button: {
        primaryColor: selected.color.action.onPrimary,
      },
    },
  };
}
