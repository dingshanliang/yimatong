# Deep modules

每个 `packages/<name>/` 都是一个深模块：调用方通过小而稳定的 interface 获得更多行为，implementation 则集中隐藏在包内。

```text
packages/
  <name>/
    index.ts       # entry point，可公开导入
    client.ts      # 可选的另一个小 entry point
    lib/           # 私有 implementation
    tests/         # 通过 entry points 验证行为
```

**Entry-point seam。** 只能通过包根目录的 entry points 导入模块。任何子目录内容都是私有 implementation，应用和其他包不得 deep import。

**包内自由。** 同一包的 implementation 可以自由互相导入，因此复杂度保持在模块内部，维护修改具有 locality。

**测试走 interface。** `tests/` 中的测试与普通调用方跨越相同 seam，只能导入包根 entry points；测试可以使用自身 `tests/` 下的 fixture，但不能绕过 interface 访问任何包的 implementation。

**禁止循环。** dependency graph 中不允许循环依赖。运行 `pnpm lint:boundaries` 检查这些规则，或运行 `pnpm check` 同时执行类型检查。

不要用一个巨型 barrel file 重导出整棵子目录。需要扩展 interface 时，优先增加多个小而明确的根 entry points，例如 `client.ts`、`server.ts`。

`example/` 是可复制或删除的 starter template。
