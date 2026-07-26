# 页面模板 Module Review

**Date:** 2026-06-10
**Project:** yimatong
**Reviewer:** Automated (module-iterate)

## Review Summary
| Dimension | Rating | Critical | High | Medium | Low |
|-----------|--------|----------|------|--------|-----|
| 功能完整性 | A | 0 | 0 | 1 | 0 |
| 用户体验 | A | 0 | 0 | 2 | 1 |
| 代码质量 | D | 0 | 2 | 1 | 0 |
| 安全性 | D | 0 | 1 | 1 | 0 |
| 测试覆盖 | B | 0 | 0 | 1 | 0 |
| API规范 | B | 0 | 0 | 2 | 0 |
| 性能 | B | 0 | 1 | 2 | 0 |
| 数据模型 | B | 0 | 1 | 1 | 0 |
| 前后端一致性 | A | 0 | 0 | 1 | 0 |
| 架构 | B | 0 | 0 | 1 | 0 |
| **TOTAL** | — | **0** | **5** | **13** | **1** |

## High Findings

### H-1: 所有端点缺少 RBAC 权限检查
- **Dimension:** 安全性 + 代码质量
- **File:** `backend/app/api/v1/page_templates.py` (全部约 15 个端点)
- **Description:** 所有 `/api/v1/page-templates/` 和 `/api/v1/page-versions/` 端点都没有 `require_role()` 或 `require_permission()` 依赖。任何有认证 token 的用户都可以访问所有模板功能。
- **Fix:** 读操作添加 `require_role("admin", "operator")`，写/删操作添加 `require_role("admin")`。

### H-2: PageTemplate 和 PageVersion 使用 uuid4 而非 uuid7
- **Dimension:** 代码质量
- **File:** `backend/app/models/page.py:32,55`
- **Description:** `id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)` 使用 `uuid4`，违反项目规范要求 UUID v7（时间排序）。
- **Fix:** 改为 `from uuid6 import uuid7`，`default=uuid7`。

### H-3: page_versions.page_template_id 缺少外键约束
- **Dimension:** 数据模型
- **File:** `backend/app/models/page.py:57`
- **Description:** `page_template_id` 字段只有 `index=True` 但缺少 `ForeignKey("page_templates.id")`，数据库层面无法保证引用完整性。
- **Fix:** 添加 `ForeignKey("page_templates.id")` 并生成对应迁移。

### H-4: 版本号生成存在竞态条件
- **Dimension:** 性能
- **File:** `backend/app/services/page.py:255-260`
- **Description:** `create_page_version` 通过查询 `max(version)` 生成新版本号，并发请求可能导致重复版本号。虽有 `UniqueConstraint` 兜底但会报错。
- **Fix:** 使用 `SELECT MAX(version) ... FOR UPDATE` 锁定查询，或在异常时重试。

### H-5: 缓存键未包含版本号
- **Dimension:** 性能
- **File:** `backend/app/services/page_render.py:37`
- **Description:** 缓存键 `page:{tenant_id}:{template_id}` 不含版本信息，版本发布/回滚后缓存可能返回旧内容。虽然有 `invalidate_cache` 机制，但键设计不够健壮。
- **Fix:** 缓存键改为 `page:{tenant_id}:{template_id}:published`，并在发布/回滚/归档时主动失效。

## Medium Findings (关键项)

### M-1: Schema 定义在 API 文件中而非 schemas/ 目录
- **Dimension:** API规范
- **File:** `backend/app/api/v1/page_templates.py:36-60`
- **Description:** `PageTemplateCreateRequest`、`PageVersionCreateRequest` 等 Schema 直接定义在 API 文件中，应移至 `schemas/` 目录。
- **Fix:** 提取到 `backend/app/schemas/page_template.py`。

### M-2: config_json 无大小限制
- **Dimension:** 安全性
- **File:** `backend/app/api/v1/page_templates.py:53-56` + `backend/app/models/page.py:62`
- **Description:** `config_json` 字段无大小限制，恶意用户可提交超大 JSON 导致数据库压力。
- **Fix:** 在 Pydantic Schema 中添加 `@field_validator` 检查 `len(json.dumps(config_json)) <= 100_000`。

### M-3: 获取模板详情时存在 N+1 查询
- **Dimension:** 性能
- **File:** `backend/app/services/page.py:150-154`
- **Description:** `get_page_template` 中单独查询 `Product.name`，列表页如果有多个模板则产生 N+1。
- **Fix:** 使用 `selectinload` 或在列表查询时 JOIN。

### M-4: 预览面板使用示例数据时缺少提示
- **Dimension:** 用户体验
- **File:** `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/page.tsx:52-76`
- **Description:** 产品数据缺失时预览显示示例数据，但没有明确提示用户。
- **Fix:** 在预览面板添加"正在使用示例数据"提示。

## Low Findings

### L-1: 模块配置表单缺少保存反馈
- **Dimension:** 用户体验
- **File:** `frontend/apps/admin/src/app/(dashboard)/pages/[id]/edit/components/ModuleConfigForms.tsx:69-87`
- **Description:** 模块配置修改后没有自动保存提示。
- **Fix:** 添加"已保存"状态提示或自动保存机制。

## Technical Decisions
| 决策 | 理由 |
|------|------|
| RBAC 使用 require_role 而非 require_permission | 与已完成的 organizations/tenants 模块保持一致 |
| 缓存键不使用版本号而使用 published 后缀 | 避免缓存膨胀，公开页面只需要最新发布版 |
