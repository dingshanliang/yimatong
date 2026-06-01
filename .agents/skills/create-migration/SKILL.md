---
name: create-migration
description: Generate Alembic migration with tenant_id, UUID v7, RLS, and reversibility checks for the yimatong project
disable-model-invocation: true
---

# 创建 Alembic 迁移

## 参数

用户需提供：要做什么变更（新建表 / 加列 / 改列 / 数据迁移等）。

## 执行步骤

### 1. 生成迁移文件

```bash
cd backend && source .venv/bin/activate
alembic revision --autogenerate -m "<描述>"
```

如果 autogenerate 不适用（数据迁移、RLS 策略等），手动创建：

```bash
alembic revision -m "<描述>"
```

### 2. 编写 upgrade()

根据变更类型使用对应模板：

#### 新建业务表模板

```python
op.create_table(
    'table_name',
    sa.Column('id', sa.Uuid(), nullable=False),           # UUID v7 主键
    sa.Column('tenant_id', sa.Uuid(), nullable=False),     # 必须：租户隔离
    # ... 业务列 ...
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    sa.PrimaryKeyConstraint('id'),
)
op.create_index(op.f('ix_table_name_tenant_id'), 'table_name', ['tenant_id'], unique=False)
```

#### 加列模板

```python
op.add_column('table_name', sa.Column('column_name', sa.String(length=100), nullable=True))
```

#### RLS 策略模板（新表必须）

```python
op.execute("ALTER TABLE table_name ENABLE ROW LEVEL SECURITY")
op.execute("""
    CREATE POLICY tenant_isolation ON table_name
    USING (tenant_id = current_tenant_id())
""")
```

### 3. 编写 downgrade()

每个 upgrade 操作必须有对应的 reverse：

| upgrade | downgrade |
|---------|-----------|
| `create_table` | `drop_table` |
| `add_column` | `drop_column` |
| `create_index` | `drop_index` |
| `ENABLE ROW LEVEL SECURITY` | `DISABLE ROW LEVEL SECURITY` |
| `CREATE POLICY` | `DROP POLICY` |

### 4. 自检清单

生成后逐项验证：

- [ ] `tenant_id` 列：新业务表是否包含（NOT NULL + index）
- [ ] RLS：新业务表是否启用 RLS 策略
- [ ] 主键：是否使用 `sa.Uuid()`（非 Integer）
- [ ] downgrade：每个 upgrade 操作是否有 reverse
- [ ] 大表索引：是否使用 `CONCURRENTLY`（scan_events 等分区表）
- [ ] NOT NULL 新列：是否提供 safe default 或分步迁移
- [ ] revision chain：`down_revision` 是否指向最新 revision

### 5. 验证

```bash
# 检查语法
alembic upgrade head --sql  # dry run
# 实际执行（开发环境）
alembic upgrade head
# 验证可回滚
alembic downgrade -1
alembic upgrade head
```
