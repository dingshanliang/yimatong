# 延期决策汇总

所有需要人工确认的决策记录在此。

---

## Tenants 模块

### DEFERRED-1: Tenant 表索引 + AgencyAuthorization 复合索引 (Task 6)
- **原因**: 需要人工确认 Alembic 迁移文件。索引包括 Tenant.status、Tenant.plan、AgencyAuthorization(tenant_id, agency_id) 复合索引
- **涉及文件**: `backend/app/models/tenant.py`、新的 alembic 迁移
- **优先级**: H-2 / H-9
- **日期**: 2026-06-10

### DEFERRED-2: 租户暂停/恢复功能 (H-5)
- **原因**: 需要产品确认 API 设计（暂停状态的业务影响、恢复流程）
- **优先级**: H-5
- **日期**: 2026-06-10

### DEFERRED-3: LIKE 搜索优化 (M-4)
- **原因**: pg_trgm 或全文搜索是全局架构决策，影响所有模块
- **优先级**: M-4
- **日期**: 2026-06-10

### DEFERRED-4: get_tenant 缓存 (M-6)
- **原因**: 需要设计缓存失效策略（Redis 缓存 key 设计、TTL、主动失效触发点）
- **优先级**: M-6
- **日期**: 2026-06-10

### DEFERRED-5: brands 页面概念混淆 (M-8)
- **原因**: 需要产品确认命名规范（brands vs tenants 在前端的使用）
- **优先级**: M-8
- **日期**: 2026-06-10
