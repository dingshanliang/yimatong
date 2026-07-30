#!/usr/bin/env node
// 设计体系门禁脚本（docs/02_tech/design-system/governance.md）
//
// 作用：跑各 app 的 ESLint / stylelint，按「文件+规则」粒度与
// frontend/.design-gates-baseline.json 比对，只报「baseline 未收录的增量违规」。
//
//   - 默认（check）：报告增量违规；存在增量则退出码 1（CI 阻断）
//   - --update-baseline：把当前全部违规写回 baseline.json（仅门禁落地/批次收敛时用）
//
// baseline 以 {appId,linter,ruleId,path} 为键收录现状；行号会随编辑漂移，故不参与匹配。
// 每个迁移批次完成时移除该批页面条目，批 3 完成时 baseline 必须清空。
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const baselinePath = resolve(root, ".design-gates-baseline.json");

const UPDATE = process.argv.includes("--update-baseline");

// 受门禁约束的设计 token 规则集合（其它 eslint/stylelint 规则不在本门禁范围）。
const DESIGN_GATE_RULES = new Set([
  "no-restricted-syntax",
  "tailwindcss/no-arbitrary-value",
  "color-no-hex",
  "declaration-property-value-disallowed-list",
]);

// 每条 = { appId, linter, ruleId, path }；path 为相对 app 根的路径。
function collectCurrentViolations() {
  const violations = [];
  const apps = [
    { appId: "h5", cwd: resolve(root, "apps/h5") },
    { appId: "admin", cwd: resolve(root, "apps/admin") },
    { appId: "platform", cwd: resolve(root, "apps/platform") },
  ];

  for (const { appId, cwd } of apps) {
    // ESLint（JSON 输出）
    try {
      const out = execFileSync("npx", ["eslint", "--format", "json", "."], {
        cwd,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
        maxBuffer: 64 * 1024 * 1024,
      });
      for (const file of JSON.parse(out || "[]")) {
        const rel = file.filePath.split(`/frontend/apps/${appId}/`)[1];
        if (!rel) continue;
        for (const m of file.messages) {
          if (m.ruleId && DESIGN_GATE_RULES.has(m.ruleId)) {
            violations.push({
              appId,
              linter: "eslint",
              ruleId: m.ruleId,
              path: rel,
            });
          }
        }
      }
    } catch (err) {
      // eslint 非 0 退出时仍有 JSON 输出在 stdout（err.stdout）。
      const out = err?.stdout;
      if (out) {
        try {
          for (const file of JSON.parse(out)) {
            const rel = file.filePath.split(`/frontend/apps/${appId}/`)[1];
            if (!rel) continue;
            for (const m of file.messages) {
              if (m.ruleId && DESIGN_GATE_RULES.has(m.ruleId)) {
                violations.push({
                  appId,
                  linter: "eslint",
                  ruleId: m.ruleId,
                  path: rel,
                });
              }
            }
          }
        } catch {
          console.error(`design-gates: 无法解析 ${appId} eslint 输出`);
        }
      } else {
        console.error(
          `design-gates: 运行 ${appId} eslint 失败: ${err.message}`
        );
      }
    }

    // stylelint 仅 admin/platform（JSON 输出）
    if (appId === "h5") continue;
    const cfg = resolve(root, "stylelint.config.mjs");
    try {
      const out = execFileSync(
        "npx",
        [
          "stylelint",
          "**/*.{css,scss}",
          "--config",
          cfg,
          "--formatter",
          "json",
        ],
        {
          cwd,
          encoding: "utf8",
          stdio: ["ignore", "pipe", "ignore"],
          maxBuffer: 64 * 1024 * 1024,
        }
      );
      for (const file of JSON.parse(out || "[]")) {
        const rel = file.source.split(`/frontend/apps/${appId}/`)[1];
        if (!rel) continue;
        for (const w of file.warnings) {
          if (w.rule && DESIGN_GATE_RULES.has(w.rule)) {
            violations.push({
              appId,
              linter: "stylelint",
              ruleId: w.rule,
              path: rel,
            });
          }
        }
      }
    } catch (err) {
      const out = err?.stdout;
      if (out) {
        try {
          for (const file of JSON.parse(out)) {
            const rel = file.source.split(`/frontend/apps/${appId}/`)[1];
            if (!rel) continue;
            for (const w of file.warnings) {
              if (w.rule && DESIGN_GATE_RULES.has(w.rule)) {
                violations.push({
                  appId,
                  linter: "stylelint",
                  ruleId: w.rule,
                  path: rel,
                });
              }
            }
          }
        } catch {
          console.error(`design-gates: 无法解析 ${appId} stylelint 输出`);
        }
      } else {
        console.error(
          `design-gates: 运行 ${appId} stylelint 失败: ${err.message}`
        );
      }
    }
  }
  return violations;
}

function violationKey(v) {
  return `${v.appId}\t${v.linter}\t${v.ruleId}\t${v.path}`;
}

const current = collectCurrentViolations();

if (UPDATE) {
  // 以「键 → 出现次数」写回 baseline。
  const counts = {};
  for (const v of current)
    counts[violationKey(v)] = (counts[violationKey(v)] || 0) + 1;
  const entries = Object.entries(counts)
    .map(([key, count]) => {
      const [appId, linter, ruleId, path] = key.split("\t");
      return { appId, linter, ruleId, path, count };
    })
    .sort(
      (a, b) =>
        a.appId.localeCompare(b.appId) ||
        a.path.localeCompare(b.path) ||
        a.ruleId.localeCompare(b.ruleId)
    );
  writeFileSync(baselinePath, JSON.stringify(entries, null, 2) + "\n", "utf8");
  console.log(
    `design-gates: baseline 已更新，共 ${entries.length} 个文件×规则条目（${current.length} 处违规）。`
  );
  process.exit(0);
}

// check 模式：报 baseline 未收录的增量。
const baseline = existsSync(baselinePath)
  ? JSON.parse(readFileSync(baselinePath, "utf8"))
  : [];
const baselineKeys = new Set(
  baseline.map((b) => `${b.appId}\t${b.linter}\t${b.ruleId}\t${b.path}`)
);

const incremental = current.filter((v) => !baselineKeys.has(violationKey(v)));
const surplus = baseline.filter(
  (b) =>
    !current.some(
      (v) =>
        violationKey(v) === `${b.appId}\t${b.linter}\t${b.ruleId}\t${b.path}`
    )
);

if (incremental.length === 0) {
  console.log(
    `design-gates: PASS（无增量违规）。baseline 收录 ${baseline.length} 条，当前 ${new Set(current.map(violationKey)).size} 条。`
  );
  if (surplus.length > 0) {
    console.log(
      `design-gates: 提示 baseline 有 ${surplus.length} 条已修复条目可移除（非阻断）。`
    );
  }
  process.exit(0);
}

console.error(
  `design-gates: FAIL — 发现 ${incremental.length} 处增量违规（baseline 未收录）：\n`
);
const incByKey = {};
for (const v of incremental) {
  const k = violationKey(v);
  incByKey[k] = (incByKey[k] || 0) + 1;
}
for (const [key, count] of Object.entries(incByKey).sort()) {
  const [appId, linter, ruleId, path] = key.split("\t");
  console.error(`  [${appId}/${linter}] ${path}  (${ruleId}, ${count} 处)`);
}
console.error(
  "\n设计 token 违规须消费 design tokens（var(--ymt-*)）而非硬编码值。"
);
console.error(
  "修复后重跑 `pnpm lint:design`；新门禁落地或批次收敛用 `node scripts/design-gates.mjs --update-baseline`。"
);
process.exit(1);
