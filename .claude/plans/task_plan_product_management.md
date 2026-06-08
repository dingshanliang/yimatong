# 任务计划：产品管理模块闭环改进

## 目标
修复产品管理模块所有 P0 缺陷，补齐缺失的删除功能，重构服务层异常处理，优化前端工作台代码结构，使其达到生产级完整度。

## 当前阶段
阶段 1（P0 删除功能补齐）

## 改进总览

| 阶段 | 内容 | 优先级 | 预估工作量 | 依赖 |
|------|------|--------|-----------|------|
| 阶段 1 | P0 删除功能补齐（Brand/Product/SKU/Batch/Asset） | P0 | 0.5 天 | 无 |
| 阶段 2 | 服务层重构（领域异常 + 参数封装 + 重复代码提取） | P1 | 1 天 | 阶段 1 |
| 阶段 3 | 前端工作台拆分 + CSV 导入界面 + 资料删除 | P1 | 1 天 | 阶段 1 |
| 阶段 4 | 测试补全（删除场景 + 边界 case） | P1 | 0.5 天 | 阶段 1-3 |

**总预估：3 天**

---

## 现状发现（影响计划的关键事实）

### ✅ 已实现的（不需要从零开发）
1. **后端删除服务函数** — `delete_product_asset` 已实现，但 API 端点和前端未接入
2. **CSV 导入后端** — `import_batches_csv` + `/production-batches/import-csv` 端点已存在
3. **前端组件复用** — `ProductionBatchFormFields`、`SKUFormFields` 等共享组件已存在
4. **测试基础设施** — 5 个测试文件已有完整 fixture 和客户端设置

### ❌ 缺失的（需要开发）
1. **Brand 真正删除** — 端点只检查不执行
2. **Product 删除端点** — 完全缺失（API + 服务 + 前端）
3. **SKU 删除端点** — 完全缺失
4. **ProductionBatch 删除端点** — 完全缺失
5. **前端资料删除按钮** — 表格无删除操作
6. **前端 CSV 导入界面** — 批次 Tab 无导入按钮

---

## 各阶段详情

### 阶段 1：P0 删除功能补齐
- **状态：** ✅ complete

#### 1.1 Brand 删除修复
- [ ] 修复 `delete_brand_endpoint`：检查无产品后执行 `db.delete(brand)`
- [ ] 品牌删除使用级联或手动清理关联（确认外键约束行为）

**关键文件：**
- 修改：`backend/app/api/v1/products.py`
- 修改：`backend/app/services/product.py`（如需新增 `delete_brand`）

#### 1.2 Product 删除
- [ ] 服务层新增 `delete_product(db, tenant_id, product_id)`
- [ ] API 端点 `DELETE /api/v1/products/{product_id}`
- [ ] 删除前检查：是否有关联 SKU？有关联批次？有关联码批次？
- [ ] 决策点：是否允许级联删除（删除产品时连带删除 SKU/批次）？还是阻止删除直到子资源清空？

**关键文件：**
- 修改：`backend/app/services/product.py`
- 修改：`backend/app/api/v1/products.py`
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/page.tsx`（添加删除按钮）

#### 1.3 SKU 删除
- [ ] 服务层新增 `delete_sku(db, tenant_id, sku_id)`
- [ ] API 端点 `DELETE /api/v1/skus/{sku_id}`
- [ ] 删除前检查：是否有关联批次？

**关键文件：**
- 修改：`backend/app/services/product.py`
- 修改：`backend/app/api/v1/products.py`
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`（SKU Tab 添加删除）

#### 1.4 ProductionBatch 删除
- [ ] 服务层新增 `delete_production_batch(db, tenant_id, batch_id)`
- [ ] API 端点 `DELETE /api/v1/production-batches/{batch_id}`
- [ ] 删除前检查：是否有关联码批次（CodeBatch）？

**关键文件：**
- 修改：`backend/app/services/product.py`
- 修改：`backend/app/api/v1/products.py`
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`（Batch Tab 添加删除）

#### 1.5 ProductAsset 删除（前端接入）
- [ ] 前端资产表格添加删除按钮
- [ ] 调用已有 `DELETE /api/v1/product-assets/{asset_id}` 端点
- [ ] 添加确认对话框

**关键文件：**
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`

**验收标准：**
- 所有删除端点返回 204 或 409（有关联资源时阻止）
- 删除后数据库记录物理删除（本次不做软删除）
- 前端可正常删除所有资源类型
- 现有测试全部通过

---

### 阶段 2：服务层重构
- **状态：** ✅ complete

#### 2.1 引入领域异常类
- [ ] 新建 `backend/app/exceptions.py`（或 `core/exceptions.py`）
- [ ] 定义 `DuplicateResourceError`、`ResourceNotFoundError`、`BusinessValidationError`
- [ ] 在 `main.py` 或 `api/deps.py` 中注册全局异常处理器

**关键文件：**
- 新建：`backend/app/exceptions.py`
- 修改：`backend/app/main.py`

#### 2.2 替换服务层 HTTPException
- [ ] `create_brand`：品牌名重复时抛 `DuplicateResourceError`
- [ ] `update_brand`：同上
- [ ] `create_sku`：SKU code 重复时抛 `DuplicateResourceError`
- [ ] `update_sku`：同上
- [ ] `create_production_batch`：批次号重复时抛 `DuplicateResourceError`
- [ ] `update_production_batch`：同上
- [ ] 日期校验抛 `BusinessValidationError`

**关键文件：**
- 修改：`backend/app/services/product.py`

#### 2.3 API 层适配新异常
- [ ] `products.py` 中捕获 `DuplicateResourceError` → 409
- [ ] 捕获 `ResourceNotFoundError` → 404
- [ ] 捕获 `BusinessValidationError` → 400

**关键文件：**
- 修改：`backend/app/api/v1/products.py`

#### 2.4 提取通用分页逻辑（可选，如时间允许）
- [ ] 新建 `paginate_query()` 辅助函数
- [ ] 替换所有 `list_brands/list_products/list_skus/list_production_batches/list_product_assets`

**验收标准：**
- 服务层不再 import `HTTPException`
- 所有现有测试通过（异常类型改变不影响 HTTP 响应）
- 代码行数减少（重复逻辑提取）

---

### 阶段 3：前端优化
- **状态：** ✅ complete

#### 3.1 产品工作台拆分（可选，如时间允许）
- [ ] 新建 `ProductProfileTab.tsx`
- [ ] 新建 `ProductAssetsTab.tsx`
- [ ] 新建 `ProductSkusTab.tsx`
- [ ] 新建 `ProductBatchesTab.tsx`
- [ ] 新建 `ProductPagesTab.tsx`
- [ ] 主页面只保留 Tab 切换和公共状态

**决策点**：是否在本次迭代中进行拆分？拆分后需全面回归测试。

**关键文件：**
- 新建：`frontend/apps/admin/src/app/(dashboard)/products/_components/*.tsx`
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`

#### 3.2 CSV 批量导入界面
- [ ] 批次 Tab 添加"批量导入"按钮
- [ ] 弹出模态框：选择 SKU + 上传 CSV 文件
- [ ] 展示导入结果（成功数/失败列表）
- [ ] 提供 CSV 模板下载（可选）

**关键文件：**
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`（或拆分后的 Batch Tab）

#### 3.3 产品资料删除
- [ ] 资产表格操作列添加删除按钮
- [ ] 添加 `Popconfirm` 确认
- [ ] 删除后局部刷新资产列表（非全量 `load()`）

**关键文件：**
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`

#### 3.4 产品列表页删除
- [ ] 产品列表操作列添加删除按钮
- [ ] 删除确认后刷新列表

**关键文件：**
- 修改：`frontend/apps/admin/src/app/(dashboard)/products/page.tsx`

**验收标准：**
- 前端可删除产品、SKU、批次、资料
- CSV 导入界面可用
- 所有操作有确认/反馈
- TypeScript 编译通过

---

### 阶段 4：测试补全
- **状态：** ✅ complete

#### 4.1 删除场景测试
- [ ] `test_brands.py`：测试品牌删除（无产品时成功，有产品时 409）
- [ ] `test_products.py`：测试产品删除（无 SKU 时成功，有 SKU 时阻止）
- [ ] `test_skus.py`：测试 SKU 删除（无批次时成功，有批次时阻止）
- [ ] `test_production_batches.py`：测试批次删除

#### 4.2 边界 case 测试
- [ ] 删除不存在的资源 → 404
- [ ] 跨租户删除 → 404 或 403
- [ ] 并发删除同一资源（竞态条件）

#### 4.3 异常处理测试
- [ ] 重复品牌名 → 409
- [ ] 重复 SKU code → 409
- [ ] 重复批次号 → 409
- [ ] 无效日期范围 → 400

**验收标准：**
- 新增测试全部通过
- 原有测试不受影响
- 删除场景覆盖率 100%

---

## 关键依赖关系

```
阶段 1 (删除功能) ──→ 阶段 2 (服务层重构)
       ↓                    ↓
阶段 3 (前端优化) ←────── 阶段 2
       ↓
阶段 4 (测试补全) ←──── 阶段 1,3
```

> 阶段 2 和阶段 3 可并行（互不依赖）

---

## 已做决策
| 决策 | 理由 |
|------|------|
| 先做删除功能（P0） | 影响用户基本使用，无法清理数据 |
| 硬删除（非软删除） | 本次范围控制；软删除需评估级联影响 |
| 阻止删除而非级联 | 避免误删导致数据丢失；用户需手动清理子资源 |
| 服务层引入领域异常 | 解耦 HTTP 层，提高可测试性和复用性 |
| 前端先接入功能，后拆分组件 | 优先级：功能可用 > 代码美观 |

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| （暂无） | - | - |

## 备注
- 阶段 1 必须完全完成后再进入阶段 2/3
- 前端工作台拆分属于可选优化，如时间紧张可延后
- 所有删除操作需确认外键约束行为（ON DELETE）
- 每个阶段完成后更新此文件状态，并更新 progress.md
