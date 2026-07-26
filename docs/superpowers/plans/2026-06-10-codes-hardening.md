# 码管理模块改进计划

**模块ID**: codes
**基于 Review**: `docs/superpowers/reviews/2026-06-10-codes.md`
**总任务数**: 8

## High 修复

- [ ] H-1: mark-printing 和 mark-delivered 端点添加 RBAC — `backend/app/api/v1/code_batches.py:259-288` — 两个状态变更端点缺少 `require_permission("code:manage")` 依赖，添加 `_ = Depends(require_permission("code:manage"))`
- [ ] H-2: 服务层替换 HTTPException 为领域异常 — `backend/app/services/code.py:410,430` — `revoke_code_item` 和 `bind_code_item` 直接抛 `HTTPException(404)`，替换为 `NotFoundError`，移除 `from fastapi import HTTPException` 导入
- [ ] H-3: code_batches 添加唯一约束 — `backend/app/models/code.py:69` + 新迁移 — 将 `Index("ix_code_batches_tenant_batch", "tenant_id", "batch_code")` 改为 `UniqueConstraint("tenant_id", "batch_code", name="uq_code_batches_tenant_batch_code")`，生成对应迁移
- [ ] H-4: 码生成数量添加上限限制 — `backend/app/api/v1/code_batches.py:42`（CodeBatchCreateRequest）— `quantity` 字段添加 `le=100000` 限制
- [ ] H-5: 并发状态转换添加行级锁 — `backend/app/services/code.py` — 在 activate/freeze/void/mark_printing/mark_delivered 函数中，将批次查询改为 `select(CodeBatch).where(...).with_for_update()` 防止并发冲突

## Medium 改进

- [ ] M-1: 提取重复的缓存清理逻辑 — `backend/app/services/code.py` — 将 revoke/freeze/void 中的缓存清理代码提取为 `_invalidate_batch_cache(db, batch)` 辅助函数
- [ ] M-2: API 层配额检查下沉至服务层 — `backend/app/api/v1/code_batches.py:99-123` → `backend/app/services/code.py:create_code_batch` — 将 `check_quota_for_tenant` 调用移入服务层
- [ ] M-3: Excel 导入添加文件大小限制 — `backend/app/services/import_service.py` — 在 `parse_and_validate` 入口添加 `len(file_content) > 10MB` 检查

## 延期项（需人工确认）

- [DEFERRED] 导出接口 POST→GET 改造 — 影响 API 兼容性，需评估前端改动范围
- [DEFERRED] 码项管理前端页面 — 功能增强，非安全/质量问题
- [DEFERRED] 批量操作功能 — 功能增强，需独立排期
- [DEFERRED] 码项过期机制 — 需业务确认是否需要
