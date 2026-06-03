---
status: active
last_verified: 2026-06-03
accuracy: high
---
# EPIC-23 现金红包插件 — 实施计划

> Issue: yimatong-f4c
> 日期: 2026-05-30
> 状态: 待实施

## 设计决策摘要

| # | 决策 | 选择 |
|---|------|------|
| 1 | 合规路径 | 方案 A：每个租户用自己的微信支付商户号 |
| 2 | 集成方式 | `benefit_type = "cash_red_packet"` 接入 Benefit 体系 |
| 3 | OpenID 获取 | H5 内嵌微信 OAuth（`snsapi_base`），点击领取时触发 |
| 4 | 部署模式 | 多租户 SaaS，非私有化 |
| 5 | 功能开关 | Tenant 加 `enabled_features` JSON 列 |
| 6 | 发放模式 | 即时到账，无提现流程 |
| 7 | 金额策略 | 固定 + 随机 + 拼手气（限量 N 份，二倍均值法） |
| 8 | KYC | 不做 |
| 9 | 凭证存储 | 单个 Connector（`wechat_pay_transfer`） |
| 10 | 消费者身份 | 新建 `consumer_profiles` 表 |
| 11 | 并发安全 | 乐观锁（原子 UPDATE） |
| 12 | 失败处理 | 先 claim 再 transfer，BenefitDelivery 状态机兜底 |

## 实施任务

### Phase 1：数据模型 & 迁移（后端基础）

#### 1.1 清理现有红包骨架
- **删除** `backend/app/models/redpacket.py` 中的 `KYCRecord`、`Withdrawal`、`RedPacketClaim`
- **删除** `backend/app/services/redpacket.py` 中对应的服务函数
- **删除** `backend/app/api/v1/redpacket.py` 中独立路由
- **从** `main.py` 中移除 `redpacket_router` 注册
- **保留** `RedPacketRule` 暂不动（后续迁移后删除）

#### 1.2 Tenant 模型变更
- `backend/app/models/tenant.py`：添加 `enabled_features: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)`
- 新建 Alembic 迁移

#### 1.3 ConsumerProfile 模型
- 新建 `backend/app/models/consumer.py`：
  ```python
  class ConsumerProfile(Base):
      __tablename__ = "consumer_profiles"
      id: Mapped[uuid.UUID]          # PK, uuid7
      tenant_id: Mapped[uuid.UUID]   # 租户 ID
      wechat_openid: Mapped[str]     # 租户 appid 下的 OpenID
      phone_hash: Mapped[str | None] # 手机号哈希（可选）
      created_at: Mapped[datetime]
      # 唯一约束：tenant_id + wechat_openid
      # 索引：tenant_id
  ```
- 新建 Alembic 迁移

#### 1.4 BenefitType 扩展
- `backend/app/models/campaign.py`：`BenefitType` 添加 `CASH_RED_PACKET = "cash_red_packet"`

### Phase 2：WeChat Pay Connector 适配器

#### 2.1 适配器实现
- 新建 `backend/app/services/connectors/wechat_pay_transfer.py`
  - `sync_stock()`: 从 config_json 读取预算，对比 claimed_budget 返回剩余
  - `deliver()`: 调用微信支付 V3 转账 API
    - 构建 `POST /v3/fund-app/mch-transfer/transfer-bills/transfer`
    - 场景：`cash_marketing`（现金营销）
    - 参数：appid, openid, amount, transfer_scene_id, out_bill_no
    - 签名：HTTP Authorization header（SHA256-RSA2048）
  - `parse_callback()`: 解析微信转账结果回调
  - `validate_config()`: 校验 mch_id, appid, 证书等配置完整性
  - `verify_callback()`: 验证微信回调签名

#### 2.2 微信支付 V3 签名工具
- 新建 `backend/app/utils/wechat_pay.py`
  - 生成 Authorization header（SHA256-RSA2048 签名）
  - 验证回调签名
  - 证书加载（从 Connector encrypted_config 解密后加载）

#### 2.3 注册适配器
- `backend/app/services/connectors/registry.py`：注册 `wechat_pay_transfer` 类型

### Phase 3：红包业务逻辑

#### 3.1 金额计算服务
- 新建 `backend/app/services/redpacket_amount.py`
  - `calc_fixed(config) -> int`: 返回固定金额
  - `calc_random(config) -> int`: `random.randint(min, max)`
  - `calc_lucky(config, remaining_budget, remaining_count) -> int`: 二倍均值法
  - `validate_config(config)`: 校验金额范围（单笔 ≤ 200元，min ≥ 0.1元 等）

#### 3.2 BenefitClaim 流程改造
- 修改 `backend/app/api/v1/benefit_claims.py`
  - 检测 benefit_type == `cash_red_packet`
  - 检查 `tenant.enabled_features.get("cash_red_packet")`
  - 检查 consumer 是否有 OpenID（ConsumerProfile）
  - 无 OpenID → 返回 `403 require_wechat_auth`，附带 OAuth URL
  - 有 OpenID → 执行 claim + 乐观锁扣预算 + 计算金额 + 调 deliver

#### 3.3 OAuth 流程
- 新建 `backend/app/api/v1/wechat_oauth.py`
  - `GET /api/v1/wechat/auth-url?benefit_id=xxx&scan_token=yyy`
    - 生成微信 OAuth URL（`snsapi_base`）
    - state 参数编码 `{benefit_id, scan_token}`（AES 加密或 JWT）
  - `GET /api/v1/wechat/oauth-callback?code=xxx&state=yyy`
    - 用租户的 OA appid/appsecret 换 access_token + openid
    - 创建/查找 ConsumerProfile
    - 自动执行 claim + 微信转账
    - 302 重定向回 H5 结果页

#### 3.4 乐观锁预算扣减
- 修改 `backend/app/services/campaign.py` 的 claim 逻辑
  - 红包类型使用原子 UPDATE 扣减 config_json 中的 claimed_budget
  - 拼手气类型额外原子扣减 stock_total（剩余份数）

### Phase 4：Admin 前端

#### 4.1 微信凭证配置页
- Admin 新增 Connector 配置页面
  - 类型选择 `wechat_pay_transfer`
  - 表单：OA appid, OA appsecret, mch_id, APIv3 key
  - 证书上传（pem 文件）
  - 配置校验（调 validate_config）

#### 4.2 红包权益创建
- Benefit 创建/编辑页面增加 `cash_red_packet` 类型
  - 金额模式选择：固定 / 随机 / 拼手气
  - 条件表单：根据模式显示对应配置字段
  - 预算、限领次数等通用字段
  - 关联已配置的 wechat_pay_transfer Connector

#### 4.3 租户功能开关
- 租户管理页面增加"已开通功能"编辑
  - `cash_red_packet` 开关
  - 仅超级管理员可操作

### Phase 5：H5 前端

#### 5.1 OAuth 跳转处理
- 红包领取按钮点击逻辑：
  - 正常权益 → 直接 POST /benefit-claims
  - 红包权益 → 先调 `/api/v1/wechat/auth-url` → 跳转 OAuth
- OAuth 回调结果页展示

#### 5.2 红包领取结果 UI
- 成功：金额展示 + "已到微信零钱"
- 处理中：loading 状态
- 失败：错误提示 + 重试按钮

### Phase 6：测试

#### 6.1 单元测试
- 金额计算：固定/随机/拼手气算法
- 配置校验：边界值、微信限额
- 乐观锁：并发扣减场景
- OAuth state 编解码

#### 6.2 集成测试
- Connector 适配器：mock 微信 API
- 完整 claim 流程：claim → deliver → delivery status
- OAuth 流程：auth-url → callback → consumer profile

#### 6.3 端到端测试（Playwright）
- H5 红包领取完整流程
- Admin 红包创建 → H5 领取

## config_json Schema

### Benefit.config_json（红包配置）

```json
{
  "amount_type": "fixed",              // "fixed" | "random" | "lucky"
  "fixed_amount": 188,                 // 固定金额（分），amount_type=fixed 时必填
  "min_amount": 30,                    // 随机最小值（分），amount_type=random 时必填
  "max_amount": 300,                   // 随机最大值（分），amount_type=random 时必填
  "lucky_total_count": 20,            // 拼手气总份数，amount_type=lucky 时必填
  "lucky_min_per": 10,                // 拼手气每份最少（分）
  "budget": 10000,                     // 总预算（分）
  "claimed_budget": 0,                 // 已发放预算（分），系统维护
  "daily_limit_per_user": 3,          // 每人每日限领
  "total_limit_per_user": 10,         // 每人总限领
  "transfer_remark": "扫码领红包"       // 微信转账备注（可选）
}
```

### Connector.encrypted_config（解密后）

```json
{
  "oa_appid": "wx1234567890",
  "oa_appsecret": "xxxxxxxx",
  "mch_id": "1600000001",
  "api_v3_key": "xxxxxxxx",
  "cert_serial_no": "xxxxxxxx",
  "cert_private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
  "cert_public_cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"
}
```

## 微信支付 V3 转账 API 参考

- **接口**: `POST https://api.mch.weixin.qq.com/v3/fund-app/mch-transfer/transfer-bills/transfer`
- **场景**: `cash_marketing`（现金营销，单笔上限 200 元）
- **签名**: HTTP Authorization, SHA256-RSA2048
- **认证**: 商户 API 证书
- **回调**: 转账结果通知（需配置回调 URL）
- **查询**: `GET /v3/fund-app/mch-transfer/transfer-bills/transfer-detail/{detail_id}`

## 风险 & 注意事项

1. **微信支付商户证书安全**：必须加密存储，传输走 TLS
2. **幂等控制**：`out_bill_no` 用 claim_id 保证幂等
3. **回调幂等**：微信可能重复推送回调，需按 out_bill_no 去重
4. **超时处理**：转账 API 超时后需异步查询，不能重复发
5. **金额单位**：微信支付用"分"，前端显示用"元"，注意转换
6. **预算超卖**：乐观锁 UPDATE affected rows = 0 时必须回滚 claim
7. **租户未配置**：未开通 cash_red_packet 或未配 Connector 时，前端应隐藏或禁用红包选项
