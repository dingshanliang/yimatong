---
description: Iterate through product modules systematically - review, plan, execute. Designed for /loop self-paced automation. Auto-discovers modules on first run.
---

# Module Iterate: 系统化模块迭代

你是一个自动化模块迭代引擎。每次调用处理一个模块的一个阶段。

## 流程

### Step 0: 项目发现（仅首次运行）

**如果 `docs/superpowers/module-iterate-state.json` 不存在**，执行项目发现：

1. **读取 CLAUDE.md** 获取项目结构信息（后端目录、前端目录、测试目录等）
2. **扫描后端模块**：
   - 找到 API 路由目录（如 `backend/app/api/v1/`），每个路由文件 = 一个模块
   - 找到 Service 目录，按文件名匹配到对应模块
   - 找到 Model 目录，按文件名匹配到对应模块
   - 找到测试目录，按文件名匹配
3. **扫描前端模块**：
   - 找到前端页面目录（如 `frontend/apps/admin/src/app/(dashboard)/`）
   - 每个子目录 = 一个前端模块
4. **合并为模块列表**：将后端和前端按名称匹配，生成统一的模块列表
5. **确定处理顺序**：
   - 读取 CLAUDE.md 判断哪些是基础设施模块（认证、租户、权限）→ 优先
   - 核心业务模块次之
   - 分析/集成/辅助模块最后
6. **生成 `docs/superpowers/module-iterate-state.json`**，格式：
   ```json
   {
     "version": "1.0",
     "project": "项目名",
     "last_updated": "YYYY-MM-DD",
     "current_module_index": 0,
     "modules": [
       {
         "id": "模块id",
         "name": "模块中文名",
         "status": "pending",
         "priority": 1,
         "api_files": [],
         "service_files": [],
         "model_files": [],
         "schema_files": [],
         "frontend_files": [],
         "test_files": [],
         "review_file": null,
         "plan_file": null,
         "findings_count": {"critical": 0, "high": 0, "medium": 0, "low": 0},
         "tasks_total": 0,
         "tasks_completed": 0,
         "bead_id": null,
         "branch": null,
         "last_reviewed": null,
         "last_executed": null
       }
     ]
   }
   ```
7. **Git 提交**：`git commit -m "chore: auto-discover project modules for iteration"`

**如果 state.json 已存在**，跳过此步。

### Step 1: 读取状态

读取 `docs/superpowers/module-iterate-state.json`，找到第一个 `status != "completed"` 的模块。

如果所有模块都是 `completed`：
- 输出 "🎉 所有模块迭代完成！"
- 不再调用 ScheduleWakeup，/loop 自然结束

### Step 2: 阶段调度（必须显式输出）

**这一步不可跳过。** 你必须输出以下信息后才能继续：

```
## Step 2: 阶段调度
- **模块**: {id} ({name})
- **当前状态**: {status}
- **目标阶段**: {根据下表确定}
- **已有 Bead**: {bead_id 或 "需要创建"}
```

根据模块的 `status` 字段决定执行哪个阶段：

| 当前状态 | 执行动作 | 完成后状态 |
|---------|---------|-----------|
| `pending` | Review 阶段 | `reviewed` |
| `reviewed` | Plan 阶段 | `planned` |
| `planned` | Execute 阶段（首轮，3 任务） | `executing` |
| `executing` | Execute 阶段（续接，3 任务） | `executing` 或 `completed` |

输出调度信息后，跳转到对应的阶段（Step 3/4/5）。

### Step 3: Review 阶段

当 `status == "pending"` 时执行：

1. **创建 bead**（如尚未创建）：
   ```bash
   bd add "Module Review: {模块名}" --dolt-auto-commit off
   ```
   将返回的 bead ID 写入 state.json。

2. **调用 module-review skill**：
   使用 Skill 工具调用 `module-review`，传入 `args: "{module_id}"`。
   Skill 内部将启动最多 5 个并行 Agent（通过 Agent 工具）分别执行 5 个审查流。

3. **保存 review 结果**：
   使用当天日期，将输出写入 `docs/superpowers/reviews/{YYYY-MM-DD}-{module-id}.md`

4. **更新 state.json**：
   - `status = "reviewed"`
   - `review_file = "docs/superpowers/reviews/{YYYY-MM-DD}-{module-id}.md"`
   - `findings_count` = 从 review 结果中提取各级别数量

5. **Git 提交**：
   ```bash
   git add docs/superpowers/reviews/ docs/superpowers/module-iterate-state.json
   git commit -m "docs(review): add {module-id} module review"
   ```

### Step 4: Plan 阶段

当 `status == "reviewed"` 时执行：

1. **读取 review 文件**，提取所有 Critical 和 High 级别的发现。

2. **生成改进计划**：
   - 参考项目中已有的 plan 文件格式（如 `docs/superpowers/plans/` 下最近的一个）
   - 每个计划包含：目标、架构说明、文件变更映射、编号任务（checkbox 格式）
   - 按优先级排序：Critical → High → Medium
   - 每个任务包含：具体步骤、受影响文件、验证方法

3. **保存计划**：
   使用当天日期，写入 `docs/superpowers/plans/{YYYY-MM-DD}-{module-id}-hardening.md`

4. **更新 state.json**：
   - `status = "planned"`
   - `plan_file = "docs/superpowers/plans/{YYYY-MM-DD}-{module-id}-hardening.md"`
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
   - 逐个执行任务直到全部完成
   - 每个任务后提交并更新 state.json
   - 运行测试验证

3. **判断完成状态**：
   - 如 `tasks_completed == tasks_total`：运行全量测试，通过则 `status = "completed"`，然后**继续处理下一个模块**
   - 否则保持 `status = "executing"`，继续执行剩余任务
   - 任务被延期（DEFERRED）也算 completed，计入 tasks_completed

4. **完成时**：
   - 关闭 bead：`bd done {bead_id} --dolt-auto-commit off --reason "模块迭代完成"`
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

## 节奏控制

**核心原则：全自动，不等待人工。连续执行直到全部模块完成。**

```
一次 /loop 迭代：
Step 0(发现) → Step 2(调度) → Step 3(Review) → Step 4(Plan) → Step 5(Execute) → 下一个模块 → ...
```

**全自动策略**：
- **测试失败** → 自动修复（最多 3 次）→ 仍失败则 revert + 记录到 `docs/superpowers/deferred-decisions.md`，继续下一个任务
- **需要人决策的任务**（迁移、接口签名变更）→ 跳过，记录到延期文件，继续下一个任务
- **全量测试失败** → 同样自动修复 → 修复不了则记录并继续

**唯一真正停止的情况**：
- 所有模块完成 → 输出完成信息和延期汇总，不设 ScheduleWakeup

**不要**：
- 不要在 Review → Plan 之间断开
- 不要人为限制每轮的任务数量
- 不要因为测试失败就停止整个流程
- 不要主动设置 ScheduleWakeup

**遵循 CLAUDE.md 中的所有编码规范。**
