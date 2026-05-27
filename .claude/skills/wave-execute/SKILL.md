---
name: wave-execute
description: Execute a single story from the current wave autonomously
disable-model-invocation: true
---

# Story 执行工作流

## 1. 加载上下文
- 读取 `.yimatong/project-state.json` 获取当前状态
- 读取当前 wave 的 `.yimatong/wave-specs/{wave}.json`
- 读取 `.yimatong/progress.md` 获取历史经验
- 读取 `.yimatong/decisions.md` 获取已有决策
- 读取策略文档 `docs/superpowers/specs/2026-05-27-development-strategy-design.md` 中对应 Wave 的段落（第 4 节详细设计 + 第 7 节技术规范）

## 2. 找到下一个 story
从 wave-spec JSON 中找到第一个 `passes: false` 的 story。如果所有 story 都 `passes: true`，标记 wave 为 complete 并更新 project-state.json。

## 3. TDD 实现
对每个 story：
1. **先写测试**：根据 acceptance_criteria 编写测试用例
2. **运行测试**：确认测试失败（Red）
3. **实现功能**：按策略文档规范编写代码
4. **运行测试**：确认测试通过（Green）
5. **代码质量**：确保 ruff lint 通过

## 4. 关键规范（必须遵守）
- 所有业务表必须包含 tenant_id（即使可从父表推导）
- RLS 使用 `SET LOCAL app.tenant_id` 事务级设置
- public_id 10 位 Base62 + Luhn 校验位
- 幂等控制：Redis 缓存 + 数据库唯一约束双层
- scan_token：resolver 颁发短期 JWT
- JWT：HS256，Bearer Token 模式
- 主键：UUID v7（uuid6 库）
- 所有 migration 必须有 downgrade

## 5. 更新状态
- story 通过：更新 wave-spec JSON 中 passes=true，git commit
- story 失败 3 次：记录到 progress.md，标记 attempt_count=3，继续下一个 story
- 发现新决策：追加到 decisions.md
- 发现新模式/坑：追加到 progress.md

## 6. Wave 完成检查
如果当前 wave 所有 story 都 passes=true：
1. 更新 project-state.json（wave status=complete）
2. 运行黄金链路 E2E 测试
3. 生成验收报告到 .yimatong/verification/{wave}-report.md
4. 自动设置下一个 wave 为 current_wave
