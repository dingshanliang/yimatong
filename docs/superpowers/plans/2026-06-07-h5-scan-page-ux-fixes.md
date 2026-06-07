# H5 扫码页体验修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 H5 消费者扫码页的 12 个体验问题（3 Critical + 4 Moderate + 5 Suggestion），使核心扫码闭环体验完整可用。

**Architecture:** 后端 resolver 补充溯源数据和图片字段 → 前端组件适配新字段并添加空数据/加载/错误状态 → CSP 策略修复。每个 task 独立可测试。

**Tech Stack:** FastAPI (Python), Next.js 16 (React), Tailwind CSS 4, SQLAlchemy 2.0, pytest (后端), vitest (前端)

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/app/api/v1/resolver.py` | 补充溯源数据、修复图片字段、修复 scan_count |
| Modify | `frontend/apps/h5/src/app/c/[publicId]/ResolveContent.tsx` | 传递溯源数据给组件 |
| Modify | `frontend/apps/h5/src/components/ProductCard.tsx` | 适配 image_url、添加图片加载失败兜底 |
| Modify | `frontend/apps/h5/src/components/TraceabilitySection.tsx` | 适配新溯源数据结构 |
| Modify | `frontend/apps/h5/src/components/VerifyStatus.tsx` | 修复 a11y |
| Modify | `frontend/apps/h5/src/components/BrandHeader.tsx` | 添加图片加载失败兜底 |
| Modify | `frontend/apps/h5/src/components/LeadForm.tsx` | 添加提交 loading 状态、键盘优化 |
| Modify | `frontend/apps/h5/src/components/PrivateDomainButtons.tsx` | 添加微信环境检测 |
| Modify | `frontend/apps/h5/src/components/FooterSection.tsx` | a11y 修复 |
| Modify | `frontend/apps/h5/src/components/ErrorPage.tsx` | 添加返回按钮 |
| Modify | `frontend/apps/h5/next.config.ts` | CSP connect-src 修复 |
| Modify | `frontend/apps/h5/src/components/BenefitClaimCard.tsx` | 添加按钮防抖 |
| Create | `backend/tests/test_api/test_resolver_json_response.py` | resolver JSON 响应集成测试 |

---

## Task 1: 修复后端 resolver — 补充溯源数据、产品图片、scan_count 边界

**Files:**
- Modify: `backend/app/api/v1/resolver.py:152-262`
- Create: `backend/tests/test_api/test_resolver_json_response.py`

- [ ] **Step 1: 写失败测试 — 溯源数据在 JSON 响应中**

```python
# backend/tests/test_api/test_resolver_json_response.py
"""验证 resolver JSON 响应结构完整性"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal, test_engine
from app.models.base import Base


@pytest.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    from httpx import ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def traceability_setup(client: AsyncClient):
    """创建带溯源数据的完整链路"""
    resp = await client.post("/api/v1/tenants", json={
        "name": "溯源测试", "admin_email": "trace@test.com",
        "admin_name": "Admin", "admin_password": "Pass1234",
    })
    assert resp.status_code in (200, 201)
    tid = resp.json()["id"]
    token = create_access_token(tid, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    brand = await client.post("/api/v1/brands", json={"name": "溯源品牌"}, headers=headers)
    assert brand.status_code in (200, 201)

    prod = await client.post("/api/v1/products", json={
        "brand_id": brand.json()["id"], "name": "溯源大米",
        "description": "测试溯源", "origin": "黑龙江五常",
        "image_url": "https://example.com/rice.jpg",
    }, headers=headers)
    assert prod.status_code in (200, 201)

    sku = await client.post("/api/v1/skus", json={
        "product_id": prod.json()["id"], "code": "TRACE-SKU", "name": "5kg装",
    }, headers=headers)
    assert sku.status_code in (200, 201)

    pb = await client.post("/api/v1/production-batches", json={
        "product_id": prod.json()["id"], "sku_id": sku.json()["id"],
        "batch_code": "PB-TRACE-001", "production_date": "2026-03-01",
        "expiry_date": "2027-03-01", "origin": "黑龙江省五常市",
    }, headers=headers)
    assert pb.status_code in (200, 201)

    batch = await client.post("/api/v1/code-batches", json={
        "product_id": prod.json()["id"], "sku_id": sku.json()["id"],
        "production_batch_id": pb.json()["id"],
        "batch_code": "CB-TRACE-001", "quantity": 2,
    }, headers=headers)
    assert batch.status_code in (200, 201)

    await client.post(f"/api/v1/code-batches/{batch.json()['id']}/activate", headers=headers)

    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch.json()['id']}", headers=headers)
    return items.json()["items"][0]["public_id"]


class TestResolverJsonResponse:
    @pytest.mark.anyio
    async def test_json_has_product_image_url(self, client, traceability_setup):
        """产品图片应该用 image_url 而非空 images 数组"""
        resp = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        product = data["code_data"]["product"]
        assert "image_url" in product, "缺少 image_url 字段"
        assert product["image_url"] == "https://example.com/rice.jpg"

    @pytest.mark.anyio
    async def test_json_has_traceability_data(self, client, traceability_setup):
        """响应应包含溯源信息（产地、生产日期、保质期、批次号）"""
        resp = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})
        data = resp.json()
        # 溯源数据可能在 code_data 或顶层
        code_data = data.get("code_data", {})
        batch = data.get("batch") or code_data.get("batch")
        assert batch is not None, "缺少溯源/批次信息"
        assert batch.get("batch_code") == "PB-TRACE-001"
        assert batch.get("origin") == "黑龙江省五常市"
        assert batch.get("production_date") is not None
        assert batch.get("expiry_date") is not None

    @pytest.mark.anyio
    async def test_first_scan_count_is_zero(self, client, traceability_setup):
        """首次扫码时 scan_count 应为 0（不含本次）"""
        resp = await client.get(f"/c/{traceability_setup}", headers={"Accept": "application/json"})
        data = resp.json()
        assert data["scan_info"]["is_first_scan"] is True
        assert data["scan_info"]["scan_count"] == 0, "首次扫码前 scan_count 应为 0"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_resolver_json_response.py -v --tb=short`
Expected: FAIL — 缺少 image_url、缺少 batch 数据、scan_count 为 1

- [ ] **Step 3: 修改 resolver.py — 修复 product 图片字段**

在 `_build_json_response` 中，将 `images` 改为 `image_url`：

找到 `resolver.py` 中 `_build_json_response` 函数的 product_data 构建：
```python
product_data = {
    "name": product.name,
    "description": product.description,
    "images": product.images if hasattr(product, "images") and product.images else [],
}
```

替换为：
```python
product_data = {
    "name": product.name,
    "description": product.description,
    "image_url": product.image_url or "",
    "origin": product.origin or "",
}
```

- [ ] **Step 4: 修改 resolver.py — 补充溯源/批次数据**

在 `_build_json_response` 函数中，查询页面配置之后（`result["page_config"]` 之后），添加批次溯源数据查询：

```python
    # 查询码批次关联的生产批次溯源信息
    code_batch_id = data.get("code_batch_id")
    if code_batch_id:
        from app.models.code import CodeBatch
        from app.models.product import ProductionBatch

        cb_result = await db.execute(
            select(CodeBatch).where(CodeBatch.id == uuid.UUID(code_batch_id))
        )
        code_batch = cb_result.scalar_one_or_none()
        if code_batch and code_batch.production_batch_id:
            pb_result = await db.execute(
                select(ProductionBatch).where(
                    ProductionBatch.id == code_batch.production_batch_id
                )
            )
            prod_batch = pb_result.scalar_one_or_none()
            if prod_batch:
                result["batch"] = {
                    "batch_code": prod_batch.batch_code,
                    "production_date": str(prod_batch.production_date),
                    "expiry_date": str(prod_batch.expiry_date),
                    "origin": prod_batch.origin or "",
                }
```

注意：需要在 `resolve_public_code` 返回的 data 中包含 `code_batch_id`。检查 resolver 使用的 data dict 是否有此字段，如果没有需要从 resolve 服务中补充。

- [ ] **Step 5: 修改 resolver.py — 修复 scan_count 边界**

将 scan_count 查询移到 `record_scan_event` **之前**，这样返回的是"之前"的次数：

将：
```python
            scan_info["is_first_scan"] = event.is_first_scan
            # 查询累计扫码次数
            count_result = await db.execute(
                select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id)
            )
            scan_info["scan_count"] = count_result.scalar() or 0
```

改为：
```python
            # 查询本次扫码之前的事件数
            count_before = await db.execute(
                select(func.count()).select_from(ScanEvent).where(ScanEvent.public_id == public_id)
            )
            scan_info["scan_count"] = count_before.scalar() or 0
            # 记录本次扫码事件
            event = await record_scan_event(...)
            scan_info["is_first_scan"] = event.is_first_scan
```

- [ ] **Step 6: 确保 resolve_public_code 返回 code_batch_id**

检查 `app/services/resolver.py` 中 `resolve_public_code` 函数，确认返回的 dict 包含 `code_batch_id`。如果缺失，在查询 CodeItem 时补充此字段。

- [ ] **Step 7: 运行测试确认通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_resolver_json_response.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 8: 运行已有测试确认无回归**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_integration/test_core_scan_closed_loop.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 9: 提交**

```bash
git add backend/app/api/v1/resolver.py backend/app/services/resolver.py backend/tests/test_api/test_resolver_json_response.py
git commit -m "fix(resolver): add traceability data, fix product image_url, fix scan_count boundary"
```

---

## Task 2: 前端 ProductCard 适配 image_url + 图片加载失败兜底

**Files:**
- Modify: `frontend/apps/h5/src/components/ProductCard.tsx`

- [ ] **Step 1: 修改 ProductCard 组件适配 image_url**

当前组件接收 `images` 数组或 `imageUrl` 字符串。需要适配后端现在返回的 `image_url`：

将 `ProductCard` 的 props 中图片相关逻辑改为优先使用 `image_url`，兜底 `imageUrl`，最后 `images[0]`。添加 `onError` 处理：

```tsx
// 在组件内添加图片错误状态
const [imgError, setImgError] = useState(false);

// 确定图片源：优先 image_url，兜底 imageUrl，最后 images[0]
const imageSource = props.image_url || props.imageUrl || (props.images?.[0]) || "";
const showImage = imageSource && !imgError;

// img 标签添加 onError
{showImage && (
  <img
    src={imageSource}
    alt={productName || "产品图片"}
    className="w-full rounded-xl object-cover"
    onError={() => setImgError(true)}
  />
)}
{!showImage && (
  <div className="flex h-40 items-center justify-center rounded-xl bg-gray-100">
    <span className="text-4xl text-gray-300">🌾</span>
  </div>
)}
```

需要在文件顶部添加 `import { useState } from "react";`。

- [ ] **Step 2: 更新 ResolveContent 中传给 ProductCard 的 props**

在 `ResolveContent.tsx` 的 `product_hero` case 中，将 `image_url` 传给 ProductCard：

```tsx
case "product_hero":
  return (
    <ProductCard
      productName={(product.name as string) || ""}
      description={(product.description as string) || ""}
      image_url={(product.image_url as string) || ""}
      imageUrl={productImages?.[0]}
      images={productImages}
      showBadge={config.show_verify_badge as boolean}
    />
  );
```

同时在 `DefaultRender` 中也传递 `image_url`。

- [ ] **Step 3: 验证**

启动 H5 后打开扫码页，确认：
- 有 image_url 时显示图片
- 图片加载失败时显示 🌾 占位图
- 无图片时也显示占位图

- [ ] **Step 4: 提交**

```bash
git add frontend/apps/h5/src/components/ProductCard.tsx frontend/apps/h5/src/app/c/[publicId]/ResolveContent.tsx
git commit -m "fix(h5): adapt ProductCard for image_url, add image error fallback"
```

---

## Task 3: 前端 TraceabilitySection 适配溯源数据

**Files:**
- Modify: `frontend/apps/h5/src/components/TraceabilitySection.tsx`
- Modify: `frontend/apps/h5/src/app/c/[publicId]/ResolveContent.tsx`

- [ ] **Step 1: 修改 TraceabilitySection 组件适配新数据结构**

当前组件从 `codeData` 中查找溯源字段。后端现在返回 `batch` 对象（含 batch_code, production_date, expiry_date, origin）。需要合并 codeData 和 batch 中的溯源字段：

```tsx
// 合并溯源数据源
const traceData = {
  origin: batch?.origin || codeData?.origin || (product as any)?.origin || "",
  production_date: batch?.production_date || "",
  expiry_date: batch?.expiry_date || "",
  batch_code: batch?.batch_code || codeData?.batch_code || "",
  sku_name: codeData?.sku_name || "",
  sku_code: codeData?.sku_code || "",
};
```

在 `ResolveContent.tsx` 中，将 `batch` 传递给 `TraceabilitySection` 和 `light_traceability` case：

```tsx
case "light_traceability":
  return (
    <TraceabilitySection
      codeData={{ ...codeData, batch, product }}
      config={config as { fields?: string[] }}
    />
  );
```

- [ ] **Step 2: 验证**

打开扫码页，确认溯源信息区域显示：
- 产地：黑龙江省五常市
- 生产日期
- 保质期
- 批次号

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/h5/src/components/TraceabilitySection.tsx frontend/apps/h5/src/app/c/[publicId]/ResolveContent.tsx
git commit -m "fix(h5): adapt TraceabilitySection for backend batch data"
```

---

## Task 4: VerifyStatus a11y 修复 + BrandHeader 兜底

**Files:**
- Modify: `frontend/apps/h5/src/components/VerifyStatus.tsx`
- Modify: `frontend/apps/h5/src/components/BrandHeader.tsx`

- [ ] **Step 1: 给 VerifyStatus 的 SVG 添加 aria-label**

每个 SVG 图标添加 `aria-hidden="true"` 并在父容器添加 `aria-label`：

找到 VerifyStatus.tsx 中所有 `<svg>` 标签，添加 `aria-hidden="true"`。在外层 div 添加：
```tsx
<div className="..." role="status" aria-label={
  status === "first_scan" ? "首次扫码验证" : 
  status === "repeat_scan" ? "重复扫码提醒" : "验证失败"
}>
```

- [ ] **Step 2: BrandHeader 添加图片加载失败兜底**

```tsx
const [logoError, setLogoError] = useState(false);

// logo img 标签添加 onError
{logoUrl && !logoError ? (
  <img src={logoUrl} alt={`${name} logo`} className="h-10 w-10 rounded-full object-cover" onError={() => setLogoError(true)} />
) : (
  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-lg font-bold">
    {(name || "品")[0]}
  </div>
)}
```

需要在文件顶部添加 `import { useState } from "react";`。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/h5/src/components/VerifyStatus.tsx frontend/apps/h5/src/components/BrandHeader.tsx
git commit -m "fix(h5): add a11y to VerifyStatus, add logo error fallback to BrandHeader"
```

---

## Task 5: LeadForm 添加 loading 状态 + 键盘优化

**Files:**
- Modify: `frontend/apps/h5/src/components/LeadForm.tsx`

- [ ] **Step 1: 添加 isSubmitting 状态**

找到 LeadForm 中的 submit handler，添加 loading 状态控制：

```tsx
const [isSubmitting, setIsSubmitting] = useState(false);

// 在 handleSubmit 中：
const handleSubmit = async (e: React.FormEvent) => {
  e.preventDefault();
  if (isSubmitting) return;
  setIsSubmitting(true);
  try {
    // ... 现有提交逻辑 ...
  } catch (err) {
    // ... 错误处理 ...
  } finally {
    setIsSubmitting(false);
  }
};
```

修改提交按钮，loading 时禁用并显示状态：

```tsx
<button
  type="submit"
  disabled={isSubmitting}
  className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium text-white transition-colors ${
    isSubmitting ? "bg-blue-400 cursor-not-allowed" : "bg-blue-600 hover:bg-blue-700 active:bg-blue-800"
  }`}
>
  {isSubmitting ? "提交中..." : submitLabel || "提交"}
</button>
```

- [ ] **Step 2: 添加 inputMode 和 autoComplete**

手机号输入框添加 `inputMode="tel"` 和 `autoComplete="tel"`：
```tsx
<input
  id="lead-phone"
  type="tel"
  inputMode="tel"
  autoComplete="tel"
  placeholder="请输入手机号"
  ...
/>
```

地区输入框添加 `autoComplete="address-level1"`。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/h5/src/components/LeadForm.tsx
git commit -m "fix(h5): add submit loading state and inputMode to LeadForm"
```

---

## Task 6: PrivateDomainButtons 微信环境检测

**Files:**
- Modify: `frontend/apps/h5/src/components/PrivateDomainButtons.tsx`

- [ ] **Step 1: 添加微信环境检测逻辑**

```tsx
const isWeChat = typeof navigator !== "undefined" && /MicroMessenger/i.test(navigator.userAgent);

const handleClick = (url: string) => {
  if (isWeChat && url.startsWith("http")) {
    // 微信内打开外链提示用户复制到浏览器
    if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(() => {
        alert("链接已复制，请在浏览器中打开");
      });
    }
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
};
```

将现有 `window.open` 调用替换为 `handleClick(url)`。

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/h5/src/components/PrivateDomainButtons.tsx
git commit -m "fix(h5): detect WeChat env and handle external links"
```

---

## Task 7: CSP connect-src 修复

**Files:**
- Modify: `frontend/apps/h5/next.config.ts`

- [ ] **Step 1: 修改 CSP connect-src 支持后端 API 调用**

在 `next.config.ts` 中，将 `connect-src 'self'` 改为支持环境变量配置的后端 URL：

```typescript
const connectSrc = process.env.NODE_ENV !== "production"
  ? "'self' http://localhost:* http://127.0.0.1:*"
  : "'self'";

// CSP 中的 connect-src 改为:
`connect-src ${connectSrc}`,
```

这样开发环境允许客户端 API 调用到后端，生产环境保持严格。

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/h5/next.config.ts
git commit -m "fix(h5): relax CSP connect-src for development API calls"
```

---

## Task 8: ErrorPage 添加返回按钮 + FooterSection a11y

**Files:**
- Modify: `frontend/apps/h5/src/components/ErrorPage.tsx`
- Modify: `frontend/apps/h5/src/components/FooterSection.tsx`

- [ ] **Step 1: ErrorPage 添加返回操作**

在 ErrorPage 组件的错误信息下方添加操作按钮：

```tsx
<div className="mt-4 flex gap-3">
  <button
    onClick={() => window.location.reload()}
    className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white"
  >
    重新扫码
  </button>
  {publicId && (
    <a
      href={`tel:400-000-0000`}
      className="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700"
    >
      联系客服
    </a>
  )}
</div>
```

- [ ] **Step 2: FooterSection img 添加 alt**

```tsx
<img src={logoUrl} alt={name ? `${name} logo` : "一码通"} className="..." />
```

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/h5/src/components/ErrorPage.tsx frontend/apps/h5/src/components/FooterSection.tsx
git commit -m "fix(h5): add retry button to ErrorPage, fix FooterSection a11y"
```

---

## Task 9: BenefitClaimCard 按钮防抖

**Files:**
- Modify: `frontend/apps/h5/src/components/BenefitClaimCard.tsx`

- [ ] **Step 1: 添加防抖逻辑**

在 BenefitClaimCard 的领取按钮点击处理中，添加防抖：

```tsx
const lastClickRef = useRef(0);

const handleClaim = () => {
  const now = Date.now();
  if (now - lastClickRef.current < 1000) return; // 1秒防抖
  lastClickRef.current = now;
  // ... 现有领取逻辑
};
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/h5/src/components/BenefitClaimCard.tsx
git commit -m "fix(h5): add debounce to BenefitClaimCard claim button"
```

---

## Task 10: 端到端验证

**Files:** 无修改

- [ ] **Step 1: 运行后端全部测试**

```bash
cd backend && source .venv/bin/activate && python -m pytest tests/ -v --tb=short -x
```
Expected: ALL PASS

- [ ] **Step 2: 重建 H5 并启动生产服务器**

```bash
cd frontend/apps/h5
BACKEND_URL=http://localhost:8001 NEXT_PUBLIC_API_URL=http://localhost:8001 npx next build
BACKEND_URL=http://localhost:8001 NEXT_PUBLIC_API_URL=http://localhost:8001 npx next start -p 3001
```

- [ ] **Step 3: 手动验证清单**

打开 `http://localhost:3001/c/JWDRdGDXXAc`，逐项检查：

| 检查项 | 预期 |
|--------|------|
| 品牌头部 | 显示"好味道"+ Logo 或首字 |
| 产品卡片 | 显示产品名+描述+图片（或🌾占位图） |
| 验证状态 | 首次扫码绿色提示，scan_count=0 |
| 溯源信息 | 显示产地、生产日期、保质期、批次号 |
| 私域按钮 | 企微+京东按钮可点击 |
| 留资表单 | 提交时显示"提交中..."，完成后显示成功 |
| 页脚 | "由一码通提供技术支持" |

- [ ] **Step 4: 提交所有改动**

```bash
git add -A
git commit -m "fix(h5): complete H5 scan page UX fixes - traceability, images, a11y, CSP"
```
