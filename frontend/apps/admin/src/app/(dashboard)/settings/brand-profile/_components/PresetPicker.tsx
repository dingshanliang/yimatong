"use client";

import { Tooltip } from "antd";

/**
 * 可视化预设卡片选择器（决策 5：圆角/背景预设呈现粒度）
 *
 * 每个 Option 用一个迷你预览方块呈现档位差异，而非纯文字 Radio。
 * 与后端 brand_color.py / H5 brand-theme.ts 的预设档位保持一致。
 */

export interface PresetOption<T extends string> {
  value: T;
  label: string;
  /** 预览方块样式：圆角档传 { radius }，背景档传 { background }，二者只填相关那个 */
  preview: { radius?: number; background?: string };
  desc?: string;
}

interface PresetPickerProps<T extends string> {
  value?: T;
  onChange?: (value: T) => void;
  options: PresetOption<T>[];
}

export default function PresetPicker<T extends string>({
  value,
  onChange,
  options,
}: PresetPickerProps<T>) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => {
        const active = value === opt.value;
        return (
          <Tooltip key={opt.value} title={opt.desc}>
            <button
              type="button"
              onClick={() => onChange?.(opt.value)}
              className="flex flex-col items-center gap-1 rounded-md border-2 p-2 transition-colors"
              style={{
                borderColor: active
                  ? "var(--ymt-color-brand)"
                  : "var(--ymt-color-border)",
                background: active
                  ? "var(--ymt-color-brand-subtle)"
                  : "transparent",
                cursor: "pointer",
              }}
              aria-pressed={active}
            >
              <span
                className="block"
                style={{
                  width: 40,
                  height: 28,
                  borderRadius: opt.preview.radius ?? 10,
                  background:
                    opt.preview.background ?? "var(--ymt-color-bg-surface)",
                  border: "1px solid var(--ymt-color-border)",
                }}
              />
              <span className="text-xs text-text-secondary">{opt.label}</span>
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
