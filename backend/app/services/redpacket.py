"""现金红包服务 — 已废弃。

红包逻辑已整合到 Benefit 体系：
- 金额计算 → app.services.redpacket_amount
- 发放 → app.services.connectors.wechat_pay_transfer
- 领取 → app.api.v1.benefit_claims
"""
