import { theme as antdTheme, type ThemeConfig } from "antd";

export type AdminThemeMode = "light" | "dark";

const baseToken: NonNullable<ThemeConfig["token"]> = {
  colorPrimary: "#1677ff",
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
    colorPrimary: "#4c9dff",
    colorBgBase: "#07111f",
    colorBgLayout: "#07111f",
    colorBgContainer: "#0f1a2a",
    colorBgElevated: "#142033",
    colorBorder: "#26364f",
    colorBorderSecondary: "#1e2d45",
    colorText: "#e6edf7",
    colorTextSecondary: "#a7b4c8",
    colorTextTertiary: "#7f8ea5",
  },
  components: {
    Layout: {
      bodyBg: "#07111f",
      headerBg: "#0f1a2a",
      siderBg: "#06101d",
      triggerBg: "#0b1628",
      triggerColor: "#d6e3f5",
    },
    Menu: {
      darkItemBg: "#06101d",
      darkSubMenuItemBg: "#071426",
      darkItemSelectedBg: "#1668dc",
    },
    Card: {
      headerBg: "transparent",
    },
    Table: {
      headerBg: "#121f31",
      rowHoverBg: "#14243a",
    },
  },
};

export const adminThemes: Record<AdminThemeMode, ThemeConfig> = {
  light: lightTheme,
  dark: darkTheme,
};

export default lightTheme;
