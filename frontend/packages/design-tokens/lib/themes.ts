export type ThemeMode = "light" | "dark";

export interface SemanticTokens {
  color: {
    bg: {
      canvas: string;
      surface: string;
      elevated: string;
      muted: string;
    };
    text: {
      primary: string;
      secondary: string;
      tertiary: string;
      inverse: string;
    };
    border: {
      base: string;
      strong: string;
    };
    brand: {
      primary: string;
      subtle: string;
      accent: string;
    };
    action: {
      primary: string;
      primaryHover: string;
      primaryActive: string;
      onPrimary: string;
      link: string;
      accent: string;
      onAccent: string;
    };
    feedback: {
      success: string;
      successBg: string;
      warning: string;
      warningBg: string;
      danger: string;
      dangerBg: string;
      info: string;
      infoBg: string;
    };
    focus: {
      ring: string;
    };
    chrome: {
      sider: string;
      header: string;
      tableHeader: string;
      rowHover: string;
      rowSelected: string;
    };
  };
  shadow: {
    surface: string;
    overlay: string;
  };
}

export interface AccessibilityPair {
  label: string;
  foreground: string;
  background: string;
  minimum: 3 | 4.5 | 7;
}

export const lightTokens = {
  color: {
    bg: {
      canvas: "#f6f8f5",
      surface: "#ffffff",
      elevated: "#ffffff",
      muted: "#f0f4ef",
    },
    text: {
      primary: "#17211a",
      secondary: "#435348",
      tertiary: "#5f7064",
      inverse: "#ffffff",
    },
    border: {
      base: "#d9e3da",
      strong: "#738178",
    },
    brand: {
      primary: "#16a34a",
      subtle: "#dcfce7",
      accent: "#f59e0b",
    },
    action: {
      primary: "#15803d",
      primaryHover: "#166534",
      primaryActive: "#14532d",
      onPrimary: "#ffffff",
      link: "#15803d",
      accent: "#f59e0b",
      onAccent: "#17211a",
    },
    feedback: {
      success: "#166534",
      successBg: "#f0fdf4",
      warning: "#92400e",
      warningBg: "#fffbeb",
      danger: "#b91c1c",
      dangerBg: "#fef2f2",
      info: "#1d4ed8",
      infoBg: "#eff6ff",
    },
    focus: {
      ring: "#166534",
    },
    chrome: {
      sider: "#092c18",
      header: "#ffffff",
      tableHeader: "#eef4ee",
      rowHover: "#f2f8f2",
      rowSelected: "#dcfce7",
    },
  },
  shadow: {
    surface: "0 10px 28px rgb(15 61 34 / 0.08)",
    overlay: "0 18px 48px rgb(15 61 34 / 0.14)",
  },
} as const satisfies SemanticTokens;

export const darkTokens = {
  color: {
    bg: {
      canvas: "#07130b",
      surface: "#0d1f13",
      elevated: "#14291a",
      muted: "#112318",
    },
    text: {
      primary: "#edf7ef",
      secondary: "#bdccbf",
      tertiary: "#9dae9f",
      inverse: "#07130b",
    },
    border: {
      base: "#294333",
      strong: "#7e9583",
    },
    brand: {
      primary: "#4ade80",
      subtle: "#123b20",
      accent: "#fbbf24",
    },
    action: {
      primary: "#4ade80",
      primaryHover: "#86efac",
      primaryActive: "#22c55e",
      onPrimary: "#07130b",
      link: "#86efac",
      accent: "#fbbf24",
      onAccent: "#1f1603",
    },
    feedback: {
      success: "#86efac",
      successBg: "#123b20",
      warning: "#fcd34d",
      warningBg: "#3d2a08",
      danger: "#fca5a5",
      dangerBg: "#451a1a",
      info: "#93c5fd",
      infoBg: "#172554",
    },
    focus: {
      ring: "#86efac",
    },
    chrome: {
      sider: "#041108",
      header: "#0d1f13",
      tableHeader: "#13281a",
      rowHover: "#17321f",
      rowSelected: "#1b4728",
    },
  },
  shadow: {
    surface: "0 14px 34px rgb(0 0 0 / 0.34)",
    overlay: "0 22px 56px rgb(0 0 0 / 0.48)",
  },
} as const satisfies SemanticTokens;

export const themes: Record<ThemeMode, SemanticTokens> = {
  light: lightTokens,
  dark: darkTokens,
};

export const accessibilityPairs: readonly AccessibilityPair[] = [
  {
    label: "主文字 / 页面",
    foreground: "color.text.primary",
    background: "color.bg.canvas",
    minimum: 7,
  },
  {
    label: "主文字 / 容器",
    foreground: "color.text.primary",
    background: "color.bg.surface",
    minimum: 7,
  },
  {
    label: "次文字 / 容器",
    foreground: "color.text.secondary",
    background: "color.bg.surface",
    minimum: 4.5,
  },
  {
    label: "辅助文字 / 容器",
    foreground: "color.text.tertiary",
    background: "color.bg.surface",
    minimum: 4.5,
  },
  {
    label: "主操作文字 / 主操作",
    foreground: "color.action.onPrimary",
    background: "color.action.primary",
    minimum: 4.5,
  },
  {
    label: "点缀操作文字 / 点缀操作",
    foreground: "color.action.onAccent",
    background: "color.action.accent",
    minimum: 4.5,
  },
  {
    label: "链接 / 容器",
    foreground: "color.action.link",
    background: "color.bg.surface",
    minimum: 4.5,
  },
  {
    label: "控件边界 / 容器",
    foreground: "color.border.strong",
    background: "color.bg.surface",
    minimum: 3,
  },
  {
    label: "焦点环 / 容器",
    foreground: "color.focus.ring",
    background: "color.bg.surface",
    minimum: 3,
  },
  {
    label: "品牌图形 / 页面",
    foreground: "color.brand.primary",
    background: "color.bg.surface",
    minimum: 3,
  },
  {
    label: "成功文字 / 成功底",
    foreground: "color.feedback.success",
    background: "color.feedback.successBg",
    minimum: 4.5,
  },
  {
    label: "警告文字 / 警告底",
    foreground: "color.feedback.warning",
    background: "color.feedback.warningBg",
    minimum: 4.5,
  },
  {
    label: "危险文字 / 危险底",
    foreground: "color.feedback.danger",
    background: "color.feedback.dangerBg",
    minimum: 4.5,
  },
  {
    label: "信息文字 / 信息底",
    foreground: "color.feedback.info",
    background: "color.feedback.infoBg",
    minimum: 4.5,
  },
] as const;
