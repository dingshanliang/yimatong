# User Journey Review Report - M4 商品资料（brands/products/skus/batches）

### 📊 Review Overview

- Review Date: 2026-09-05
- Scope: 品牌 → 产品 → SKU → 生产批次级联创建/编辑/删除；CSV 批量导入；批次召回/过期；图片资产上传
- 方式: 代码驱动审查 + 基线测试根因定位（代码评审模块）
- Issues Found: 1 数据正确性 bug（P0）+ 吞 detail ×8 + 状态展示错误 ×2 页 + CI 超时根因；本次修复 P0/P1 共 8 项，P2 记录
- Journey Score（修复后均值）: 4.2

---

### 🔴 P0（全部已修复）

#### 1. CSV 批量导入 SKU 归属错误（数据正确性）

- **Location**: `frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx:1739-1750`
- **现状**: 文件 onChange 用 `form.querySelector('[name="sku_id"]')` 取 SKU——antd Select 的 DOM 无 name 属性，恒为 null → 回退 `skus[0]?.id`。多 SKU 产品时用户选择被忽略，批次永远导入到第一个 SKU。
- **Fix**: 导入表单绑定 `importForm`（Form.useForm），onChange 从 `importForm.getFieldValue("sku_id")` 取用户选择。

#### 2. products/[id] 页面测试 CI 超时（基线 3 失败根因）

- **结论**: 组件与断言均正确（本机 7/7 通过），是 jsdom 渲染整页 Tabs+3 表+4 Modal 成本过高，3 个最慢用例（7-10s）逼近 CI 的 15s 显式 timeout。
- **Fix**: 该文件 3 处显式 timeout 15_000 → 30_000。后续可考虑抽取轻量渲染（记录）。

#### 3. 列表页错误提示吞服务端 detail

- **Fix**: products/brands/BrandFormModal/skus/batches 共 10 处 catch（含 3 处状态 Switch）改用 `extractErrorMessage`；品牌重名/SKU 编码重复/批次号重复/配额超限等 409/422 语义可达用户。

### 🟡 P1（全部已修复）

- **过期批次展示错误（安全相关）**: `brands/[id]` 与 `skus/[id]` 的批次状态列改用后端 `effective_status`（此前已过期但 status=active 的批次显示绿色「有效」，与 /batches 页和产品工作台矛盾）。
- **后端 catalog 冲突文案中文化**: `services/product.py` + `schemas/product.py` 共 15+ 条面向用户 detail 中文化（品牌名/SKU 编码/批次号重复、召回/编辑门控、删除级联 409、CSV 导入行错误、保质期校验），同步更新 6 个测试文件的断言。
- **品牌删除入口缺失**: brands 列表操作列新增删除（admin 权限门控 + Popconfirm + 级联保护 409 透出）。
- **SKU 页筛选回显**: 产品筛选 Select 由 `value={undefined}` 改受控。

### 📋 记录（P2，未修）

1. 删除入口旅程不对称：SKU/批次独立页无删除入口（仅产品工作台内可删）。
2. 级联依赖下拉 `page_size: 100` 硬编码，超 100 条父资源静默缺失（建议加搜索）。
3. 编辑时清空 Select 类字段（品类）可能无法保存为空（axios 丢弃 undefined；需运行时复验）。
4. 搜索框无防抖；上传组件缺客户端大小/类型前置校验（文案承诺 5MB/20MB 但直接打服务端）；CSV 导入错误列表服务端 100 条封顶且前端未提示截断；召回批次仍可删除的语义待产品确认。

### ✅ 验证记录（2026-09-05）

| 项                                  | 结果     |
| ----------------------------------- | -------- |
| products/[id] vitest（含超时修复）  | 7/7      |
| brands/products/skus/batches vitest | 24/24    |
| 后端 catalog 相关 API 测试          | 157 通过 |
| tsc --noEmit / eslint / ruff        | 通过     |
