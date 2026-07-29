import { getAntdTheme, type ThemeMode } from "@yimatong/design-tokens";
import type { ThemeConfig } from "antd";

export type AdminThemeMode = ThemeMode;

export const lightTheme: ThemeConfig = getAntdTheme("light");
export const darkTheme: ThemeConfig = getAntdTheme("dark");

export const adminThemes: Record<AdminThemeMode, ThemeConfig> = {
  light: lightTheme,
  dark: darkTheme,
};

export default lightTheme;
