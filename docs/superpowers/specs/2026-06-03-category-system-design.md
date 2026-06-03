---
title: 租户级品类管理系统
status: ✅ approved
last_verified: 2026-06-03
accuracy: high
---

# 租户级品类管理系统

## 背景

当前品类（category）系统存在三个问题：

1. **硬编码**：AI 助手页面写死 13 个食品品类，无法服务其他行业客户
2. **不一致**：AI 助手硬编码、产品页动态提取、后端无约束 — 三处各做各的
3. **无管理能力**：没有增删改品类的入口

不同行业的客户品类需求完全不同（茶叶客户要"绿茶/红茶/白茶"，水果客户要"苹果/橙子/草莓"），需要租户级可配置的品类管理。

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 层级深度 | 单层扁平 | 当前 PRD 和产品模型都是扁平结构，YAGNI |
| 存储方式 | 租户 JSON 字段 | 品类是小型配置数据（< 50 条），与现有 `enabled_features` 模式一致 |
| 管理入口 | 租户设置内 Tab | 复用现有设置页架构，不需要新增菜单项 |
| 初始数据 | 按行业预填 | 根据租户 `industry` 字段自动填充对应品类，租户可自由增删改 |
| 实施范围 | 完整统一 | 后端存储 + 管理界面 + 所有消费端统一 |

## 1. 数据层

### Tenant 模型变更

文件：`backend/app/models/tenant.py`

新增字段：

```python
categories: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True, comment="租户品类配置")
```

存储格式为字符串数组：`["大米", "面粉", "食用油"]`

### Schema 变更

文件：`backend/app/schemas/tenant.py`

- `TenantRead` 新增 `categories: list[str] | None = None`
- `TenantUpdate` 新增 `categories: list[str] | None = None`

### 行业默认品类映射

新建 `backend/app/constants/categories.py`：

```python
INDUSTRY_DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "茶叶": ["绿茶", "红茶", "乌龙茶", "白茶", "黄茶", "黑茶", "花茶", "普洱茶"],
    "水果": ["苹果", "橙子", "草莓", "葡萄", "猕猴桃", "桃子", "梨", "柑橘"],
    "大米": ["籼米", "粳米", "糯米", "糙米", "有机大米", "富硒大米"],
    "农产品": ["蔬菜", "水果", "粮食", "食用油", "蜂蜜", "坚果", "菌菇"],
    "食品": ["大米", "面粉", "食用油", "茶叶", "零食", "饮料", "酒类", "乳制品", "保健品"],
    "酒类": ["白酒", "红酒", "啤酒", "黄酒", "果酒", "米酒"],
    "乳制品": ["牛奶", "酸奶", "奶酪", "奶粉", "黄油"],
    "食用油": ["花生油", "菜籽油", "大豆油", "橄榄油", "芝麻油", "葵花籽油"],
    "饮料": ["碳酸饮料", "果汁", "茶饮料", "功能饮料", "矿泉水"],
    "零食": ["饼干", "薯片", "坚果", "糖果", "巧克力", "肉干"],
}
DEFAULT_CATEGORIES = ["其他"]
```

租户创建时，根据 `industry` 字段从映射取默认值填充 `categories`。

### 数据库迁移

新增 Alembic 迁移：在 `tenants` 表添加 `categories` JSON 列，默认值为空数组。

## 2. API 层

### 新增品类查询接口

```
GET /api/v1/tenants/me/categories
```

- 需认证，从 JWT 获取 `tenant_id`
- 返回：`{ "categories": ["大米", "面粉", "食用油"] }`
- 轻量接口，仅供前端品类下拉消费

### 品类更新

复用现有 `PATCH /api/v1/tenants/me`，请求体传 `categories` 字段。

校验规则：
- 每个品类字符串 trim 后长度 ≤ 20 字符
- 列表长度 ≤ 100 条
- 去重（trim 后比较）
- 允许空列表

### 产品 API 不变

- `product.category` 仍为自由字符串，不强制从品类列表选择
- 品类列表仅作为下拉建议

## 3. 前端 — 管理界面

### 租户设置页新增「品类管理」Tab

位置：现有租户设置页新增 Tab

布局：

```
┌─────────────────────────────────────────┐
│  品类管理                                │
├─────────────────────────────────────────┤
│  [+ 添加品类]                           │
│                                         │
│  ┌─ 1. 大米    ─── [↑] [↓] [✕] ──┐     │
│  ├─ 2. 面粉    ─── [↑] [↓] [✕] ──┤     │
│  ├─ 3. 食用油  ─── [↑] [↓] [✕] ──┤     │
│  ├─ 4. 茶叶    ─── [↑] [↓] [✕] ──┤     │
│  └─ 5. 水果    ─── [↑] [↓] [✕] ──┘     │
│                                         │
│              [保存]                      │
└─────────────────────────────────────────┘
```

功能：
- **添加**：Input 行，回车或失焦确认
- **删除**：点击 ✕ 移除（不阻止删除，仅影响下拉选项）
- **排序**：上下箭头调整顺序
- **保存**：`PATCH /tenants/me` 提交完整 `categories` 数组
- 组件：Ant Design `List` + `Input` + `Button`

## 4. 前端 — 消费端统一

### 新增品类 Hook

文件：`frontend/apps/admin/src/lib/use-categories.ts`

- 调用 `GET /api/v1/tenants/me/categories`
- 返回 `{ categories: string[], loading: boolean }`
- React Query 缓存，`staleTime` 5 分钟

### 改造：AI 助手页面

文件：`frontend/apps/admin/src/app/(dashboard)/ai-assistant/_components/PagePlanTab.tsx`

- 移除硬编码 13 个品类数组
- 改用 `useCategories()` 获取
- 品类为空时降级为自由输入

### 改造：产品列表页

文件：`frontend/apps/admin/src/app/(dashboard)/products/page.tsx`

- 品类下拉选项从 `useCategories()` 获取
- 保留自由输入能力
- 已有产品品类不在列表中也显示（不丢数据）

### 改造：产品详情/编辑页

文件：`frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`

- 品类输入改为 `Select` + `showSearch`，选项来自 `useCategories()`
- 保留手动输入能力

## 不变的部分

- 后端 `product.category` 仍是自由字符串，无强制校验
- 产品 API 的 `category` 筛选参数不变
- AI prompt 中的品类示例文本保持原样
- H5 和 Platform 应用不涉及品类管理

## 影响范围

| 类型 | 文件 | 变更 |
|------|------|------|
| 模型 | `backend/app/models/tenant.py` | 新增 `categories` JSON 字段 |
| 常量 | `backend/app/constants/categories.py` | **新建** 行业默认品类映射 |
| Schema | `backend/app/schemas/tenant.py` | `TenantRead`/`TenantUpdate` 新增字段 |
| API | `backend/app/api/v1/tenants.py` | 新增 `GET /me/categories` 端点 |
| 服务 | `backend/app/services/tenant.py` | 创建租户时填充默认品类 |
| 迁移 | `backend/alembic/versions/` | 新增迁移 |
| Hook | `frontend/apps/admin/src/lib/use-categories.ts` | **新建** React Query hook |
| 页面 | 租户设置页 | 新增品类管理 Tab |
| 组件 | `PagePlanTab.tsx` | 移除硬编码，改用 hook |
| 组件 | `products/page.tsx` | 品类下拉改用 hook |
| 组件 | `products/[id]/page.tsx` | 品类输入改用 hook |
