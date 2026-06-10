# 行业模板模块改进计划

**模块ID**: industry-templates
**基于 Review**: `docs/superpowers/reviews/2026-06-10-industry-templates.md`
**总任务数**: 3

## High 修复

- [ ] H-1: 修复 apply 端点字段名错误 — `backend/app/api/v1/industry_templates.py:65` — `version_number=1` 改为 `version=1`
- [ ] H-2: 所有端点添加 RBAC + 认证 — `backend/app/api/v1/industry_templates.py` — 列表/详情添加 `require_role("admin", "operator")` + `get_current_tenant`，apply 添加 `require_role("admin")` + `get_current_account_id`
- [ ] H-3: apply 端点重构为调用服务层 — `backend/app/api/v1/industry_templates.py:44-69` — 用 `create_page_template` + `create_page_version` 替代直接模型操作，添加 Pydantic Schema 替代 raw dict

## 延期项（需人工确认）

- [DEFERRED] 模板 ID 稳定化（slug 替代索引）— 需评估前端影响
- [DEFERRED] 与 page_templates clone 功能去重 — 需人工决策
