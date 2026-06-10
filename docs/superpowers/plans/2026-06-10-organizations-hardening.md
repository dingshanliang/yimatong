# Organizations (组织管理) 改进计划

**模块ID**: organizations
**基于 Review**: docs/superpowers/reviews/2026-06-10-organizations.md
**总任务数**: 8

## Critical 修复

- [ ] C-1: 添加 RBAC 权限控制 — `backend/app/api/v1/organizations.py` — 为全部 8 个端点添加 `Depends(require_role("admin"))` 或 `require_permission`。CRUD 操作至少要求 admin 角色，列表可允许 operator。

- [ ] C-2: 修复 LIKE 查询 SQL 注入 — `backend/app/services/organization.py` — 导入 `escape_like_pattern`，搜索查询使用 `ilike(f"%{escaped}%", escape="\\")`，与 tenants 模块保持一致。

- [ ] C-3: 初始密码仅在创建时返回 — `backend/app/schemas/account.py`, `backend/app/api/v1/organizations.py` — 从 `AccountRead` 移除 `initial_password`，创建单独的 `AccountCreateResponse` schema。

- [ ] C-4: AccountCreate 添加密码强度验证 — `backend/app/schemas/account.py` — 添加 `@field_validator("password")` 调用 `validate_password_strength()`，与 TenantCreate 一致。

- [ ] C-5: AccountUpdate 添加 organization_id 字段 — `backend/app/schemas/account.py`, `backend/app/services/organization.py` — schema 添加 `organization_id: uuid.UUID | None = None`，service 层处理组织变更（含租户归属验证）。

## High 修复

- [ ] H-1: Service 层移除 HTTPException — `backend/app/services/organization.py` — 7 处 `raise HTTPException` 改为 `raise ValueError`，API 层 try/except 转换。

- [ ] H-2: 添加 DELETE /accounts 端点 — `backend/app/api/v1/organizations.py` — 新增软删除端点，检查关联数据后标记为禁用。

- [ ] H-3: 修复 N+1 查询 + 合并 count 查询 — `backend/app/models/tenant.py`, `backend/app/services/organization.py` — Organization.accounts 改为 `lazy="noload"`，将 account_count 合并到组织列表查询（子查询）。

## 延期项（需人工确认）

- [DEFERRED] H-5: API 层 DB 查询移入 Service — 重构 list_accounts 返回含 org_name，影响面大
- [DEFERRED] H-6: 前端组织搜索改服务端 — 前端改动，非后端 hardening 范围
- [DEFERRED] H-7: 前端加载状态 — 前端改动
- [DEFERRED] H-8/H-9: 安全边界测试 — 单独任务，待核心修复完成后补充
- [DEFERRED] M-2: 循环引用改递归 CTE — 深度优化，当前实现可工作
- [DEFERRED] M-3: 组织软删除 — 需要数据模型变更和迁移
- [DEFERRED] M-4: 拆分 organizations.py — 大重构，单独迭代
