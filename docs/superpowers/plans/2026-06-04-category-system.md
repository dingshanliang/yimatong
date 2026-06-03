# 租户级品类管理系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将硬编码的品类列表改为租户级可配置，统一所有消费端数据源。

**Architecture:** 在 Tenant 模型新增 `categories` JSON 字段存储品类数组，新增轻量 API 端点供前端查询。前端用 SWR hook 统一获取品类，替换 AI 助手和产品页面的硬编码/动态提取逻辑。

**Tech Stack:** SQLAlchemy 2.0 + Alembic (后端)，Next.js + Ant Design 6 + SWR (前端)

---

## Task 1: 创建行业默认品类常量文件

**Files:**
- Create: `backend/app/constants/__init__.py`
- Create: `backend/app/constants/categories.py`

- [ ] **Step 1: 创建 `__init__.py`**

```python
# backend/app/constants/__init__.py
```

空文件，将 `constants` 标记为 Python 包。

- [ ] **Step 2: 创建 `categories.py`**

```python
# backend/app/constants/categories.py
"""行业默认品类映射表。

租户创建时根据 industry 字段取对应品类列表填充 tenant.categories。
"""

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

DEFAULT_CATEGORIES: list[str] = ["其他"]
```

- [ ] **Step 3: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/app/constants/__init__.py backend/app/constants/categories.py
git commit -m "feat: add industry-to-categories mapping constants"
```

---

## Task 2: 后端模型与 Schema 变更

**Files:**
- Modify: `backend/app/models/tenant.py` (在 `enabled_features` 字段后新增 `categories`)
- Modify: `backend/app/schemas/tenant.py` (在 `TenantUpdate` 和 `TenantRead` 中新增字段)

- [ ] **Step 1: Tenant 模型新增 categories 字段**

在 `backend/app/models/tenant.py` 第 64 行（`enabled_features` 后）新增：

```python
    categories: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True, comment="租户品类配置")
```

完整的字段上下文（第 59-65 行替换为）：

```python
    industry: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    quota: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    compliance_settings: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    onboarding_progress: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    enabled_features: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    categories: Mapped[list | None] = mapped_column(JSON, default=list, nullable=True, comment="租户品类配置")
    created_at: Mapped[datetime] = mapped_column(
```

- [ ] **Step 2: TenantUpdate Schema 新增 categories 字段**

在 `backend/app/schemas/tenant.py` 的 `TenantUpdate` 类中（第 36 行 `enabled_features` 后），新增：

```python
    categories: list[str] | None = None
```

同时在 `TenantUpdate` 类底部新增校验器（在类结束前）：

```python
    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("品类数量不能超过 100 条")
        # trim + 去重
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = item.strip()
            if not s:
                continue
            if len(s) > 20:
                raise ValueError(f"品类名称不能超过 20 个字符: {s}")
            key = s.lower()
            if key not in seen:
                seen.add(key)
                cleaned.append(s)
        return cleaned
```

注意：需要在文件顶部 import 中确认 `field_validator` 已导入（第 4 行已有）。

- [ ] **Step 3: TenantRead Schema 新增 categories 字段**

在 `backend/app/schemas/tenant.py` 的 `TenantRead` 类中（第 52 行 `enabled_features` 后），新增：

```python
    categories: list[str] | None = Field(None, description="租户品类配置")
```

- [ ] **Step 4: 验证 import 无缺失**

`backend/app/schemas/tenant.py` 顶部已有 `from pydantic import BaseModel, Field, field_validator`，无需额外 import。

- [ ] **Step 5: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/app/models/tenant.py backend/app/schemas/tenant.py
git commit -m "feat: add categories field to Tenant model and schemas"
```

---

## Task 3: 数据库迁移

**Files:**
- Create: `backend/alembic/versions/xxxx_add_tenant_categories.py` (由 alembic 自动生成)

- [ ] **Step 1: 激活虚拟环境并生成迁移**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend
source .venv/bin/activate
alembic revision --autogenerate -m "add tenant categories column"
```

- [ ] **Step 2: 检查生成的迁移文件**

打开生成的迁移文件，确认：
- `upgrade()` 中有 `op.add_column('tenants', sa.Column('categories', sa.JSON(), nullable=True, comment='租户品类配置', server_default='[]'))`
- `downgrade()` 中有 `op.drop_column('tenants', 'categories')`

如果 `server_default` 未自动生成，手动补上 `server_default='[]'`。

- [ ] **Step 3: 运行迁移**

```bash
alembic upgrade head
```

- [ ] **Step 4: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/alembic/versions/
git commit -m "migrate: add categories column to tenants table"
```

---

## Task 4: 后端服务层更新

**Files:**
- Modify: `backend/app/services/tenant.py`

- [ ] **Step 1: update_tenant 函数新增 categories 参数**

在 `backend/app/services/tenant.py` 的 `update_tenant` 函数签名中（第 101-113 行），新增参数：

```python
async def update_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    name: str | None = None,
    industry: str | None = None,
    notes: str | None = None,
    quota: dict | None = None,
    compliance_settings: dict | None = None,
    plan_expires_at: datetime | None = None,
    onboarding_progress: dict | None = None,
    enabled_features: dict | None = None,
    tenant_type: str | None = None,
    categories: list[str] | None = None,
) -> Tenant | None:
```

在函数体中（第 136 行 `tenant_type` 处理之后），新增：

```python
    if categories is not None:
        tenant.categories = categories
```

- [ ] **Step 2: create_tenant 函数填充默认品类**

在 `backend/app/services/tenant.py` 的 `create_tenant` 函数中，找到 Tenant 构造（第 37-46 行），在 `quota=...` 行后新增：

```python
        categories=_get_default_categories(industry),
```

然后在文件顶部（`_generate_slug` 函数之后，约第 19 行）新增辅助函数：

```python
from app.constants.categories import DEFAULT_CATEGORIES, INDUSTRY_DEFAULT_CATEGORIES


def _get_default_categories(industry: str | None) -> list[str]:
    """根据行业返回默认品类列表。"""
    if industry and industry in INDUSTRY_DEFAULT_CATEGORIES:
        return INDUSTRY_DEFAULT_CATEGORIES[industry][:]
    return DEFAULT_CATEGORIES[:]
```

注意：`from app.constants.categories import ...` 放在文件顶部 import 区（第 9 行 `from app.utils.security import hash_password` 之后）。

- [ ] **Step 3: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/app/services/tenant.py
git commit -m "feat: tenant service supports categories with industry defaults"
```

---

## Task 5: 后端 API 端点

**Files:**
- Modify: `backend/app/api/v1/tenants.py`

- [ ] **Step 1: 新增 GET /me/categories 端点**

在 `backend/app/api/v1/tenants.py` 的 `get_current_tenant_endpoint` 函数之后（第 94 行后），新增：

```python
from pydantic import BaseModel as PydanticModel


class CategoriesResponse(PydanticModel):
    categories: list[str] = Field(default_factory=list, description="租户品类列表")


@router.get("/me/categories", response_model=CategoriesResponse, summary="获取当前租户品类列表")
async def get_current_tenant_categories(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    tenant = await get_tenant(db, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return CategoriesResponse(categories=tenant.categories or [])
```

注意：`from pydantic import BaseModel as PydanticModel` 放在文件顶部 import 区。因为文件已经从 schemas 导入了 `TenantCreate` 等，这里用别名避免冲突。或者更好的做法是直接在顶部改为 `from pydantic import BaseModel as PydanticBaseModel`。

但实际上查看 schemas/tenant.py 的 import 方式 — 该文件使用 `from pydantic import BaseModel, Field, field_validator`。API 路由文件没有直接 import pydantic。所以直接在文件顶部加一行：

```python
from pydantic import BaseModel as PydanticBaseModel
```

然后在函数前定义：

```python
class CategoriesResponse(PydanticBaseModel):
    categories: list[str] = Field(default_factory=list, description="租户品类列表")
```

需要确认顶部已有 `from pydantic import ...` — 没有，需要新增 import 行。`Field` 也不在当前 import 中。

最终顶部新增（放在第 3 行 `from fastapi import ...` 之后）：

```python
from pydantic import BaseModel as PydanticBaseModel, Field as PydanticField
```

endpoint 中的 response model：

```python
class CategoriesResponse(PydanticBaseModel):
    categories: list[str] = PydanticField(default_factory=list, description="租户品类列表")
```

- [ ] **Step 2: PATCH /me 和 PATCH /{tenant_id} 端点传递 categories 参数**

在 `update_current_tenant_endpoint` 函数中（第 103 行），在 `update_tenant` 调用中新增参数：

```python
    tenant = await update_tenant(
        db,
        tenant_id,
        name=body.name,
        industry=body.industry,
        notes=body.notes,
        quota=body.quota,
        compliance_settings=body.compliance_settings,
        plan_expires_at=body.plan_expires_at,
        onboarding_progress=body.onboarding_progress,
        enabled_features=body.enabled_features,
        tenant_type=body.tenant_type,
        categories=body.categories,
    )
```

同样修改 `update_tenant_endpoint`（第 131 行）：

```python
    tenant = await update_tenant(
        db,
        tenant_id,
        name=body.name,
        industry=body.industry,
        notes=body.notes,
        quota=body.quota,
        compliance_settings=body.compliance_settings,
        plan_expires_at=body.plan_expires_at,
        onboarding_progress=body.onboarding_progress,
        enabled_features=body.enabled_features,
        tenant_type=body.tenant_type,
        categories=body.categories,
    )
```

- [ ] **Step 3: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/app/api/v1/tenants.py
git commit -m "feat: add GET /me/categories endpoint and wire categories to PATCH"
```

---

## Task 6: 后端测试

**Files:**
- Create: `backend/tests/test_services/test_tenant_categories.py`

- [ ] **Step 1: 写测试**

```python
# backend/tests/test_services/test_tenant_categories.py
"""Tests for tenant categories: industry defaults, CRUD, and validation."""

import pytest
from pydantic import ValidationError

from app.constants.categories import DEFAULT_CATEGORIES, INDUSTRY_DEFAULT_CATEGORIES, _get_default_categories
from app.schemas.tenant import TenantUpdate


class TestIndustryDefaultCategories:
    def test_returns_industry_specific_categories(self):
        result = _get_default_categories("茶叶")
        assert result == INDUSTRY_DEFAULT_CATEGORIES["茶叶"]

    def test_returns_copy_not_reference(self):
        result = _get_default_categories("茶叶")
        result.append("新茶")
        assert "新茶" not in INDUSTRY_DEFAULT_CATEGORIES["茶叶"]

    def test_returns_default_for_unknown_industry(self):
        result = _get_default_categories("未知行业")
        assert result == DEFAULT_CATEGORIES

    def test_returns_default_for_none_industry(self):
        result = _get_default_categories(None)
        assert result == DEFAULT_CATEGORIES


class TestCategoryValidation:
    def test_accepts_valid_categories(self):
        schema = TenantUpdate(categories=["大米", "面粉", "食用油"])
        assert schema.categories == ["大米", "面粉", "食用油"]

    def test_trims_whitespace(self):
        schema = TenantUpdate(categories=["  大米  ", "面粉"])
        assert schema.categories == ["大米", "面粉"]

    def test_deduplicates_case_insensitive(self):
        schema = TenantUpdate(categories=["大米", "大米", "DaMi"])
        assert schema.categories == ["大米"]

    def test_removes_empty_strings(self):
        schema = TenantUpdate(categories=["大米", "", "  ", "面粉"])
        assert schema.categories == ["大米", "面粉"]

    def test_rejects_too_long_category(self):
        with pytest.raises(ValidationError, match="不能超过 20 个字符"):
            TenantUpdate(categories=["A" * 21])

    def test_rejects_too_many_categories(self):
        with pytest.raises(ValidationError, match="不能超过 100 条"):
            TenantUpdate(categories=[f"品类{i}" for i in range(101)])

    def test_accepts_none(self):
        schema = TenantUpdate(categories=None)
        assert schema.categories is None

    def test_accepts_empty_list(self):
        schema = TenantUpdate(categories=[])
        assert schema.categories == []
```

注意：`_get_default_categories` 需要在 `backend/app/constants/categories.py` 中导出。如果它在 `tenant.py` 服务中作为局部函数定义，则需要移到 `categories.py` 中导出。

在 `backend/app/constants/categories.py` 末尾新增：

```python
def get_default_categories(industry: str | None) -> list[str]:
    """根据行业返回默认品类列表。"""
    if industry and industry in INDUSTRY_DEFAULT_CATEGORIES:
        return INDUSTRY_DEFAULT_CATEGORIES[industry][:]
    return DEFAULT_CATEGORIES[:]
```

然后 `tenant.py` 中的 `_get_default_categories` 改为调用 `get_default_categories`，测试 import 使用 `from app.constants.categories import get_default_categories`。

- [ ] **Step 2: 运行测试验证通过**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend
source .venv/bin/activate
python -m pytest tests/test_services/test_tenant_categories.py -v
```

Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add backend/tests/test_services/test_tenant_categories.py backend/app/constants/categories.py
git commit -m "test: add tenant categories unit tests"
```

---

## Task 7: 前端 — useCategories Hook

**Files:**
- Create: `frontend/apps/admin/src/lib/use-categories.ts`

- [ ] **Step 1: 创建 SWR hook**

```typescript
// frontend/apps/admin/src/lib/use-categories.ts
"use client";

import useSWR from "swr";
import api from "./api";

export interface CategoriesResponse {
  categories: string[];
}

/**
 * 获取当前租户品类列表。
 * SWR 自动缓存，staleTime 由全局 provider 控制。
 */
export function useCategories() {
  const { data, isLoading, mutate } = useSWR<CategoriesResponse>(
    "/tenants/me/categories"
  );

  return {
    categories: data?.categories ?? [],
    loading: isLoading,
    mutate,
  };
}
```

- [ ] **Step 2: 验证 SWR fetcher 配置**

确认 `frontend/apps/admin/src/lib/swr-provider.tsx` 或 `api.ts` 中 SWR 的 fetcher 能正确处理 `/tenants/me/categories` 路径。

SWR 默认 fetcher 需要配置。检查 `swr-provider.tsx` 中是否有全局 fetcher 配置。如果有类似 `fetcher: (url) => api.get(url).then(r => r.data)` 的配置，则路径会自动拼接为完整 API URL。如果没有，需要在 hook 中使用自定义 fetcher：

```typescript
const fetcher = (url: string) => api.get(url).then((r) => r.data);

const { data, isLoading, mutate } = useSWR<CategoriesResponse>(
  "/tenants/me/categories",
  fetcher
);
```

根据实际 `swr-provider.tsx` 的配置决定是否需要内联 fetcher。

- [ ] **Step 3: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add frontend/apps/admin/src/lib/use-categories.ts
git commit -m "feat: add useCategories SWR hook"
```

---

## Task 8: 前端 — 租户设置页新增品类管理

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/settings/tenant/page.tsx`

- [ ] **Step 1: 新增品类管理 UI**

在 `settings/tenant/page.tsx` 中做以下修改：

1. 顶部 import 新增：

```typescript
import { DeleteOutlined, PlusOutlined, ArrowUpOutlined, ArrowDownOutlined } from "@ant-design/icons";
import { useCategories } from "@/lib/use-categories";
```

2. 在组件函数内部（`const [form] = Form.useForm();` 之后），新增品类管理 state：

```typescript
  // 品类管理
  const { categories: savedCategories, mutate: mutateCategories } = useCategories();
  const [localCategories, setLocalCategories] = useState<string[]>([]);
  const [newCategory, setNewCategory] = useState("");
  const [categoriesDirty, setCategoriesDirty] = useState(false);
  const [savingCategories, setSavingCategories] = useState(false);
```

3. 新增同步 effect（在第一个 `useEffect` 之后）：

```typescript
  // 同步远程品类到本地编辑状态
  useEffect(() => {
    if (savedCategories.length > 0 || localCategories.length === 0) {
      setLocalCategories(savedCategories);
      setCategoriesDirty(false);
    }
  }, [savedCategories]);
```

4. 新增品类操作函数：

```typescript
  const addCategory = () => {
    const trimmed = newCategory.trim();
    if (!trimmed) return;
    if (localCategories.some((c) => c.toLowerCase() === trimmed.toLowerCase())) {
      message.warning("该品类已存在");
      return;
    }
    if (trimmed.length > 20) {
      message.warning("品类名称不能超过 20 个字符");
      return;
    }
    setLocalCategories((prev) => [...prev, trimmed]);
    setNewCategory("");
    setCategoriesDirty(true);
  };

  const removeCategory = (index: number) => {
    setLocalCategories((prev) => prev.filter((_, i) => i !== index));
    setCategoriesDirty(true);
  };

  const moveCategory = (index: number, direction: "up" | "down") => {
    setLocalCategories((prev) => {
      const next = [...prev];
      const target = direction === "up" ? index - 1 : index + 1;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setCategoriesDirty(true);
  };

  const saveCategories = async () => {
    if (!tenantId) return;
    setSavingCategories(true);
    try {
      await api.patch(`/tenants/${tenantId}`, { categories: localCategories });
      mutateCategories();
      setCategoriesDirty(false);
      message.success("品类配置已保存");
    } catch {
      message.error("保存品类失败");
    } finally {
      setSavingCategories(false);
    }
  };
```

5. 在 JSX 中，在功能开关的 `</div>` 之后，新增品类管理区域：

```tsx
      <Divider />

      <div className="max-w-lg">
        <Title level={5} className="!mb-2">品类管理</Title>
        <Text type="secondary" className="block mb-4">
          管理产品品类选项，用于产品录入和 AI 助手中的品类下拉
        </Text>

        <div className="mb-3 flex gap-2">
          <Input
            placeholder="输入品类名称"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            onPressEnter={addCategory}
            maxLength={20}
            className="flex-1"
          />
          <Button icon={<PlusOutlined />} onClick={addCategory} disabled={!newCategory.trim()}>
            添加
          </Button>
        </div>

        {localCategories.length === 0 ? (
          <div className="py-4 text-center text-text-muted">暂无品类，请添加</div>
        ) : (
          <div className="space-y-1">
            {localCategories.map((cat, idx) => (
              <div
                key={`${cat}-${idx}`}
                className="flex items-center justify-between rounded border border-border-subtle px-3 py-2"
              >
                <span className="flex-1">
                  <Text type="secondary" className="mr-2 text-xs">{idx + 1}.</Text>
                  {cat}
                </span>
                <Space size={4}>
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowUpOutlined />}
                    disabled={idx === 0}
                    onClick={() => moveCategory(idx, "up")}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowDownOutlined />}
                    disabled={idx === localCategories.length - 1}
                    onClick={() => moveCategory(idx, "down")}
                  />
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => removeCategory(idx)}
                  />
                </Space>
              </div>
            ))}
          </div>
        )}

        {categoriesDirty && (
          <div className="mt-3">
            <Button type="primary" onClick={saveCategories} loading={savingCategories}>
              保存品类
            </Button>
          </div>
        )}
      </div>
```

- [ ] **Step 2: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add frontend/apps/admin/src/app/\(dashboard\)/settings/tenant/page.tsx
git commit -m "feat: add category management UI to tenant settings page"
```

---

## Task 9: 前端 — AI 助手页面替换硬编码品类

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/ai-assistant/_components/PagePlanTab.tsx`

- [ ] **Step 1: 替换硬编码品类**

1. 顶部 import 新增：

```typescript
import { useCategories } from "@/lib/use-categories";
```

2. 在 `PagePlanTab` 组件函数内（`const [form] = Form.useForm();` 之后），新增：

```typescript
  const { categories: tenantCategories } = useCategories();
```

3. 将第 65-67 行的硬编码 Select options：

```typescript
              <Select showSearch allowClear placeholder="选择或输入品类"
                options={["大米","面粉","食用油","茶叶","水果","蔬菜","肉类","乳制品","酒类","饮料","零食","保健品","其他"].map(v => ({ value: v, label: v }))}
              />
```

替换为：

```typescript
              <Select showSearch allowClear placeholder="选择或输入品类"
                options={tenantCategories.map(v => ({ value: v, label: v }))}
              />
```

品类为空时 `options` 为空数组，`showSearch` 的 Select 仍允许自由输入，自然降级。

- [ ] **Step 2: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add frontend/apps/admin/src/app/\(dashboard\)/ai-assistant/_components/PagePlanTab.tsx
git commit -m "refactor: replace hardcoded categories with useCategories hook in AI assistant"
```

---

## Task 10: 前端 — 产品列表页品类下拉改用 hook

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/products/page.tsx`

- [ ] **Step 1: 替换品类下拉数据源**

1. 顶部 import 新增：

```typescript
import { useCategories } from "@/lib/use-categories";
```

2. 在组件函数内（`const [categorySearch, setCategorySearch] = useState("");` 之后），新增：

```typescript
  const { categories: tenantCategories } = useCategories();
```

3. 替换 `categoryOptions` 的 `useMemo`（第 55-60 行）：

原代码：
```typescript
  const categoryOptions = useMemo(() => {
    const values = products.map((product) => product.category).filter(Boolean) as string[];
    const typed = categorySearch.trim();
    if (typed) values.push(typed);
    return Array.from(new Set(values)).map((value) => ({ value, label: value }));
  }, [categorySearch, products]);
```

替换为：

```typescript
  const categoryOptions = useMemo(() => {
    // 以租户品类列表为基础
    const base = tenantCategories.slice();
    // 补充已有产品中存在但不在租户列表中的品类（不丢数据）
    const productCategories = products.map((p) => p.category).filter(Boolean) as string[];
    for (const cat of productCategories) {
      if (!base.some((c) => c.toLowerCase() === cat.toLowerCase())) {
        base.push(cat);
      }
    }
    // 用户当前输入
    const typed = categorySearch.trim();
    if (typed && !base.some((c) => c.toLowerCase() === typed.toLowerCase())) {
      base.push(typed);
    }
    return base.map((value) => ({ value, label: value }));
  }, [categorySearch, products, tenantCategories]);
```

- [ ] **Step 2: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add frontend/apps/admin/src/app/\(dashboard\)/products/page.tsx
git commit -m "refactor: products page category dropdown uses useCategories hook"
```

---

## Task 11: 前端 — 产品详情页品类输入改用 Select

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/products/[id]/page.tsx`

- [ ] **Step 1: 替换品类输入为 Select**

1. 顶部 import 新增：

```typescript
import { useCategories } from "@/lib/use-categories";
```

2. 在组件函数内（`const [loading, setLoading] = useState(true);` 之后），新增：

```typescript
  const { categories: tenantCategories } = useCategories();
```

3. 找到 profile tab 中的品类字段（第 627 行）：

```typescript
                    <Form.Item name="category" label="品类"><Input /></Form.Item>
```

替换为：

```typescript
                    <Form.Item name="category" label="品类">
                      <Select
                        showSearch
                        allowClear
                        placeholder="选择或输入品类"
                        options={tenantCategories.map((v) => ({ value: v, label: v }))}
                      />
                    </Form.Item>
```

- [ ] **Step 2: 提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git add frontend/apps/admin/src/app/\(dashboard\)/products/\[id\]/page.tsx
git commit -m "refactor: product workbench category field uses Select with useCategories"
```

---

## Task 12: 集成验证

- [ ] **Step 1: 后端 lint 检查**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend
source .venv/bin/activate
ruff check app/constants/ app/services/tenant.py app/api/v1/tenants.py app/schemas/tenant.py app/models/tenant.py
ruff format app/constants/ app/services/tenant.py app/api/v1/tenants.py app/schemas/tenant.py app/models/tenant.py
```

- [ ] **Step 2: 运行全部后端测试**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend
source .venv/bin/activate
python -m pytest tests/test_services/test_tenant_categories.py -v
```

- [ ] **Step 3: 启动后端验证 API**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend
source .venv/bin/activate
uvicorn app.main:app --reload &
# 等待启动后测试
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/tenants/me/categories | python -m json.tool
```

- [ ] **Step 4: 前端构建验证**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend
pnpm build:shared
pnpm build:admin
```

- [ ] **Step 5: 手动 UI 验证**

启动 `pnpm dev:admin`，依次验证：
1. 设置 > 租户设置 > 品类管理区域是否正确显示
2. 添加/删除/排序品类后保存是否生效
3. 产品列表 > 新建产品 > 品类下拉是否显示租户品类
4. AI 助手 > 页面文案 > 品类下拉是否显示租户品类

- [ ] **Step 6: 清理临时文件并最终提交**

```bash
cd /Users/ericding/code/agriculture/yimatong
git status  # 检查无未跟踪临时文件
git add -A
git commit -m "feat: complete tenant-level category management system"
```
