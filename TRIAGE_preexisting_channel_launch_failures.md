# Triage：channel / launch 预存在测试失败（11 项）

> **状态：已全部修复**（分支 `fix/test-fixtures-channel-launch-ops`）。
> Apples-to-apples 回归：同一测试范围 FAILED 从 37 → 27，正好减少这 11 项，零新回归
> （唯一「新增」的 `test_jwt_auth_loads_permissions_to_request_state` 是 dev 基线已存在、
> 且已在 `9001e6cc` 另行修复的无关项）。
>
> 来源：独立安全审查（independent-review-loop，business profile）期间在 pristine 基线
> `b69b3563` / `e0eb9384` 上发现的预存在失败。**与安全审查的修补无关** —— 审查引入的
> 改动在这些用例上的失败集合与 pristine 完全一致（apples-to-apples，37 == 37，diff 为空）。
>
> 本文件是给各模块负责人的诊断交接，不是产品文档。问题处理完后可删除。

## 失败清单

`uv run pytest tests/test_api/test_channel.py tests/test_api/test_launch_releases.py tests/test_api/test_ops_launch.py -p no:cacheprovider`

```
11 failed, 48 passed
```

| #   | 测试                                                                                              | 现象                                            | 模块    |
| --- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------- | ------- |
| 1   | `test_channel.py::TestStoreCRUD::test_create_store`                                               | `POST /channels/stores` → 403                   | channel |
| 2   | `test_channel.py::TestStoreCRUD::test_patch_store_and_filter_by_region`                           | 上游 403，`store.json()["id"]` → KeyError       | channel |
| 3   | `test_channel.py::TestRegionCRUD::test_region_list_returns_distributor_name_and_store_count`      | 依赖门店创建，连锁失败                          | channel |
| 4   | `test_channel.py::TestBatchAssignment::test_store_allocation_rejects_quantity_above_remaining`    | 依赖门店创建，连锁失败                          | channel |
| 5   | `test_channel.py::TestChannelAccountScopes::test_account_scope_limits_distributor_portal_summary` | 依赖门店创建，连锁失败                          | channel |
| 6   | `test_channel.py::TestChannelAccountScopes::test_account_scope_limits_store_portal_summary`       | 依赖门店创建，连锁失败                          | channel |
| 7   | `test_channel.py::TestChannelOverview::test_overview_returns_channel_metrics`                     | 门店没建成，`store_count == 0`                  | channel |
| 8   | `test_launch_releases.py::test_agency_publish_requires_explicit_release_scope`                    | `POST /ops/launch-releases` → 403               | launch  |
| 9   | `test_ops_launch.py::TestLaunchChecklist::test_empty_checklist`                                   | `GET /ops/clients/{tid}/launch-checklist` → 401 | ops     |
| 10  | `test_ops_launch.py::TestLaunchChecklist::test_checklist_after_product`                           | 上游 401，`data["checks"]` → KeyError           | ops     |
| 11  | `test_ops_launch.py::TestLaunchChecklist::test_tenant_status`                                     | `GET /ops/clients/{tid}/status` → 401           | ops     |

## 根因分类

全部 11 项都是 **测试 fixture 的认证/数据准备 bug**，没有一项是生产代码缺陷。
按根因归并成 3 类：

### 类 A — channel：`enabled_features` 被自助接口静默丢弃（7 项：#1–#7）

**根因**

`tests/test_api/test_channel.py::setup_tenant`（第 61–66 行）用 **租户 admin token** 调
`PATCH /api/v1/tenants/me` 想打开 `channel_store` feature：

```python
feature_resp = await client.patch(
    "/api/v1/tenants/me",
    json={"enabled_features": {"channel_store": True}},
    headers=headers,  # 租户 admin Bearer
)
assert feature_resp.status_code == 200
```

但 `/tenants/me` 的请求体 schema 是 `TenantUpdateSelf`（`app/schemas/tenant.py:79`），
**故意不包含 `enabled_features`**（自助接口只放行非敏感字段，品牌方不能自己给自己开
付费 feature）。Pydantic 默认 `extra="ignore"`，所以 `enabled_features` 被静默丢弃，
接口照常 200（因为 name/notes 等是合法字段），但 `enabled_features` 根本没落库。

随后 `POST /channels/stores` 走 `require_store_enabled` 依赖
（`app/api/v1/channels.py:46`），读 `tenant.enabled_features.get("channel_store", False)`
拿到 `False` → 403「门店模块未启用」。门店建不出来，#2–#7 全部连锁失败。

**验证**

```bash
uv run python -c "
from app.schemas.tenant import TenantUpdateSelf, TenantUpdate
print('Self drops it:', not hasattr(TenantUpdateSelf(enabled_features={'x':1}), 'enabled_features'))
print('Platform keeps it:', TenantUpdate(enabled_features={'x':1}).enabled_features)
"
# Self drops it: True
# Platform keeps it: {'x': True}
```

**生产代码是对的** —— 品牌方不能自助开 feature 是正确的安全边界。错的只是测试。

**建议修法（给 channel 模块负责人）**

fixture 应改用 **平台 admin** 的 `PATCH /api/v1/tenants/{tenant_id}`（`app/api/v1/tenants.py:255`，
`require_role("platform_admin")`，请求体 `TenantUpdate` 接受 `enabled_features`）。
仓库里已经有 `_platform_admin_headers()` 工具函数（同文件第 17–25 行）可直接复用：

```python
feature_resp = await client.patch(
    f"/api/v1/tenants/{tid}",
    json={"enabled_features": {"channel_store": True}},
    headers=_platform_admin_headers(),  # platform cookie，不是租户 Bearer
)
```

### 类 B — ops：用 Bearer 模拟平台 principal（3 项：#9–#11）

**根因**

`tests/test_api/test_ops_launch.py::setup_tenant`（第 60–66 行）造了一个「ops token」，
用 **JWT Bearer** 承载 `platform_admin` 角色：

```python
ops_token = create_access_token(
    "00000000-0000-0000-0000-000000000000",  # tenant_id
    "00000000-0000-0000-0000-000000000001",  # account_id
    "platform_admin",
    tenant_type="platform",
)
return tid, ..., {"Authorization": f"Bearer {ops_token}"}
```

但平台后台是 **独立认证边界、cookie-only**（见 AGENTS.md「Platform 使用
`platform_access_token`」和 `app/middleware/tenant.py:67-82`）。`TenantScopeMiddleware`
只在「路径是 `/api/v1/ops` 且带了 `platform_access_token` cookie」时才走平台 cookie 认证
分支（第 79 行）。这里只有 Bearer、没有 cookie，所以请求落到普通 JWT 分支，把平台 principal
当成租户账户去查 → `_load_account_access` 找不到这个 account → 401。

**对比**：同文件第 15–23 行的 `_platform_admin_headers()` 用的就是正确的 cookie 形式
（`platform_access_token=...; platform_csrf_token=...` + `X-Platform-CSRF` + `Origin`）。
ops_launch 的 fixture 只是没复用它。

**建议修法（给 ops 模块负责人）**

把 `ops_headers` 改成复用 `_platform_admin_headers()`：

```python
return tid, {"Authorization": f"Bearer {tenant_token}"}, _platform_admin_headers()
```

注意 `get_ops_user`（`app/core/dependencies.py:66-73`）对平台 principal 有四元组强校验：
`role==platform_admin` + `account_id=="platform-admin"` + `tenant_id=="platform"` +
`auth_method=="platform_cookie"`。`_platform_admin_headers()` 造的 token 正好满足前三项，
cookie 分支会设上第四项。

### 类 C — launch：agency 授权缺 agency tenant 行（1 项：#8）

**根因**

`tests/test_api/test_launch_releases.py::test_agency_publish_requires_explicit_release_scope`
（第 129–148 行）直接往 DB 塞了一条 `AgencyAuthorization`，但 **没有建它引用的 agency
租户行**（`agency_tenant_id = 00000000-...0777`）：

```python
authorization = AgencyAuthorization(
    agency_tenant_id=agency_tenant_id,   # 这个 tenant 行不存在
    client_tenant_id=client_tenant_id,   # launch_facts 建了
    scope=["pages"],
    status=AgencyAuthStatus.active,
    granted_by=brand_account_id,
)
```

中间件 `_load_acting_authorization`（`app/middleware/tenant.py:535-541`）会校验 **agency
租户和 client 租户都必须是 `TenantStatus.active`**：

```python
statuses = dict(... Tenant.id.in_([agency_id, client_id]) ...)
if statuses.get(agency_id) != TenantStatus.active or statuses.get(client_id) != TenantStatus.active:
    return None   # → 中间件第 186 行返回 403「代运营授权已失效」
```

`statuses.get(agency_id)` 是 `None`（行不存在）≠ `active` → 返回 `None` → 403。
测试期望 201。

注意：路径白名单本身是对的（`_acting_path_is_explicitly_supported` 第 464 行放行
`/api/v1/ops/launch-releases` + `pages` scope），问题纯粹是 tenant 行缺失。

**建议修法（给 launch 模块负责人）**

在塞 `AgencyAuthorization` 之前，先建一个 `agency_tenant_id` 对应的 active `Tenant`
（以及对应的 agency 账户，否则 `_load_account_access` 也会拒）：

```python
db.add(Tenant(id=agency_tenant_id, name="测试代运营", slug="agency-test", tenant_type="agency", status=TenantStatus.active))
db.add(Account(tenant_id=agency_tenant_id, id=agency_account_id, ...))
await db.flush()
db.add(authorization)
```

## 为什么这些不算安全审查的回归

- 安全审查的 9 个已确认修补 + 4 个 board 决策修补，改的是 `auth_rbac` / `database.py`
  / `password.py` / `tenant.py`（中间件）/ `redpacket.py` / `open_api.py` /
  `benefit_claims.py` / `risk_*.py` / `files.py`。
- 这 11 个失败所在的 `channels.py` / `ops.py` / `ops_launch_releases.py` 路由逻辑，
  审查 **没有触碰**。
- Apples-to-apples 基线对比：在 `9001e6cc`（含审查全部修补）和 pristine `b69b3563`
  上跑同一测试范围，FAILED 集合 **完全相同**（37 == 37，`diff` 为空）。

## 优先级建议

- 类 A（channel，7 项）：改 fixture 一行即可解锁 7 个测试，性价比最高，建议先做。
- 类 B（ops，3 项）：同上，换 header 即可。
- 类 C（launch，1 项）：补两条种子行即可。

三类都是 fixture-only 修复，不涉及生产代码变更，风险极低。修完后建议在 CI 里把这 11
项从「已知预存在失败」基线里移除（不要再用 skip/xfollow 掩盖，要让它们重新成为契约）。
