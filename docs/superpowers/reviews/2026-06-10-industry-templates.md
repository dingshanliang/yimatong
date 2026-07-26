# 行业模板 Module Review

**Date:** 2026-06-10
**Project:** yimatong
**Reviewer:** Automated (module-iterate)

## Review Summary
| Dimension | Rating | Critical | High | Medium | Low |
|-----------|--------|----------|------|--------|-----|
| 功能完整性 | B | 0 | 1 | 0 | 1 |
| 用户体验 | A | 0 | 0 | 0 | 0 |
| 代码质量 | D | 0 | 2 | 1 | 0 |
| 安全性 | C | 0 | 1 | 1 | 0 |
| 测试覆盖 | N/A | 0 | 0 | 0 | 0 |
| API规范 | B | 0 | 0 | 1 | 0 |
| 性能 | A | 0 | 0 | 0 | 0 |
| 数据模型 | N/A | 0 | 0 | 0 | 0 |
| 前后端一致性 | N/A | 0 | 0 | 0 | 0 |
| 架构 | C | 0 | 1 | 1 | 0 |
| **TOTAL** | — | **0** | **5** | **4** | **1** |

## High Findings

### H-1: apply 端点使用错误的字段名 version_number（应为 version）
- **Dimension:** 功能完整性
- **File:** `backend/app/api/v1/industry_templates.py:65`
- **Description:** `PageVersion(... version_number=1 ...)` 但模型字段名是 `version`，调用会报 `TypeError`。这意味着行业模板应用功能**完全不可用**。
- **Fix:** 改为 `version=1`。

### H-2: 所有端点缺少 RBAC 权限检查
- **Dimension:** 安全性
- **File:** `backend/app/api/v1/industry_templates.py` (3 个端点)
- **Description:** `list_industry_templates` 和 `get_industry_template` 无需认证（列表/详情应添加认证），`apply_industry_template` 有 tenant_id 但缺少 `require_role`。
- **Fix:** 列表/详情添加 `require_role("admin", "operator")`，apply 添加 `require_role("admin")`。

### H-3: API 层直接操作模型而非调用服务层
- **Dimension:** 架构
- **File:** `backend/app/api/v1/industry_templates.py:44-69`
- **Description:** `apply_industry_template` 直接 import 模型并操作数据库，绕过了服务层的 `create_page_template` 和 `create_page_version`。这导致缺少 DSL 校验、HTML 消毒、配额检查等业务逻辑。
- **Fix:** 重构为调用 `services/page.py` 中的 `create_page_template` 和 `create_page_version`。

### H-4: apply 端点使用原始 dict 而非 Pydantic Schema
- **Dimension:** 代码质量
- **File:** `backend/app/api/v1/industry_templates.py:33`
- **Description:** `body: dict | None = None` 缺少输入验证，`body.get("product_id")` 无类型检查。
- **Fix:** 定义 `IndustryTemplateApplyRequest(BaseModel)` 并使用 Pydantic 验证。

### H-5: apply 端点缺少 created_by 字段
- **Dimension:** 代码质量
- **File:** `backend/app/api/v1/industry_templates.py:60-69`
- **Description:** `PageVersion` 的 `created_by` 是 NOT NULL 字段，但 apply 端点未传入该字段，会导致数据库插入失败。
- **Fix:** 添加 `account_id: uuid.UUID = Depends(get_current_account_id)` 并传入 `created_by=account_id`。

## Medium Findings

### M-1: 两次独立的 commit 而非一次事务
- **Dimension:** 代码质量
- **File:** `backend/app/api/v1/industry_templates.py:53-69`
- **Description:** 先 commit PageTemplate 再 commit PageVersion，如果第二次失败，会产生无版本的模板。
- **Fix:** 使用一次 `db.flush()` 代替两次 `db.commit()`。

### M-2: 列表/详情端点无需认证即可访问
- **Dimension:** API规范
- **File:** `backend/app/api/v1/industry_templates.py:16-28`
- **Description:** `list_industry_templates` 和 `get_industry_template` 没有 `get_current_tenant` 依赖，任何人都可访问。行业模板库是配置数据，应该要求认证。
- **Fix:** 添加 `tenant_id: uuid.UUID = Depends(get_current_tenant)` 和 RBAC。

### M-3: 模板 ID 使用整数索引而非稳定标识符
- **Dimension:** API规范
- **File:** `backend/app/api/v1/industry_templates.py:25`
- **Description:** 使用数组索引作为模板 ID，如果模板顺序变化会导致客户端引用失效。
- **Fix:** 为每个模板添加稳定的 `slug` 或 `id` 字段。

### M-4: 未验证 product_id 是否属于当前租户
- **Dimension:** 安全性
- **File:** `backend/app/api/v1/industry_templates.py:42-50`
- **Description:** `product_id` 直接使用未验证是否属于当前租户，可能导致跨租户关联。
- **Fix:** 重构为使用服务层后会自动包含验证。

## Low Findings

### L-1: 与 page_templates 中的行业模板功能重复
- **Dimension:** 功能完整性
- **File:** `backend/app/api/v1/page_templates.py:67-94`
- **Description:** page_templates 中已有 `clone_industry_template` 端点做同样的事，造成功能重复。
- **Fix:** 考虑移除此模块的 apply 端点，统一使用 page_templates 中的 clone 端点。
