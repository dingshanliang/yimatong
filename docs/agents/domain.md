---
status: active
last_verified: 2026-07-26
accuracy: high
---

# Domain docs

本仓库采用 single-context 领域文档布局。

## 探索前读取

- 如果根目录存在 `CONTEXT.md`，先读取其中的领域术语和边界。
- 阅读 `docs/adr/` 中与当前工作相关的架构决策。
- 结合 `docs/01_product/` 和 `docs/02_tech/` 中相关的现有产品与技术文档。

如果 `CONTEXT.md` 或 `docs/adr/` 尚不存在，继续工作，不提示缺失，也不预先创建空文件。只有在真正形成新术语或架构决策时，才通过相应 domain-modeling 工作流创建。

## 术语规则

输出中的领域概念、issue 标题、测试名和重构提案应使用 `CONTEXT.md` 定义的术语，不应随意创造同义词。

如果所需概念尚未定义，应先判断：

- 当前说法是否偏离了项目已有语言；
- 是否确实存在需要 domain-modeling 解决的领域缺口。

## ADR 冲突

如果提案与现有 ADR 冲突，必须明确指出冲突及重新评估的理由，不得静默覆盖已有决策。
