import { getAntdTheme, type ThemeMode } from "@yimatong/design-tokens";
import type { ThemeConfig } from "antd";

export type PlatformThemeMode = ThemeMode;

export const lightTheme: ThemeConfig = getAntdTheme("light");
export const darkTheme: ThemeConfig = getAntdTheme("dark");

export const platformThemes: Record<PlatformThemeMode, ThemeConfig> = {
  light: lightTheme,
  dark: darkTheme,
};

export default lightTheme;
