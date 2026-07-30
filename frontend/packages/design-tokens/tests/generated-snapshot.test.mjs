import assert from "node:assert/strict";
import test from "node:test";

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { primitives, themes } from "../tokens.ts";

// 设计 token 产物快照（docs/02_tech/design-system/governance.md）：
// dist/theme.css 是由 scripts/build-css.mjs 从 TS 源生成的提交产物。
// 本测试把生成内容与提交的黄金文件做字符串相等校验，捕捉 token 演进导致的意外变更。
// 失败时先确认是有意变更，再 `pnpm --filter @yimatong/design-tokens build` 重新生成并提交。
const here = dirname(fileURLToPath(import.meta.url));
const distPath = resolve(here, "../dist/theme.css");
const goldenPath = resolve(here, "__snapshots__/theme.css");

const generated = readFileSync(distPath, "utf8");
const golden = readFileSync(goldenPath, "utf8");

test("dist/theme.css 与提交的黄金快照一致", () => {
  assert.equal(
    generated,
    golden,
    "dist/theme.css 与快照不一致。\n" +
      "若为有意的 token 变更：确认 light/dark 双端视觉走查后，\n" +
      "运行 `pnpm --filter @yimatong/design-tokens build` 重新生成 dist/theme.css，\n" +
      "并把新内容复制到 tests/__snapshots__/theme.css 后提交。"
  );
});

test("快照头标注为生成产物", () => {
  assert.ok(
    golden.startsWith("/* Generated from @yimatong/design-tokens."),
    "黄金快照缺少生成产物头部注释——确认未手改。"
  );
});

// 结构化断言：token 源的关键不变量（避免大对象全量快照的噪音）。
test("品牌主色锚点未漂移", () => {
  assert.equal(primitives.color.brand[500], "#16a34a");
  assert.equal(themes.light.color.brand.primary, "#16a34a");
  assert.equal(themes.dark.color.brand.primary, "#4ade80");
});
