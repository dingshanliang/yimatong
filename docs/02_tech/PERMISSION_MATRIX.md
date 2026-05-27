# 权限矩阵

## 1. 角色定义

| 角色 | 说明 |
|---|---|
| PlatformAdmin | 平台超级管理员 |
| AgencyOperator | 代运营服务商/客户成功 |
| RegionalAdmin | 区域品牌/协会管理员 |
| BrandAdmin | 品牌方企业管理员 |
| BrandOperator | 品牌方运营人员 |
| Distributor | 经销商/渠道商 |
| StoreGuide | 门店/导购/团长 |
| PrintPartner | 印刷/标签协作方 |
| Auditor | 只读审计/管理角色 |

## 2. 权限矩阵

| 功能 | PlatformAdmin | AgencyOperator | RegionalAdmin | BrandAdmin | BrandOperator | Distributor | StoreGuide | PrintPartner | Auditor |
|---|---|---|---|---|---|---|---|---|---|
| 租户开通 | RW | R | - | - | - | - | - | - | R |
| 套餐额度 | RW | R | - | R | - | - | - | - | R |
| 品牌资料 | RW | RW* | R | RW | R | - | - | - | R |
| 产品资料 | RW | RW* | R | RW | RW | R* | - | - | R |
| 检测报告/证书 | RW | RW* | R | RW | RW | - | - | - | R |
| 码批次创建 | RW | RW* | R | RW | RW | - | - | - | R |
| 码包导出 | RW | RW* | - | RW | RW | - | - | R* | R |
| 码激活/作废 | RW | RW* | - | RW | - | - | - | - | R |
| 既有码接管 | RW | RW* | - | RW | RW | - | - | - | R |
| 页面编辑 | RW | RW* | R | RW | RW | - | - | - | R |
| 页面发布 | RW | RW* | R* | RW | - | - | - | - | R |
| 活动配置 | RW | RW* | R | RW | RW | - | - | - | R |
| 权益配置 | RW | RW* | R | RW | RW | - | - | - | R |
| 私域承接 | RW | RW* | - | RW | RW | - | - | - | R |
| 数据看板 | RW | RW* | R* | RW | R | R* | R* | - | R |
| 风控预警 | RW | RW* | R* | RW | R | R* | - | - | R |
| 渠道管理 | RW | RW* | - | RW | RW | R* | - | - | R |
| 核销 | RW | RW* | - | RW | RW | R* | RW* | - | R |
| 数据导出 | RW | RW* | R* | RW | - | - | - | - | R |
| 集成配置 | RW | RW* | - | RW | - | - | - | - | R |
| 操作日志 | RW | R | R* | R | - | - | - | - | R |

说明：

- `RW*` 表示仅限被授权客户或所属组织范围。
- `R*` 表示仅限自己负责的成员企业、区域、门店或码段。
- 第一版可先实现粗粒度权限，但数据模型必须预留细粒度权限点。

## 3. 高风险权限

以下权限需要单独控制并记录日志：

- 码批次作废。
- 码包导出。
- 页面发布/下线/回滚。
- 数据导出。
- 外部系统 API Key 配置。
- 客户自有域名配置。
- 权益库存调整。
- 风控规则关闭。
- 个人信息查看和导出。
