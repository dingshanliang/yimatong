---
description: Iterate through product modules systematically: review → plan → execute. Designed for /loop self-paced automation.
---

# Module Iterate: 系统化模块迭代

你是一个自动化模块迭代引擎。每次调用处理一个模块的一个阶段。

## 流程

### Step 1: 读取状态

读取 `docs/superpowers/module-iterate-state.json`，找到第一个 `status != "completed"` 的模块。

如果所有模块都是 `completed`：
- 输出 "🎉 所有 30 个模块迭代完成！"
- 停止 /loop

### Step 2: 阶段调度

根据模块的 `status` 字段决定执行哪个阶段：

| 当前状态 | 执行动作 | 完成后状态 |
|---------|---------|-----------|
| `pending` | Review 阶段 | `reviewed` |
| `reviewed` | Plan 阶段 | `planned` |
| `planned` | Execute 阶段（首轮，3 任务） | `executing` |
| `executing` | Execute 阶段（续接，3 任务） | `executing` 或 `completed` |

### Step 3: Review 阶段

当 `status == "pending"` 时执行：

1. **创建 bead**（如尚未创建）：
   ```bash
   bd add "Module Review: {模块名}" --dolt-auto-commit off
   ```
   将返回的 bead ID 写入 state.json。

2. **调用 module-review skill**：
   使用 Skill 工具调用 `module-review`，传入模块 ID 和文件列表。

3. **保存 review 结果**：
   将输出写入 `docs/superpowers/reviews/2026-06-09-{module-id}.md`

4. **更新 state.json**：
   - `status = "reviewed"`
   - `review_file = "docs/superpowers/reviews/2026-09-{module-id}.md"`
   - `findings_count` = 从 review 结果中提取各级别数量

5. **Git 提交**：
   ```bash
   git add docs/superpowers/reviews/ docs/superpowers/module-iterate-state.json
   git commit -m "docs(review): add {module-id} module review"
   ```

### Step 4: Plan 阶段

当 `status == "reviewed"` 时执行：

1. **读取 review 文件**，提取所有 Critical 和 High 级别的发现。

2. **生成改进计划**，格式参考 `docs/superpowers/plans/2026-06-09-anti-diversion-fixes.md`：
   - 每个计划包含：目标、架构说明、文件变更映射、编号任务（checkbox 格式）
   - 按优先级排序：Critical → High → Medium
   - 每个任务包含：具体步骤、受影响文件、验证方法

3. **保存计划**：
   写入 `docs/superpowers/plans/2026-06-09-{module-id}-hardening.md`

4. **更新 state.json**：
   - `status = "planned"`
   - `plan_file = "docs/superpowers/plans/2026-06-09-{module-id}-hardening.md"`
   - `tasks_total` = 计划中的总任务数

5. **Git 提交**：
   ```bash
   git add docs/superpowers/plans/ docs/superpowers/module-iterate-state.json
   git commit -m "docs(plan): add {module-id} module hardening plan"
   ```

### Step 5: Execute 阶段

当 `status == "planned"` 或 `status == "executing"` 时执行：

1. **调用 module-execute skill**：
   传入 `module_id`、`plan_file`、`tasks_completed`。

2. **module-execute 将**：
   - 创建 feature 分支（如需要）
   - 执行最多 3 个任务
   - 每个任务后提交并更新 state.json
   - 运行测试验证

3. **判断完成状态**：
   - 如 `tasks_completed == tasks_total`：运行全量测试，通过则 `status = "completed"`
   - 否则保持 `status = "executing"`，等待下一轮

4. **完成时**：
   - 关闭 bead
   - 输出模块完成摘要
   - 提交最终状态

### Step 6: 输出摘要

每次调用结束，输出本次处理的摘要：

```
## 模块迭代进度

**当前模块**: {module-id} ({module-name})
**阶段**: {reviewed|planned|executing|completed}
**进度**: {tasks_completed}/{tasks_total} 任务完成

### 本轮完成
- [具体做了什么]

### 发现统计
- Critical: # | High: # | Medium: # | Low: #

### 下一步
- 下一轮将继续处理 [模块名] 的 [阶段]
```

## 注意事项

- **每轮只处理一个阶段**（review 或 plan 或最多 3 个 execute 任务）
- **不要试图在一次调用中完成整个模块**
- **遇到测试失败时暂停**，不要继续执行
- **遵循 CLAUDE.md 中的所有编码规范**
- **执行前确认 Python 虚拟环境已激活**：`source backend/.venv/bin/activate`
