# Acceptance Criteria: 客户上线安全门禁

> Source: [docs/prd/launch-safety-gate.md](/Users/ericding/code/agriculture/yimatong/docs/prd/launch-safety-gate.md)
> Generated: 2026-07-31
> Functional Points: 8
> Scenarios: 25 (Happy: 8 | Error: 10 | Boundary: 7)

## 1. 上线准备度

### AC-01: 三项核心门禁通过

**Type**: Happy Path | **Priority**: P0

Given 当前上线版本已发布并正确绑定产品/活动、存在有效码批次且已绑定页面、真实扫码链路验证通过
When 用户查看该版本的上线准备度
Then 系统显示准备度通过、阻塞项为空，并允许进入品牌方确认流程

### AC-01-E1: 页面或活动绑定错误

**Type**: Error Case | **Priority**: P0

Given 当前上线版本的页面未发布，或页面绑定了错误的产品/活动
When 用户查看准备度或尝试确认上线
Then 系统显示对应阻塞原因，禁止进入正式上线流程

### AC-01-E2: 没有有效码批次

**Type**: Error Case | **Priority**: P0

Given 当前上线版本没有有效码批次，或码批次未绑定到当前页面
When 用户查看准备度或尝试确认上线
Then 系统显示码批次阻塞原因，禁止正式上线

### AC-01-E3: 真实扫码验证失败

**Type**: Error Case | **Priority**: P0

Given 当前上线版本的真实扫码链路验证失败
When 用户尝试确认上线或正式上线
Then 系统显示扫码验证失败原因，不切换线上版本

### AC-01-B1: 自定义域名按启用情况门禁

**Type**: Boundary | **Priority**: P1

Given 租户未启用自定义域名
When 用户检查默认域名上线准备度
Then 未配置自定义域名不会阻塞上线

Given 租户已启用自定义域名但 CNAME 未验证
When 用户检查上线准备度
Then 系统将域名验证列为阻塞项

## 2. 上线版本与品牌确认

### AC-02: 确认绑定具体上线版本

**Type**: Happy Path | **Priority**: P0

Given 当前上线准备度通过
When 品牌方租户管理员查看并确认上线内容
Then 系统保存租户、页面版本、活动、码批次、确认人、确认时间和内容摘要，并将该版本标记为已确认

### AC-02-E1: 非品牌方管理员不能完成确认

**Type**: Error Case | **Priority**: P0

Given 当前用户不是该租户的品牌方管理员
When 用户尝试提交品牌确认
Then 系统拒绝操作，且不产生有效品牌确认记录

### AC-02-E2: 关键配置变化使确认失效

**Type**: Error Case | **Priority**: P0

Given 某上线版本已经获得品牌确认
When 页面、活动、码批次、域名或其他影响上线结果的关键配置发生变化
Then 原确认自动失效，新版本必须重新计算准备度并重新确认

## 3. 品牌方自助确认并上线

### AC-03: 品牌方确认并正式上线

**Type**: Happy Path | **Priority**: P0

Given 当前版本准备度通过，且品牌方管理员正在查看该版本
When 品牌方管理员点击“确认并上线”并完成明确确认
Then 系统再次检查硬门禁，检查通过后执行上线，记录确认与上线事件，并显示已上线

### AC-03-E1: 二次检查失败时不切换

**Type**: Error Case | **Priority**: P0

Given 用户打开确认页后，当前版本的硬门禁在提交前发生变化并变为不通过
When 用户提交“确认并上线”
Then 系统拒绝上线，显示最新阻塞原因，线上版本保持不变

### AC-03-B1: 重复提交具有幂等结果

**Type**: Boundary | **Priority**: P0

Given 同一上线请求因重复点击或网络重试被提交多次
When 系统处理这些请求
Then 只产生一次有效上线执行，后续请求返回同一执行结果，不重复切换版本

## 4. 代运营协作上线

### AC-04: 品牌确认后由授权人员发布

**Type**: Happy Path | **Priority**: P0

Given 代运营已准备上线版本，品牌方管理员已确认该版本，且执行人员拥有该租户的发布授权
When 执行人员点击“发布上线”
Then 系统再次检查硬门禁并完成上线，保留品牌确认记录和发布执行记录

### AC-04-E1: 未经品牌确认不能发布

**Type**: Error Case | **Priority**: P0

Given 当前版本尚未获得品牌方管理员确认
When 代运营人员尝试发布上线
Then 系统拒绝发布，并明确提示需要品牌方确认

### AC-04-B1: 默认不授予代运营发布权限

**Type**: Boundary | **Priority**: P0

Given 租户没有明确授予代运营发布权限
When 代运营人员尝试发布上线
Then 系统拒绝发布；品牌方管理员仍可完成正式上线

## 5. 角色化状态与文案

### AC-05: 不同角色看到可理解的下一步

**Type**: Happy Path | **Priority**: P1

Given 同一上线版本处于等待品牌确认阶段
When 品牌方管理员查看
Then 主状态为“待确认”，页面说明“待你确认上线内容”，并提供“预览并确认”入口

Given 同一上线版本处于等待品牌确认阶段
When 代运营人员查看
Then 主状态仍为“待确认”，页面说明“等待品牌方确认”，并提供查看预览或发送确认入口

### AC-05-B1: 页面只展示一个主状态

**Type**: Boundary | **Priority**: P1

Given 上线版本存在准备度、品牌确认和页面版本等多个内部事实
When 用户查看客户列表或上线详情
Then 页面只展示一个主生命周期状态，其他事实以进度、阻塞原因或记录形式展示，不出现相互竞争的多个主状态

## 6. 上线失败与暂停

### AC-06: 上线执行成功

**Type**: Happy Path | **Priority**: P0

Given 上线版本准备度通过、品牌确认有效且发布权限满足
When 正式上线执行完成
Then 系统显示“已上线”，并能查询当前线上版本和执行时间

### AC-06-E1: 上线执行失败可重试

**Type**: Error Case | **Priority**: P0

Given 正式上线执行过程中发生可识别失败
When 系统完成执行
Then 系统显示“上线失败”及失败原因，线上版本保持不变，并提供安全重试入口

### AC-06-B1: 线上风险可暂停

**Type**: Boundary | **Priority**: P1

Given 当前线上版本出现需要停止对外服务的安全或业务风险
When 品牌方管理员执行暂停
Then 系统显示“已暂停”，记录原因、操作者和时间，且消费者不能继续访问该线上版本

## 7. 版本变更与回滚

### AC-07: 线上版本不可直接修改

**Type**: Happy Path | **Priority**: P0

Given 当前已有线上版本
When 用户修改页面、活动、码批次或其他影响上线结果的配置
Then 系统创建新的候选版本，线上版本保持不变，新版本重新进入准备度和品牌确认流程

### AC-07-E1: 回滚不能伪造为直接切换历史版本

**Type**: Error Case | **Priority**: P0

Given 用户选择一个历史版本作为回滚目标
When 用户发起回滚
Then 系统创建新的候选版本并要求重新检查和确认，不直接修改历史版本状态来伪造回滚结果

### AC-07-B1: 回滚候选版本仍受硬门禁约束

**Type**: Boundary | **Priority**: P0

Given 历史版本的页面、活动、码批次或域名事实已不再满足当前硬门禁
When 用户尝试发布回滚候选版本
Then 系统阻止发布并显示具体阻塞原因

## 8. 权限、租户隔离与审计

### AC-08: 上线全链路可审计

**Type**: Happy Path | **Priority**: P0

Given 用户完成准备度检查、品牌确认、正式上线、暂停或回滚中的任一动作
When 用户查看上线记录
Then 系统显示租户、版本、动作、操作者、时间、结果和必要的原因/内容摘要

### AC-08-E1: 跨租户操作被拒绝

**Type**: Error Case | **Priority**: P0

Given 用户属于租户 A，尝试读取或修改租户 B 的上线版本、确认记录或执行记录
When 请求到达 API 或数据库访问层
Then 请求被拒绝，且不能泄露租户 B 的业务数据

### AC-08-B1: 平台管理员不直接完成客户业务放行

**Type**: Boundary | **Priority**: P1

Given 平台管理员需要处理客户上线相关问题
When 平台管理员尝试直接代替品牌方完成业务放行
Then 系统不提供普通业务放行入口；受控支持流程必须记录原因、范围和审计信息

## Self-Check Report

| Check                     | Status | Notes                                                |
| ------------------------- | ------ | ---------------------------------------------------- |
| Happy paths exist         | PASS   | 8 个功能点均有成功场景                               |
| Write errors covered      | PASS   | 覆盖绑定错误、确认失败、上线失败、重复提交和回滚边界 |
| Boundaries covered        | PASS   | 覆盖默认域名、授权发布、单一主状态、暂停和回滚门禁   |
| Permissions covered       | PASS   | 覆盖品牌方管理员、代运营、平台管理员和跨租户访问     |
| State transitions covered | PASS   | 覆盖准备度、确认、上线、失败、暂停和重新确认         |
| Idempotency considered    | PASS   | 正式上线重复提交只产生一次有效执行                   |
| Concurrency addressed     | PASS   | 提交前二次检查和重复请求保护避免过期版本上线         |
