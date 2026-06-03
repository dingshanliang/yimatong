import { theme as antdTheme, type ThemeConfig } from "antd";

export type PlatformThemeMode = "light" | "dark";

const baseToken: NonNullable<ThemeConfig["token"]> = {
  colorPrimary: "#722ed1",
  borderRadius: 6,
  fontSize: 14,
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

export const lightTheme: ThemeConfig = {
  token: {
    ...baseToken,
    colorBgLayout: "#f4f7fb",
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorBorderSecondary: "#edf1f7",
    colorText: "#172033",
    colorTextSecondary: "#5f6b7a",
  },
};

export const darkTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    ...baseToken,
    colorPrimary: "#9254de",
    colorBgBase: "#0a0418",
    colorBgLayout: "#0a0418",
    colorBgContainer: "#14082e",
    colorBgElevated: "#1e0d45",
    colorBorder: "#2d1a5e",
    colorBorderSecondary: "#1e0d45",
    colorText: "#e6edf7",
    colorTextSecondary: "#a790c8",
    colorTextTertiary: "#7f6ea5",
  },
  components: {
    Layout: {
      bodyBg: "#0a0418",
      headerBg: "#14082e",
      siderBg: "#08021a",
      triggerBg: "#0c0420",
      triggerColor: "#d6c3f5",
    },
    Menu: {
      darkItemBg: "#08021a",
      darkSubMenuItemBg: "#0a0420",
      darkItemSelectedBg: "#722ed1",
    },
    Card: {
      headerBg: "transparent",
    },
    Table: {
      headerBg: "#160a35",
      rowHoverBg: "#1e0d45",
    },
  },
};

export const platformThemes: Record<PlatformThemeMode, ThemeConfig> = {
  light: lightTheme,
  dark: darkTheme,
};

export default lightTheme;
