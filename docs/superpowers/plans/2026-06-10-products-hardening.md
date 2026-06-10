# 产品管理模块改进计划

**模块ID**: products
**基于 Review**: `.claude/plans/findings_product_management.md`（历史 review）
**总任务数**: 6

> 注：原 review 中发现的 P0 删除功能缺失、服务层 HTTPException 问题已在之前的迭代中修复。本计划聚焦剩余的 High/Medium 级别问题。

## High 修复

- [ ] H-1: Products API 添加 RBAC 权限检查 — `backend/app/api/v1/products.py` — 所有端点缺少 `require_permissions` 装饰器/依赖，需按 CRUD 操作添加对应权限（brand:read/write/delete, product:read/write/delete, sku:read/write/delete, batch:read/write/delete）
- [ ] H-2: SKU code 唯一性检查缺少 tenant_id 过滤 — `backend/app/services/product.py:297` — `select(SKU).where(SKU.product_id == product_id, SKU.code == code)` 缺少 `SKU.tenant_id == tenant_id`，虽然 product_id 间接关联 tenant，但应遵循防御性编程直接过滤

## Medium 改进

- [ ] M-1: 添加数据库级唯一约束 — `backend/alembic/` 新迁移 — Brand.name + tenant_id、SKU.code + product_id + tenant_id、ProductionBatch.batch_code + tenant_id 的复合唯一约束
- [ ] M-2: 前端产品工作台表格添加分页 — `frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx` — Assets/SKUs/Batches/Pages 四个 Table 组件从 `pagination={false}` 改为启用分页
- [ ] M-3: 统一产品资料 API 路由风格 — `backend/app/api/v1/products.py` — 创建用嵌套路由 `/products/{id}/assets`，更新用扁平路由 `/product-assets/{id}`，应统一风格（或添加注释说明设计意图）

## Low 改进

- [ ] L-1: 前端产品工作台拆分为独立 Tab 组件 — `frontend/apps/admin/src/app/(dashboard)/products/_components/` — 1040 行单文件拆分为 ProductProfileTab、ProductSkusTab、ProductBatchesTab、ProductAssetsTab、ProductPagesTab（可选，低优先级）

## 延期项（需人工确认）

- [DEFERRED] 服务层函数参数过多（7+ 个）— 影响面大，建议独立重构迭代
- [DEFERRED] 通用分页逻辑提取 `paginate_query()` — 可在后续统一优化中处理
