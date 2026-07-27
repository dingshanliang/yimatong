# 企业微信“确认加企微”的官方能力边界

## 研究问题

依据企业微信官方文档，扫码添加客户、`state` 关联、客户联系回调、去重与可确认转化之间有哪些可靠契约和限制；一码通标准产品可以把什么定义为“确认加企微”？

本文只使用企业微信官方开发者文档，访问与核对日期为 2026-07-26。

## 结论摘要

标准产品可以可靠确认的是 **“某个企业微信客户与某位企业成员建立了客户联系关系”**，而不是“用户点击了企微按钮”，也不是“用户已经产生聊天、购买或长期留存”。

建议把“确认加企微”定义为：

> 一码通成功验签并解密企业微信客户联系回调，收到
> `Event=change_external_contact`、`ChangeType=add_external_contact`，且该回调中的企业/应用作用域、`UserID`、`ExternalUserID` 和可选 `State` 通过本地连接器校验与幂等处理。

其中：

- `add_external_contact` 是官方的“添加企业客户事件”，可以作为确认建立客户联系关系的事实来源。[企业微信：客户联系事件格式](https://developer.work.weixin.qq.com/document/path/92130)
- `add_half_external_contact` 明确表示成员**尚未确认添加对方为好友**，只能记作“待成员确认”，不能计入确认转化。[企业微信：客户联系事件格式](https://developer.work.weixin.qq.com/document/path/92130)
- 展示二维码、点击“加企微”、复制微信号或跳出 H5 都只能记作意向行为；没有客户联系回调，不能宣称“确认加企微”。
- `state` 能把确认事件归因到企业自定义的添加渠道，但它属于「联系我」配置、最长 30 个字符，不天然标识一次 H5 扫码或一个自然人。[企业微信：客户联系「联系我」管理](https://developer.work.weixin.qq.com/document/path/92228)
- 回调可能因未及时返回成功而重试；官方事件没有业务事件 ID，因此接收端必须自行验签、持久化并幂等处理。[企业微信：回调配置](https://developer.work.weixin.qq.com/document/path/90465)

## 官方契约与产品含义

| 官方能力                    | 官方契约                                                                                                                                                                                                                                                                                                                                          | 一码通可依赖的产品含义                                                                 | 不能推出的结论                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 配置「联系我」              | 企业可为具有客户联系功能的成员生成二维码或小程序按钮；客户扫描或点击后可获取成员联系方式并主动联系。接口返回 `config_id`，二维码场景还返回 `qr_code`。[联系我管理](https://developer.work.weixin.qq.com/document/path/92228)                                                                                                                      | 可以生成和管理承接增长转化的企微入口，并把企业微信配置映射到一码通租户、活动和渠道     | 二维码被展示或按钮被点击，不等于客户已添加成员                                             |
| `state` 渠道参数            | `state` 是企业自定义参数，用于区分添加渠道；配置时最长 30 个字符；添加事件回调和客户详情的 `follow_user.state` 会返回该值。[联系我管理](https://developer.work.weixin.qq.com/document/path/92228)、[事件格式](https://developer.work.weixin.qq.com/document/path/92130)、[获取客户详情](https://developer.work.weixin.qq.com/document/path/92114) | 可以把确认添加归因到一码通管理的活动/渠道/企微入口                                     | 共享同一 `state` 的多个访问不能仅靠它区分到具体扫码会话；`state` 也不是客户身份            |
| `add_external_contact`      | 配置了客户联系功能的成员添加外部联系人时，企业微信回调该事件；事件包含 `UserID`、`ExternalUserID`、可选 `State` 和可选 `WelcomeCode`。[事件格式](https://developer.work.weixin.qq.com/document/path/92130)                                                                                                                                        | 是“确认加企微”的权威触发信号；确认的是特定客户与特定成员之间的客户联系关系             | 不代表客户已聊天、阅读欢迎语、下单或仍长期保留关系                                         |
| `add_half_external_contact` | 开启免验证时，外部联系人添加成员但成员尚未确认添加对方为好友，会回调该事件。[事件格式](https://developer.work.weixin.qq.com/document/path/92130)                                                                                                                                                                                                  | 记录为 `pending_member_confirmation`，可用于发现承接流失                               | 不能作为确认添加，也不能与 `add_external_contact` 合并计数                                 |
| 获取客户详情                | `externalcontact/get` 返回客户的 `follow_user` 数组；每项包含成员 `userid`、添加时间 `createtime`、固定来源 `add_way`、自定义渠道 `state` 等。返回的跟进人受应用可见范围限制，超过 500 人需要分页。[获取客户详情](https://developer.work.weixin.qq.com/document/path/92114)                                                                       | 可用于异步对账：确认对应成员关系仍存在、补齐添加时间和来源；不能作为同步回调的前置阻塞 | 查询中暂时没有对应成员，不一定代表从未添加；还要排查权限、可见范围、分页、删除和一致性时序 |
| 删除事件                    | 成员删除客户回调 `del_external_contact`；客户删除跟进成员回调 `del_follow_user`。[事件格式](https://developer.work.weixin.qq.com/document/path/92130)                                                                                                                                                                                             | 更新当前关系为已解除，并保留解除时间和方向；历史确认转化不应被物理删除                 | 当前已解除不等于历史确认事件无效，也不等于客户从未转化                                     |
| 回调交付                    | 回调需验签和解密；企业微信 5 秒内收不到响应会断开并重新请求，总共重试三次；HTTP 200 表示接收成功，其他状态均视为失败并重试。[回调配置](https://developer.work.weixin.qq.com/document/path/90465)                                                                                                                                                  | 接收端必须先可靠落库/入队并尽快返回 200，业务处理异步执行；同一回调可能重复到达        | 不能把“收到一次 HTTP 请求”直接等同于一个新增转化                                           |

## 推荐的转化状态模型

### 1. 漏斗事件

按证据强度区分，不把不同层级混成一个“加企微”指标：

1. `wecom_entry_impression`：企微入口已展示。
2. `wecom_entry_click`：用户点击入口或请求二维码。
3. `wecom_add_pending`：收到 `add_half_external_contact`，成员尚未确认。
4. `wecom_add_confirmed`：收到并成功处理 `add_external_contact`。
5. `wecom_relation_removed`：收到 `del_external_contact` 或 `del_follow_user`。

只有第 4 项显示为“确认加企微人数/转化数”。第 1、2 项属于站内可观测意向，第 3 项属于未完成转化，第 5 项反映当前关系流失。

### 2. 确认事件的接受条件

接收 `wecom_add_confirmed` 时至少完成：

- 验证企业微信回调签名并解密消息；企业/第三方应用的接收方标识必须映射到唯一的一码通租户与连接器。
- 校验 `Event`、`ChangeType`、`UserID`、`ExternalUserID`；`UserID` 必须属于该连接器允许的客户联系成员范围。
- 若带 `State`，只能映射到当前租户下由一码通签发或登记的归因记录；未知、过期或跨租户 `State` 不得静默归因。
- 先持久化原始事件和幂等指纹，再异步更新漏斗、客户关系和报表，及时向企业微信返回 HTTP 200。
- 通过 `externalcontact/get` 做延迟对账和故障修复，而不是让外部查询阻塞回调接收。

`State` 缺失或无法映射时，仍可记录“已确认建立企微客户关系”，但应标为 **未归因确认转化**，不能计入某个活动、码批次或扫码入口的归因成绩。

## `state` 的归因边界

官方把 `state` 定义为企业自定义的添加渠道，并将其随添加事件回传；客户详情也明确区分固定枚举来源 `add_way` 与企业自定义渠道 `state`。[获取客户详情](https://developer.work.weixin.qq.com/document/path/92114)

建议一码通：

- `state` 使用不超过 30 字符的随机不透明 token，只存引用，不放手机号、姓名、租户名等明文业务或个人信息。
- 服务端映射 token 到 `tenant_id + connector_id + campaign_id + entrypoint_id + version`。
- 默认按活动或企微入口创建「联系我」配置，不为每次产品扫码动态创建一个配置。
- `config_id` 必须持久化。官方说明 API 添加的「联系我」不在管理端展示，丢失 `config_id` 可能导致无法编辑或删除；每个企业最多通过 API 配置 50 万个「联系我」。[联系我管理](https://developer.work.weixin.qq.com/document/path/92228)

因此，标准产品可承诺 **活动/入口级归因**。如果多个 H5 扫码共用同一个「联系我」二维码，不能仅凭 `state` 承诺“这一次具体扫码最终添加了企微”。逐扫码创建「联系我」虽然理论上能获得更细 token，但会消耗有限配置额度、增加生命周期管理负担，不应作为标准产品主路径。

## 去重和唯一人数口径

### 1. 传输层幂等

企业微信官方说明失败回调会重试，但客户联系事件结构没有提供独立的业务事件 ID。[回调配置](https://developer.work.weixin.qq.com/document/path/90465)、[事件格式](https://developer.work.weixin.qq.com/document/path/92130)

因此建议以规范化解密消息生成幂等指纹，例如：

```text
sha256(
  connector_scope
  + Event
  + ChangeType
  + CreateTime
  + UserID
  + ExternalUserID
  + State
  + canonical_full_payload
)
```

`connector_scope` 至少包含租户、企业 CorpID/授权企业和应用调用方作用域。相同指纹只处理一次，但保留每次投递尝试用于运维审计。这是基于官方重试契约和事件字段作出的实现建议，不是企业微信提供的现成幂等键。

### 2. 关系唯一性

客户关系的业务键使用：

```text
(connector_scope, UserID, ExternalUserID)
```

因为官方事件表达的是客户与企业成员之间的关系，同一客户可以出现在多个成员的 `follow_user` 中。[获取客户详情](https://developer.work.weixin.qq.com/document/path/92114)

- 重复回调不新增关系。
- 删除事件把关系改为已解除，不删除历史事件。
- 删除后重新添加可形成新的“关系建立事件”，但不自动形成新的“活动唯一客户”。

### 3. 报表口径

至少同时提供：

- **确认添加关系数**：去重后的 `add_external_contact` 关系建立事件，可包含同一客户添加不同成员或删除后重加。
- **确认添加唯一客户数**：在同一租户、连接器和归因活动内，按 `ExternalUserID` 首次确认去重。
- **当前保留客户关系数**：确认关系减去相应删除/解除关系后的当前快照。

三者不能互相替代。增长漏斗默认使用“确认添加唯一客户数”，运营承接使用“确认添加关系数”，客户资产健康使用“当前保留客户关系数”。

## 身份与跨系统关联限制

- `ExternalUserId` 不是全球统一客户 ID。官方明确：同一个外部联系人，对不同调用方（企业/第三方服务商）返回的 `ExternalUserId` 不同。[企业微信：客户联系概述](https://developer.work.weixin.qq.com/document/path/92109)
- 客户详情中的 `unionid` 仅在联系人是微信用户且企业绑定微信开发者 ID 时返回；官方同时注明第三方不可获取该字段。[获取客户详情](https://developer.work.weixin.qq.com/document/path/92114)
- 因此，一码通作为标准 SaaS 不能默认依赖 `unionid` 把 H5 微信身份与企微客户做人员级合并，也不能跨不同连接器或调用方用裸 `ExternalUserID` 去重。

产品和数据模型必须把身份作用域显式带上。只有客户采用企业内部自建应用、完成合规绑定并确实取得 `unionid` 时，才能把跨系统人员级关联作为可选增强能力；它不属于标准产品的必然契约。

## 产品承诺与限制

可以对品牌方承诺：

- “确认加企微”来自企业微信官方客户联系添加事件，不是按钮点击推测。
- 能按一码通管理的 `state` 映射到活动/入口，统计确认关系、唯一客户和当前保留关系。
- 能区分待成员确认、确认添加和后续删除，并对回调重复投递做幂等处理。
- 能通过客户详情接口做异步对账，但对账结果受应用权限、成员可见范围和分页影响。

不能承诺：

- 每次点击或每次扫码都能确认添加。
- 共享二维码场景下能精准关联到某一次匿名 H5 扫码。
- `add_half_external_contact` 已经完成添加。
- 确认添加等于已聊天、已阅读欢迎语、已购买或永久保留。
- 不同企业、不同第三方服务商或不同连接器的 `ExternalUserID` 可以直接合并。
- 标准第三方 SaaS 一定能取得 `unionid` 并完成 H5 与企微客户的人员级匹配。

## 对后续规格的决策建议

1. **锁定指标定义**：只有成功处理 `add_external_contact` 才产生 `wecom_add_confirmed`；点击与 `add_half_external_contact` 分开统计。
2. **锁定归因粒度**：标准能力承诺活动/入口级 `state` 归因，不承诺共享企微入口下的单次扫码级人员归因。
3. **锁定状态机**：同时保存 `pending`、`confirmed`、`removed` 以及确认时间、解除时间和解除方向；历史确认不因删除事件消失。
4. **锁定幂等双层口径**：传输层按完整回调指纹去重，业务层按连接器作用域内的客户—成员关系去重，报表另按客户—活动计算唯一人数。
5. **锁定连接器门禁**：未完成回调 URL、签名密钥、客户联系权限、成员可见范围和回调验收测试时，UI 只能展示“企微跳转/点击”，不得展示“确认转化”。
6. **锁定对账机制**：回调是实时事实入口，客户详情 API 是异步复核和修复手段；需要回调死信、重放、对账差异与权限失效告警。
7. **锁定身份作用域**：`ExternalUserID` 必须绑定连接器/调用方作用域；`unionid` 只作为满足官方条件时的可选增强字段。

## 官方来源

- [企业微信开发者中心：客户联系「联系我」管理](https://developer.work.weixin.qq.com/document/path/92228)
- [企业微信开发者中心：客户联系事件格式](https://developer.work.weixin.qq.com/document/path/92130)
- [企业微信开发者中心：获取客户详情](https://developer.work.weixin.qq.com/document/path/92114)
- [企业微信开发者中心：客户联系概述](https://developer.work.weixin.qq.com/document/path/92109)
- [企业微信开发者中心：回调配置](https://developer.work.weixin.qq.com/document/path/90465)
