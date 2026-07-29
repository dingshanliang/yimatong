import assert from "node:assert/strict";
import test from "node:test";

import { darkTokens, lightTokens, themes } from "../tokens.ts";

function collectPaths(value, prefix = "") {
  if (typeof value !== "object" || value === null) {
    return [prefix];
  }
  return Object.entries(value).flatMap(([key, child]) =>
    collectPaths(child, prefix ? `${prefix}.${key}` : key)
  );
}

test("light/dark 语义 token 键集合完全一致", () => {
  const lightPaths = collectPaths(lightTokens).sort();
  const darkPaths = collectPaths(darkTokens).sort();
  assert.deepEqual(lightPaths, darkPaths);
});

test("themes 恰好包含 light 与 dark 两个主题", () => {
  assert.deepEqual(Object.keys(themes).sort(), ["dark", "light"]);
});

test("主题锚点：light 品牌主色 #16a34a，dark 提亮为 #4ade80", () => {
  assert.equal(lightTokens.color.brand.primary, "#16a34a");
  assert.equal(darkTokens.color.brand.primary, "#4ade80");
});

test("语义 token 所有叶子值非空", () => {
  for (const [mode, tokens] of Object.entries(themes)) {
    for (const path of collectPaths(tokens)) {
      const value = path.split(".").reduce((acc, key) => acc[key], tokens);
      assert.ok(String(value).length > 0, `${mode}.${path} 为空`);
    }
  }
});
