# 当前开发状态

> 此文件由自主循环自动更新，反映最新进展。

## 状态：准备启动

- **当前阶段**：Alpha
- **当前 Wave**：A1（工程底座）
- **当前 Story**：等待 wave-spec/A1.json 加载

## 自主循环启动指令

读取 `.yimatong/project-state.json` 获取当前状态。读取当前 wave 的 `.yimatong/wave-specs/{wave}.json`。找到第一个 passes=false 的 story。按 TDD 流程实现：先写测试 → 实现 → 运行测试验证。通过后更新 story.passes=true 并 git commit。如果该 wave 所有 story 都 passes=true，更新 project-state.json 中 wave 状态为 complete，生成验收报告，自动进入下一 wave。如果所有 wave 都 complete，输出 `<promise>PHASE_COMPLETE</promise>`。

每次迭代开始时：
1. 读 `.yimatong/progress.md` 获取经验
2. 读 `.yimatong/decisions.md` 获取决策
3. 遇到未覆盖的决策按最佳实践选择，记录到 decisions.md
4. 3 次失败同一 story 则跳过并记录原因

## 注意事项

- 策略文档是最高权威：`docs/superpowers/specs/2026-05-27-development-strategy-design.md`
- 技术栈：FastAPI + PostgreSQL 16 + Redis 7 + MinIO + Next.js
- Python 包管理用 uv
- 前端用 pnpm workspace
- 所有代码必须通过 ruff lint
- 每个 story 必须有对应的测试
