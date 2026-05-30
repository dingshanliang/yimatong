"use client";

import { useState } from "react";
import { InputNumber } from "antd";

export function YuanInput({
  value = 0,
  onChange,
  ...rest
}: {
  value?: number;
  onChange?: (val: number) => void;
  min?: number;
  max?: number;
  placeholder?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const [display, setDisplay] = useState<number | null>(value / 100);

  const handleChange = (val: number | null) => {
    setDisplay(val);
    if (onChange && val !== null && val !== undefined) {
      onChange(Math.round(val * 100));
    }
  };

  return (
    <InputNumber
      value={display}
      onChange={handleChange}
      min={0.01}
      step={0.01}
      precision={2}
      addonAfter="元"
      {...rest}
    />
  );
}
