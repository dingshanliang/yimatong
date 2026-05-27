# API 草案

## 1. API 设计原则

- REST API 优先。
- 所有后台 API 需要认证和 tenant scope。
- 消费者扫码 API 要支持匿名访问，但权益领取、留资、积分需要授权。
- 重要写操作支持幂等键 `Idempotency-Key`。
- 外部 Webhook 推送采用签名校验。
- API 返回中不暴露自增 ID 和敏感字段。

## 2. 认证与租户

```http
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET  /api/v1/auth/me
GET  /api/v1/tenants/current
POST /api/v1/platform/tenants
PATCH /api/v1/platform/tenants/{tenant_id}
```

## 3. 产品资料

```http
GET    /api/v1/products
POST   /api/v1/products
GET    /api/v1/products/{product_id}
PATCH  /api/v1/products/{product_id}
DELETE /api/v1/products/{product_id}

GET    /api/v1/products/{product_id}/skus
POST   /api/v1/products/{product_id}/skus
GET    /api/v1/skus/{sku_id}/batches
POST   /api/v1/skus/{sku_id}/batches

POST   /api/v1/imports/products
POST   /api/v1/imports/batches
```

## 4. 素材库

```http
POST   /api/v1/assets/upload
GET    /api/v1/assets
PATCH  /api/v1/assets/{asset_id}
DELETE /api/v1/assets/{asset_id}
POST   /api/v1/assets/{asset_id}/ai-extract
```

## 5. 码管理

```http
POST   /api/v1/code-batches
GET    /api/v1/code-batches
GET    /api/v1/code-batches/{code_batch_id}
PATCH  /api/v1/code-batches/{code_batch_id}
POST   /api/v1/code-batches/{code_batch_id}/export
POST   /api/v1/code-batches/{code_batch_id}/activate
POST   /api/v1/code-batches/{code_batch_id}/freeze
POST   /api/v1/code-batches/{code_batch_id}/void

GET    /api/v1/code-items/{public_id}
PATCH  /api/v1/code-items/{code_item_id}
POST   /api/v1/imports/existing-codes
POST   /api/v1/code-takeover/domain
```

## 6. 扫码解析与消费者 H5

```http
GET    /c/{public_id}
GET    /api/v1/public/resolve/{public_id}
POST   /api/v1/public/events
GET    /api/v1/public/pages/{page_version_id}
POST   /api/v1/public/benefits/{benefit_id}/claim
POST   /api/v1/public/leads
POST   /api/v1/public/consents
POST   /api/v1/public/consents/{consent_id}/withdraw
```

## 7. 页面引擎

```http
GET    /api/v1/page-templates
POST   /api/v1/page-templates
GET    /api/v1/pages
POST   /api/v1/pages
GET    /api/v1/pages/{page_id}
POST   /api/v1/pages/{page_id}/versions
GET    /api/v1/page-versions/{version_id}
PATCH  /api/v1/page-versions/{version_id}
POST   /api/v1/page-versions/{version_id}/preview
POST   /api/v1/page-versions/{version_id}/publish
POST   /api/v1/page-versions/{version_id}/offline
POST   /api/v1/page-versions/{version_id}/rollback
```

## 8. 活动与权益

```http
GET    /api/v1/campaigns
POST   /api/v1/campaigns
GET    /api/v1/campaigns/{campaign_id}
PATCH  /api/v1/campaigns/{campaign_id}
POST   /api/v1/campaigns/{campaign_id}/start
POST   /api/v1/campaigns/{campaign_id}/pause
POST   /api/v1/campaigns/{campaign_id}/end

GET    /api/v1/benefits
POST   /api/v1/benefits
PATCH  /api/v1/benefits/{benefit_id}
POST   /api/v1/benefits/{benefit_id}/code-pool/import
GET    /api/v1/benefit-claims
POST   /api/v1/benefit-claims/{claim_id}/verify
```

## 9. 私域承接与外部权益

```http
GET    /api/v1/private-domain-configs
POST   /api/v1/private-domain-configs
PATCH  /api/v1/private-domain-configs/{config_id}

GET    /api/v1/connectors
POST   /api/v1/connectors
POST   /api/v1/connectors/{connector_id}/test
POST   /api/v1/connectors/{connector_id}/sync-coupon
POST   /api/v1/connectors/{connector_id}/send-coupon
POST   /api/v1/connectors/{connector_id}/sync-orders
```

## 10. 数据分析

```http
GET /api/v1/analytics/business-dashboard
GET /api/v1/analytics/campaign-dashboard
GET /api/v1/analytics/risk-dashboard
GET /api/v1/analytics/regional-dashboard
GET /api/v1/analytics/funnel
GET /api/v1/analytics/exports
POST /api/v1/analytics/exports
POST /api/v1/imports/conversions
```

## 11. 风控

```http
GET   /api/v1/risk/alerts
PATCH /api/v1/risk/alerts/{alert_id}
GET   /api/v1/risk/rules
POST  /api/v1/risk/rules
PATCH /api/v1/risk/rules/{rule_id}
POST  /api/v1/risk/evaluate
```

## 12. Webhook

### 12.1 Webhook 事件类型

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

### 12.2 Webhook Payload 示例

```json
{
  "event_id": "evt_01HY...",
  "event_type": "benefit.claimed",
  "tenant_id": "ten_001",
  "occurred_at": "2026-05-26T10:00:00+08:00",
  "data": {
    "consumer_id": "con_001",
    "code_public_id": "8F3K9Q2X",
    "campaign_id": "cam_001",
    "benefit_id": "ben_001",
    "claim_id": "clm_001"
  },
  "signature": "sha256=..."
}
```
