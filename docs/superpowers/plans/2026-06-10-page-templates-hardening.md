# 页面模板模块改进计划

**模块ID**: page-templates
**基于 Review**: `docs/superpowers/reviews/2026-06-10-page-templates.md`
**总任务数**: 7

## High 修复

- [ ] H-1: 所有端点添加 RBAC 权限检查 — `backend/app/api/v1/page_templates.py` — 约 15 个端点全部缺少权限检查。读操作添加 `require_role("admin", "operator")`，写/删/发布/归档添加 `require_role("admin")`，预览接口保持 `require_role("admin", "operator")`
- [ ] H-2: 主键从 uuid4 改为 uuid7 — `backend/app/models/page.py:32,55` — `default=uuid.uuid4` 改为 `default=uuid7`，添加 `from uuid6 import uuid7` 导入
- [ ] H-3: page_versions 添加外键约束 — `backend/app/models/page.py:57` + 新迁移 — `page_template_id` 添加 `ForeignKey("page_templates.id")`
- [ ] H-4: 版本号生成添加行级锁 — `backend/app/services/page.py:255-260` — 将 `select(func.max(...))` 改为 `.with_for_update()` 防止并发重复版本号
- [ ] H-5: 缓存键优化和失效机制完善 — `backend/app/services/page_render.py` — 确保发布/回滚/归档时主动调用 `invalidate_cache`，验证当前实现是否已覆盖

## Medium 改进

- [ ] M-1: config_json 添加大小限制 — `backend/app/api/v1/page_templates.py` — 在 PageVersionCreateRequest/PageVersionUpdateRequest 中添加 validator 检查 JSON 序列化后 <= 100KB
- [ ] M-2: 提取 Schema 到独立文件 — `backend/app/schemas/page_template.py` — 将 API 文件中的 inline Schema 移出

## 延期项（需人工确认）

- [DEFERRED] N+1 查询优化 — 需评估影响面，非紧急
- [DEFERRED] 预览面板示例数据提示 — 前端 UX 改进
- [DEFERRED] 模块配置保存反馈 — 前端 UX 改进
- [DEFERRED] 复合索引优化 — 可在性能优化阶段统一处理
