---
status: active
last_verified: 2026-06-03
accuracy: high
---
# 埋点事件字典

## 1. 事件设计原则

- 事件采用追加写入，不覆盖历史。
- 所有事件必须包含 tenant_id、event_time、source、trace_id。
- 消费者端事件尽量记录 anonymous_id，授权后关联 consumer_id。
- 渠道归因字段统一：product_id、sku_id、batch_id、code_id、campaign_id、channel_id、distributor_id、store_id、guide_id、utm_source、utm_medium、utm_campaign。
- 涉及个人信息的字段必须最小化，并遵守授权记录。

## 2. 通用字段

| 字段 | 说明 |
|---|---|
| event_id | 事件 ID |
| event_name | 事件名称 |
| tenant_id | 租户 |
| occurred_at | 发生时间 |
| code_public_id | 对外码 ID |
| code_batch_id | 码批次 |
| consumer_id | 消费者 ID，可空 |
| anonymous_id | 匿名 ID |
| session_id | 会话 ID |
| scan_app | 微信、支付宝、相机、浏览器、抖音等 |
| province/city | 地理位置，基于 IP 或授权定位 |
| user_agent | 设备 UA |
| page_version_id | 页面版本 |
| campaign_id | 活动 |
| channel_params | UTM/渠道参数 |

## 3. 事件列表

| 事件名 | 触发时机 | 关键属性 |
|---|---|---|
| scan.opened | 用户访问二维码短链 | code_public_id, scan_app, ip |
| scan.resolved | 码解析成功 | code_status, page_version_id, is_first_scan |
| scan.invalid | 查无此码/作废/冻结 | reason |
| page.viewed | H5 页面曝光 | page_version_id, module_count |
| module.viewed | 页面模块曝光 | module_id, module_type |
| trace.viewed | 查看溯源信息 | product_id, batch_id |
| certificate.viewed | 查看证书/检测报告 | certificate_id/report_id |
| verify.first_scan | 首次扫码验真 | first_scan_at, city |
| verify.repeat_scan | 重复扫码 | scan_count, first_scan_at |
| benefit.viewed | 权益曝光 | benefit_id, benefit_type |
| benefit.claim.clicked | 点击领取权益 | benefit_id |
| benefit.claimed | 领取成功 | benefit_id, claim_id |
| benefit.claim_failed | 领取失败 | failure_reason |
| points.awarded | 积分增加 | points, rule_id |
| points.redeemed | 积分兑换 | points, benefit_id |
| lead.form_viewed | 留资表单曝光 | form_id |
| lead.submitted | 留资提交 | form_id, fields_submitted |
| private_domain.clicked | 点击企微/公众号/小程序 | destination_type |
| ecommerce.clicked | 点击电商店铺/商品 | platform, url_id |
| external_coupon.clicked | 点击外部券链接 | platform, benefit_id |
| external_coupon.code_assigned | 发放券码池券码 | coupon_code_id |
| coupon.api_sent | API 发券成功 | platform, external_coupon_id |
| offline.verified | 线下核销 | verifier_id, store_id |
| risk.alert_created | 产生风险预警 | risk_type, risk_level |
| conversion.imported | 外部成交导入 | order_id, amount, match_rule |
| webhook.sent | Webhook 推送 | endpoint_id, status |
| consent.granted | 授权同意 | consent_type, purpose |
| consent.withdrawn | 撤回授权 | consent_type |

## 4. 漏斗指标

```text
扫码打开
→ 页面曝光
→ 权益曝光
→ 领券点击
→ 领券成功
→ 私域点击/商城跳转/留资提交
→ 核销/成交导入
→ 复购归因
```

## 5. 风控指标

```text
同码扫描次数
同码扫描城市数
同设备领取次数
同手机号领取次数
同 IP 访问次数
码预期区域 vs 实际扫码区域
活动预算消耗速度
权益库存消耗速度
```
