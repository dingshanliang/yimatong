# 发现与决策

> 历史记录：租户管理模块改进发现已归档至 `progress.md`（2026-06-07 ~ 2026-06-08）

---

## 需求
系统修复产品管理模块的 P0 缺陷 + 补齐缺失功能，使其达到生产级完整度。

## 审查统计（产品管理模块，2026-06-08）

| 维度 | 评价 | 🔴 严重 | 🟡 中等 | 🟢 建议 |
|------|------|---------|---------|---------|
| 功能完整度 | ⭐⭐ | 2 | 3 | 3 |
| 代码质量 | ⭐⭐⭐ | 0 | 3 | 3 |
| 数据模型 | ⭐⭐⭐ | 0 | 2 | 2 |
| API 设计 | ⭐⭐⭐ | 0 | 2 | 2 |
| 安全性 | ⭐⭐⭐ | 1 | 1 | 1 |
| 测试覆盖 | ⭐⭐⭐ | 0 | 2 | 2 |
| 前端 UX | ⭐⭐⭐ | 0 | 2 | 2 |

## 关键发现

### 🔴 P0 严重问题
1. **品牌删除端点只检查不执行删除** — `delete_brand_endpoint` 检查有关联产品后抛 409，但没有 else 分支执行删除；没有关联产品时直接返回 204，未执行任何删除逻辑
2. **产品/SKU/生产批次删除端点完全缺失** — 用户无法删除误创建的数据

### 🟡 功能缺失
3. **CSV 批量导入前端界面缺失** — 后端 API `/production-batches/import-csv` 已存在，前端无导入按钮
4. **产品资料删除功能缺失** — 前端资产表格无删除按钮，后端 `delete_product_asset` 有实现但前端未调用
5. **前端工作台分页缺失** — SKU/批次/资料/扫码页均 `pagination={false}`，大数据量性能差
6. **产品状态自动流转缺失** — `draft` 状态无发布逻辑，`expired` 需手动设置

### 🟡 代码质量问题
7. **服务层直接抛 HTTPException** — `services/product.py` 多处（品牌名重复、SKU code 重复、批次号重复），违反分层原则
8. **函数参数过多** — `update_product`/`create_product` 等 7+ 个参数，应使用 dataclass/Pydantic 封装
9. **重复代码严重** — 每个 `list_*` 重复分页逻辑，每个 `update_*` 重复字段赋值逻辑
10. **SKU code 唯一性检查缺 tenant_id** — `select(SKU).where(SKU.product_id == product_id, SKU.code == code)` 未过滤 tenant

### 🟡 数据模型问题
11. **缺少数据库级唯一约束** — Brand.name、SKU.code、ProductionBatch.batch_code 的唯一性仅在应用层检查
12. **缺少软删除** — 关键业务数据（Product、Batch）物理删除，误删不可恢复
13. **关系加载策略** — 模型层 `lazy="selectin"` 与查询层手动 `selectinload` 可能冲突

### 🟡 API 设计不一致
14. **产品资料路由不一致** — 创建用嵌套路由 `/products/{id}/assets`，更新用扁平路由 `/product-assets/{id}`
15. **缺少 ProductDetailRead** — Brand 有 `BrandDetailRead`（含统计），Product 无对应详情聚合响应

### 🟡 前端问题
16. **产品工作台单文件过大** — `[id]/page.tsx` 894 行，承载 5 个 Tab 的所有逻辑
17. **全量刷新策略粗糙** — 所有操作后调用 `load()` 刷新全部数据

### ✅ 已实现的优点
18. **数据模型设计合理** — 5 级关联（Brand→Product→SKU→Batch→Asset），租户隔离到位
19. **测试覆盖较全面** — 5 个测试文件覆盖主要 CRUD 和关联查询
20. **前端 UX 设计友好** — 资料完整度计算、下一步操作引导、AI 识别入口

## 技术决策
| 决策 | 理由 |
|------|------|
| 分 3 个阶段修复 | P0 删除功能优先，P1 代码质量次之，P2 体验优化最后 |
| 服务层引入领域异常 | 替代直接抛 HTTPException，保持分层清晰 |
| 前端工作台拆分组件 | 单文件 894 行需拆分为独立 Tab 组件 |
| 后端不引入软删除（本次） | 影响面大，需独立评估；本次仅补齐缺失的硬删除 |

## 资源
- PRD：`docs/01_product/PRD.md`
- 数据模型：`docs/02_tech/DATA_MODEL.md`
- 技术架构：`docs/02_tech/ARCHITECTURE.md`
