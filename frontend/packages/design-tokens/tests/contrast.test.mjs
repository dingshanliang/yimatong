import assert from "node:assert/strict";
import test from "node:test";

import {
  accessibilityPairs,
  contrastRatio,
  getTokenValue,
  pickAccessibleForeground,
  themes,
} from "../tokens.ts";

for (const [mode, tokens] of Object.entries(themes)) {
  test(`${mode} theme meets registered contrast contracts`, () => {
    for (const pair of accessibilityPairs) {
      const foreground = String(getTokenValue(tokens, pair.foreground));
      const background = String(getTokenValue(tokens, pair.background));
      const ratio = contrastRatio(foreground, background);

      assert.ok(
        ratio >= pair.minimum,
        `${pair.label}: ${foreground} / ${background} = ${ratio.toFixed(2)}, expected ${pair.minimum}`
      );
    }
  });
}

test("tenant colors always receive a readable black or white label", () => {
  for (const color of ["#16a34a", "#f59e0b", "#6d28d9", "#f8fafc"]) {
    const foreground = pickAccessibleForeground(color);
    assert.ok(contrastRatio(foreground, color) >= 4.5);
  }
});

test("contrast thresholds are not rounded", () => {
  assert.ok(contrastRatio("#767676", "#ffffff") >= 4.5);
  assert.ok(contrastRatio("#777777", "#ffffff") < 4.5);
});
