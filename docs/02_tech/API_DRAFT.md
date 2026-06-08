---
status: active
last_verified: 2026-06-08
accuracy: high
---

# API 参考文档

> 本文档基于代码自动审计生成（2026-06-03），反映 `backend/app/api/v1/` 下所有实际路由。

## 1. API 设计原则

- REST API 优先。
- 所有后台 API 需要认证和 tenant scope。
- 消费者扫码 API 要支持匿名访问，但权益领取、留资、积分需要授权。
- 重要写操作支持幂等键 `Idempotency-Key`。
- 外部 Webhook 推送采用签名校验。
- API 返回中不暴露自增 ID 和敏感字段。

## 2. 认证与账户

### 认证（auth.py + password.py）

```
POST   /api/v1/auth/login                    → 登录
POST   /api/v1/auth/refresh                  → 刷新 Token
GET    /api/v1/auth/me                       → 当前用户信息
POST   /api/v1/auth/logout                   → 登出
POST   /api/v1/auth/change-password          → 修改密码
POST   /api/v1/auth/reset-password           → 重置密码
POST   /api/v1/auth/generate-reset-token     → 生成重置令牌
POST   /api/v1/auth/confirm-reset-password   → 确认重置密码
GET    /api/v1/auth/reset-page               → 重置页面重定向
```

### 组织与账户（organizations.py）

```
POST   /api/v1/organizations                 → 创建组织
GET    /api/v1/organizations                 → 组织列表
POST   /api/v1/accounts                      → 创建账户
GET    /api/v1/accounts                      → 账户列表
PATCH  /api/v1/accounts/{account_id}         → 更新账户
```

### 角色与权限（roles.py）

```
GET    /api/v1/roles                         → 角色列表
POST   /api/v1/roles                         → 创建角色
GET    /api/v1/roles/permissions             → 权限列表
POST   /api/v1/roles/permissions             → 创建权限
POST   /api/v1/roles/{role_id}/permissions/{permission_id} → 分配权限
DELETE /api/v1/roles/{role_id}               → 删除角色
```

### 租户管理（tenants.py）

```
POST   /api/v1/tenants                       → 创建租户
GET    /api/v1/tenants                       → 租户列表
GET    /api/v1/tenants/me                    → 当前租户信息
PATCH  /api/v1/tenants/me                    → 更新当前租户
GET    /api/v1/tenants/{tenant_id}           → 获取指定租户
PATCH  /api/v1/tenants/{tenant_id}           → 更新指定租户
DELETE /api/v1/tenants/{tenant_id}           → 删除租户
```

## 3. 平台管理后台（platform.py）

```
POST   /api/v1/platform/auth/login           → 平台管理员登录
GET    /api/v1/platform/dashboard            → 平台概览
GET    /api/v1/platform/tenants              → 租户列表
POST   /api/v1/platform/tenants              → 创建租户
GET    /api/v1/platform/tenants/{tenant_id}  → 租户详情
PATCH  /api/v1/platform/tenants/{tenant_id}  → 更新租户
PATCH  /api/v1/platform/tenants/{tenant_id}/status → 更新租户状态
DELETE /api/v1/platform/tenants/{tenant_id}  → 删除租户
GET    /api/v1/platform/audit-logs           → 审计日志
GET    /api/v1/platform/plans                → 套餐列表
POST   /api/v1/platform/plans                → 创建套餐
PATCH  /api/v1/platform/plans/{plan_id}      → 更新套餐
POST   /api/v1/platform/tenants/{tenant_id}/assign-plan → 分配套餐
GET    /api/v1/platform/quota-usage          → 配额用量
GET    /api/v1/platform/config               → 平台配置
PATCH  /api/v1/platform/config               → 更新配置
GET    /api/v1/platform/health-overview      → 健康概览
GET    /api/v1/platform/health-tenants       → 租户健康
POST   /api/v1/platform/health/refresh       → 刷新健康数据
GET    /api/v1/platform/service-providers    → 服务商列表
```

## 4. 产品资料库（products.py）

### 品牌

```
POST   /api/v1/brands                        → 创建品牌
GET    /api/v1/brands                        → 品牌列表
PATCH  /api/v1/brands/{brand_id}             → 更新品牌
GET    /api/v1/brands/{brand_id}/products    → 品牌下产品列表
DELETE /api/v1/brands/{brand_id}             → 删除品牌
```

### 产品

```
POST   /api/v1/products                      → 创建产品
GET    /api/v1/products                      → 产品列表
GET    /api/v1/products/{product_id}         → 产品详情
PATCH  /api/v1/products/{product_id}         → 更新产品
GET    /api/v1/products/{product_id}/assets  → 产品资料列表
POST   /api/v1/products/{product_id}/assets  → 创建产品资料
GET    /api/v1/products/{product_id}/skus    → 产品下 SKU 列表
GET    /api/v1/products/{product_id}/batches → 产品下批次列表
```

### SKU

```
POST   /api/v1/skus                          → 创建 SKU
GET    /api/v1/skus                          → SKU 列表
PATCH  /api/v1/skus/{sku_id}                 → 更新 SKU
```

### 生产批次

```
POST   /api/v1/production-batches            → 创建批次
GET    /api/v1/production-batches            → 批次列表
PATCH  /api/v1/production-batches/{batch_id} → 更新批次
POST   /api/v1/production-batches/import-csv → CSV 导入
```

### 产品资料

```
PATCH  /api/v1/product-assets/{asset_id}     → 更新产品资料
DELETE /api/v1/product-assets/{asset_id}      → 删除产品资料
```

## 5. 码管理（code_batches.py）

### 码批次

> **权限要求**：创建/激活/冻结/作废 需 `code:manage`；导出 需 `code:export`；列表/详情 需登录。

```
POST   /api/v1/code-batches                  → 创建码批次 (code:generate)
GET    /api/v1/code-batches                  → 码批次列表
GET    /api/v1/code-batches/{batch_id}       → 码批次详情
PATCH  /api/v1/code-batches/{batch_id}       → 更新码批次
POST   /api/v1/code-batches/{batch_id}/activate → 激活 (code:manage)
POST   /api/v1/code-batches/{batch_id}/export   → 导出 (code:export)
POST   /api/v1/code-batches/{batch_id}/freeze   → 冻结 (code:manage)
POST   /api/v1/code-batches/{batch_id}/void      → 作废 (code:manage)
POST   /api/v1/code-batches/{batch_id}/mark-printing  → 标记印刷中 (code:manage)
POST   /api/v1/code-batches/{batch_id}/mark-delivered → 标记已交付 (code:manage)
```

### 码项

> **状态机保护**：码项 `status` 字段**禁止**通过 `PATCH /code-items/{id}` 直接修改，必须通过专用端点（`/bind`、`/revoke`、`/activate`）变更。

```
GET    /api/v1/code-items                    → 码项列表
GET    /api/v1/code-items/public/{public_id} → 按 public_id 查询
GET    /api/v1/code-items/{item_id}          → 码项详情
PATCH  /api/v1/code-items/{item_id}          → 更新码项（禁止直接改 status）
GET    /api/v1/code-items/{item_id}/pair     → 获取配对码
POST   /api/v1/code-items/{item_id}/revoke   → 撤销码项 (code:manage)
POST   /api/v1/code-items/{item_id}/bind     → 绑定码项 (code:manage)
```

## 6. 码解析与消费者 H5（resolver.py + scan_events.py + consents.py + consumers.py + public_pages.py）

```
GET    /c/{public_id}                          → 码解析入口（短链）
POST   /scan-events                            → 写入扫码事件
POST   /api/v1/public/consents                 → 提交授权
POST   /api/v1/public/consents/{consent_id}/withdraw → 撤回授权
POST   /api/v1/consumers/lead-capture          → 留资提交
GET    /api/v1/consumers/me                    → 消费者信息
GET    /api/v1/consumers/points/me             → 消费者积分
GET    /api/v1/consumers/points/transactions   → 积分流水
GET    /api/v1/consumers/points/products       → 积分商品
POST   /api/v1/consumers/points/exchanges      → 积分兑换
```

## 7. 页面引擎（page_templates.py）

### 页面模板

```
GET    /api/v1/page-templates/industry-templates → 行业模板列表
POST   /api/v1/page-templates/industry-templates/{index}/clone → 克隆行业模板
POST   /api/v1/page-templates                → 创建页面模板
GET    /api/v1/page-templates                → 页面模板列表
GET    /api/v1/page-templates/{template_id}  → 页面模板详情
PATCH  /api/v1/page-templates/{template_id}  → 更新页面模板
DELETE /api/v1/page-templates/{template_id}  → 删除页面模板
GET    /api/v1/page-templates/{template_id}/preview → 预览
POST   /api/v1/page-templates/{template_id}/versions → 创建页面版本
GET    /api/v1/page-templates/{template_id}/versions → 版本列表
POST   /api/v1/page-templates/{template_id}/versions/{version_id}/rollback → 回滚
```

### 页面版本

```
PATCH  /api/v1/page-versions/{version_id}    → 更新版本
POST   /api/v1/page-versions/{version_id}/publish → 发布
POST   /api/v1/page-versions/{version_id}/archive → 归档
```

## 8. 活动与权益（campaigns.py + benefits.py + benefit_claims.py）

### 活动

```
POST   /api/v1/campaigns                     → 创建活动
GET    /api/v1/campaigns                     → 活动列表
GET    /api/v1/campaigns/{campaign_id}       → 活动详情
PATCH  /api/v1/campaigns/{campaign_id}       → 更新活动
POST   /api/v1/campaigns/{campaign_id}/status → 修改活动状态
DELETE /api/v1/campaigns/{campaign_id}       → 删除活动
POST   /api/v1/campaigns/{campaign_id}/benefits → 创建权益
POST   /api/v1/campaigns/{campaign_id}/benefits/{benefit_id}/attach → 关联权益
DELETE /api/v1/campaigns/{campaign_id}/benefits/{benefit_id}/attach → 取消关联
GET    /api/v1/campaigns/{campaign_id}/benefits → 活动权益列表
POST   /api/v1/campaigns/benefits/{benefit_id}/claim → 领取权益
GET    /api/v1/campaigns/analytics/funnel    → 漏斗分析
GET    /api/v1/campaigns/analytics/comparison → 活动对比
```

### 权益

```
POST   /api/v1/benefits                      → 创建权益
GET    /api/v1/benefits                      → 权益列表
GET    /api/v1/benefits/summary              → 权益概览
GET    /api/v1/benefits/admin/claims         → 领取记录管理
GET    /api/v1/benefits/{benefit_id}         → 权益详情
PATCH  /api/v1/benefits/{benefit_id}         → 更新权益
DELETE /api/v1/benefits/{benefit_id}         → 删除权益
```

### 权益领取

```
POST   /api/v1/benefit-claims                → 领取权益
```

## 9. 私域承接与外部权益（private_domain.py + connectors.py）

### 私域配置

```
GET    /api/v1/private-domain-configs        → 私域配置列表
POST   /api/v1/private-domain-configs        → 创建配置
PATCH  /api/v1/private-domain-configs/{config_id} → 更新配置
```

### 连接器与券码池

```
GET    /api/v1/connectors/coupon-pools       → 券码池列表
POST   /api/v1/connectors/coupon-pools       → 创建券码池
GET    /api/v1/connectors/coupon-pools/{pool_id}/codes → 池内码列表
POST   /api/v1/connectors/coupon-pools/{pool_id}/distribute → 分发
POST   /api/v1/connectors/connectors         → 创建连接器
GET    /api/v1/connectors/connectors         → 连接器列表
GET    /api/v1/connectors/connectors/types   → 连接器类型
GET    /api/v1/connectors/connectors/{conn_id} → 连接器详情
POST   /api/v1/connectors/connectors/{conn_id}/test → 测试连接
PATCH  /api/v1/connectors/connectors/{conn_id} → 更新连接器
POST   /api/v1/connectors/connectors/{conn_id}/sync-stock → 同步库存
POST   /api/v1/connectors/connectors/{conn_id}/deliver → 发券
POST   /api/v1/connectors/connectors/{conn_id}/callback → 回调
POST   /api/v1/connectors/deliveries/{delivery_id}/retry → 重试
GET    /api/v1/connectors/deliveries/pending-retries → 待重试列表
GET    /api/v1/connectors/deliveries/{delivery_id} → 投递详情
```

## 10. 数据分析（analytics.py + analytics_dashboard.py + channel_analytics.py）

### 基础统计

```
GET    /api/v1/analytics/scan-stats          → 扫码统计
GET    /api/v1/analytics/code-stats          → 码统计
GET    /api/v1/analytics/dashboard           → 仪表盘
GET    /api/v1/analytics/campaign-scan-stats → 活动扫码统计
```

### 仪表板

```
GET    /api/v1/analytics/campaign-dashboard  → 活动看板
GET    /api/v1/analytics/risk-dashboard      → 风控看板
GET    /api/v1/analytics/regional-dashboard  → 区域看板
GET    /api/v1/analytics/exports             → 导出列表
POST   /api/v1/analytics/exports             → 创建导出
```

### 渠道分析

```
GET    /api/v1/channel-analytics/scan-by-channel → 渠道扫码统计
GET    /api/v1/channel-analytics/health-scores   → 健康评分
GET    /api/v1/channel-analytics/conversion-comparison → 转化对比
```

## 11. 风控（risk.py + risk_evaluate.py + risk_rules.py + risk_dashboard.py + risk_notifications.py）

```
GET    /api/v1/risk-alerts                   → 风控告警列表
POST   /api/v1/risk-alerts/{alert_id}/resolve → 处理告警
POST   /api/v1/risk-alerts/code-items/{item_id}/freeze → 冻结码
POST   /api/v1/risk-alerts/code-items/{item_id}/unfreeze → 解冻码
POST   /api/v1/risk/evaluate                 → 风控评估
GET    /api/v1/risk-rules                    → 风控规则列表
POST   /api/v1/risk-rules                    → 创建规则（含 CRUD，共 9 路由）
GET    /api/v1/risk-dashboard                → 风控看板
GET    /api/v1/risk-notifications            → 风控通知
```

## 12. 渠道与区域（channels.py + regional.py）

### 渠道管理

```
GET    /api/v1/channels/overview             → 渠道概览
GET    /api/v1/channels/distributors         → 经销商列表
GET    /api/v1/channels/regions              → 区域列表
GET    /api/v1/channels/stores               → 门店列表
DELETE /api/v1/channels/stores/{store_id}    → 删除门店
GET    /api/v1/channels/code-allocations     → 码流向登记列表
GET    /api/v1/channels/code-items/{public_id}/store → 扫码查门店
GET    /api/v1/channels/account-scopes       → 账号渠道范围
DELETE /api/v1/channels/account-scopes/{scope_id} → 删除渠道范围
GET    /api/v1/channels/portal/distributor/summary → 经销商入口
GET    /api/v1/channels/portal/store/summary → 门店入口
GET    /api/v1/channels/diversion-clues      → 窜货线索
（共 24 个路由，以上为主要路由）
```

### 区域品牌/协会（regional.py）

```
GET    /api/v1/regional/orgs                 → 组织列表
GET    /api/v1/regional/orgs/{org_id}        → 组织详情
GET    /api/v1/regional/orgs/{org_id}/members → 成员列表
DELETE /api/v1/regional/orgs/{org_id}/members/{member_id} → 移除成员
GET    /api/v1/regional/orgs/{org_id}/templates → 模板列表
GET    /api/v1/regional/orgs/{org_id}/dashboard → 汇总看板
GET    /api/v1/regional/orgs/{org_id}/code-rules → 码规则
GET    /api/v1/regional/orgs/{org_id}/advanced-dashboard → 高级看板
GET    /api/v1/regional/orgs/{org_id}/whitelabel → 白标配置
GET    /api/v1/regional/orgs/{org_id}/unified-campaigns → 统一活动
GET    /api/v1/regional/orgs/{org_id}/data-policy → 数据隔离策略
GET    /api/v1/regional/orgs/{org_id}/domains → 域名列表
DELETE /api/v1/regional/orgs/{org_id}/domains/{domain_id} → 删除域名
（共 27 个路由，以上为主要路由）
```

## 13. 会员与积分（members.py）

```
GET    /api/v1/members/overview              → 会员积分概览
POST   /api/v1/members/consumers             → 创建消费者
GET    /api/v1/members/consumers/search      → 搜索消费者
GET    /api/v1/members/consumers/{consumer_id} → 消费者详情
POST   /api/v1/members/points/award          → 发放积分
POST   /api/v1/members/points/spend          → 消费积分
GET    /api/v1/members/consumers/{consumer_id}/transactions → 积分流水
GET    /api/v1/members/point-rules           → 积分规则列表
POST   /api/v1/members/point-rules           → 创建积分规则
DELETE /api/v1/members/point-rules/{rule_id} → 删除积分规则
GET    /api/v1/members/point-redemptions     → 积分兑换记录
GET    /api/v1/members/point-products        → 积分商品列表
POST   /api/v1/members/point-products        → 创建积分商品
DELETE /api/v1/members/point-products/{product_id} → 删除积分商品
POST   /api/v1/members/point-products/exchange → 积分兑换
（共 17 个路由）
```

## 14. GMV 归因（gmv.py）

```
POST   /api/v1/gmv/orders/import             → 导入订单
GET    /api/v1/gmv/orders                    → 订单列表
POST   /api/v1/gmv/auto-attribution          → 批量自动归因
GET    /api/v1/gmv/dashboard                 → GMV 归因看板
GET    /api/v1/gmv/roi                       → ROI 报表
GET    /api/v1/gmv/attributions              → 归因记录列表
POST   /api/v1/gmv/aggregate-daily           → 手动触发日统计聚合
```

## 15. 代运营工作台（ops.py + agency_auth.py）

### 工作台

```
GET    /api/v1/ops/overview                  → 工作台概览
GET    /api/v1/ops/workbench                 → 工作台聚合数据
GET    /api/v1/ops/clients/{tenant_id}/status → 客户状态
GET    /api/v1/ops/clients/{tenant_id}/launch-checklist → 上线检查清单
POST   /api/v1/ops/tasks                     → 创建任务
GET    /api/v1/ops/tasks                     → 任务列表
GET    /api/v1/ops/tasks/{task_id}           → 任务详情
PATCH  /api/v1/ops/tasks/{task_id}           → 更新任务
DELETE /api/v1/ops/tasks/{task_id}           → 删除任务
```

### 代理授权

```
GET    /api/v1/ops/authorizations            → 授权列表
POST   /api/v1/ops/authorizations            → 创建授权
DELETE /api/v1/ops/authorizations/{auth_id}  → 撤销授权
POST   /api/v1/agency/switch-context         → 切换客户上下文
POST   /api/v1/agency/exit-context           → 退出客户上下文
```

## 16. Webhook 管理（webhooks.py）

```
POST   /api/v1/webhooks/endpoints            → 创建端点
GET    /api/v1/webhooks/endpoints            → 端点列表
PATCH  /api/v1/webhooks/endpoints/{endpoint_id} → 更新端点
DELETE /api/v1/webhooks/endpoints/{endpoint_id} → 删除端点
POST   /api/v1/webhooks/api-keys             → 创建 API Key
GET    /api/v1/webhooks/api-keys             → API Key 列表
DELETE /api/v1/webhooks/api-keys/{key_id}    → 删除 API Key
GET    /api/v1/webhooks/deliveries           → 投递列表
GET    /api/v1/webhooks/deliveries/{delivery_id} → 投递详情
```

### Webhook 事件类型

- scan.created
- page.viewed
- benefit.claimed
- lead.submitted
- private_domain.clicked
- external_shop.clicked
- benefit.verified
- points.changed
- risk_alert.created
- conversion.imported

## 17. AI 能力（ai.py）

```
POST   /api/v1/ai/copywriting                → AI 文案生成
POST   /api/v1/ai/extract                    → AI 信息提取
POST   /api/v1/ai/recognize-image            → AI 图片识别
POST   /api/v1/ai/recognize-image-upload     → AI 图片识别（上传）
POST   /api/v1/ai/page-copy                  → AI 页面文案
POST   /api/v1/ai/page-suggest               → AI 页面建议
POST   /api/v1/ai/campaign                   → AI 活动建议
```

## 18. 其他模块

### 文件管理（files.py）

```
POST   /api/v1/files/upload                  → 上传文件
GET    /api/v1/files/public/{file_path:path} → 公开访问文件
GET    /api/v1/files/{file_id}               → 获取文件
```

### 行业模板（industry_templates.py）

```
GET    /api/v1/industry-templates            → 模板列表
GET    /api/v1/industry-templates/{template_id} → 模板详情
POST   /api/v1/industry-templates/{template_id}/apply → 应用模板
```

### 国际化（i18n.py）

```
POST   /api/v1/i18n/translations             → 创建翻译
GET    /api/v1/i18n/translations             → 翻译列表
DELETE /api/v1/i18n/translations/{translation_id} → 删除翻译
POST   /api/v1/i18n/translations/batch       → 批量翻译
POST   /api/v1/i18n/detect                   → 语言检测
```

### 外部集成（integration.py）

```
POST   /api/v1/integration/batch-import      → 批量导入
POST   /api/v1/integration/erp/inventory-sync → ERP 库存同步
POST   /api/v1/integration/crm/customer-sync → CRM 客户同步
```

### 微信 OAuth（wechat_oauth.py）

```
GET    /api/v1/wechat/auth-url               → 获取授权 URL
GET    /api/v1/wechat/oauth-callback         → OAuth 回调
```

### 企微集成（wecom_integrations.py）

```
GET    /api/v1/integrations/wecom            → 读取配置
POST   /api/v1/integrations/wecom            → 保存配置
POST   /api/v1/integrations/wecom/verify     → 检测接入
GET    /api/v1/integrations/wecom/members    → 可添加成员
POST   /api/v1/integrations/wecom/contact-way → 添加入口
GET    /api/v1/integrations/wecom/callback/{connector_id} → URL 验证
POST   /api/v1/integrations/wecom/callback/{connector_id} → 客户事件
GET    /api/v1/integrations/wecom/mock-added → 本地模拟
```

### Open API（open_api.py）

```
GET    /open/v1/scans                        → 扫码列表
GET    /open/v1/scans/{scan_id}              → 扫码详情
GET    /open/v1/consumers                    → 消费者列表
GET    /open/v1/consumers/{consumer_id}      → 消费者详情
GET    /open/v1/claims                       → 领取列表
GET    /open/v1/events                       → 事件列表
POST   /open/v1/coupons/issue                → 发券
POST   /open/v1/coupons/{coupon_id}/redeem   → 核销券
PATCH  /open/v1/campaigns/{campaign_id}/status → 更新活动状态
GET    /open/v1/products                     → 产品列表
POST   /open/v1/products                     → 创建产品
PATCH  /open/v1/products/{product_id}        → 更新产品
GET    /open/v1/skus                         → SKU 列表
POST   /open/v1/skus                         → 创建 SKU
PATCH  /open/v1/skus/{sku_id}                → 更新 SKU
GET    /open/v1/batches                      → 批次列表
POST   /open/v1/batches                      → 创建批次
```

### 任务管理（tasks.py）

```
GET    /api/v1/tasks                         → 任务列表
（轻量任务模块）
```
