---
status: active
last_verified: 2026-07-26
accuracy: high
---

# Issue tracker：Beads

本仓库的工程任务、缺陷、规格和后续事项使用 Beads 管理。数据保存在仓库的 `.beads/` 中，通过 `bd` CLI 操作。

## 常用操作

- 创建：`bd create "标题" --type task --description "..." --json`
- 查看：`bd show <id> --json`
- 列表：`bd list --json`
- 查看可执行任务：`bd ready --json`
- 认领：`bd update <id> --claim --json`
- 更新状态：`bd update <id> --status <status> --json`
- 添加或删除标签：`bd update <id> --add-label <label> --json` / `bd update <id> --remove-label <label> --json`
- 关闭：`bd close <id> --reason "..." --json`
- 添加阻塞关系：`bd dep add <blocked-id> <blocker-id>`

优先使用 `--json`，让工程 skill 能稳定读取结果。

## Skill 语义

当工程 skill 要求“发布到 issue tracker”时，创建 Beads issue。

当工程 skill 要求“读取 ticket”时，运行：

```bash
bd show <id> --json
```

实现过程中发现独立后续工作时，创建新 issue，并用 `discovered-from` 关联来源：

```bash
bd create "后续事项" --deps discovered-from:<source-id> --json
```

## Wayfinding

- Map：创建一个 `epic`，标签为 `wayfinder:map`。
- Child ticket：使用 `--parent <map-id>` 创建子任务，并添加 `wayfinder:<type>` 标签。
- 阻塞关系：`bd dep add <blocked-id> <blocker-id>`。
- Frontier：`bd ready --parent <map-id> --unassigned --json`。
- Claim：`bd update <id> --claim --json`。
- Resolve：将结论写入 notes，关闭 ticket，并把必要的上下文指针追加到 map。

## Dolt 同步

需要形成本地 tracker checkpoint 时运行：

```bash
bd dolt commit -m "描述"
```

只有用户明确要求远端交接、push 或发布时，才执行 `bd dolt pull` / `bd dolt push`。不得擅自 force push。
