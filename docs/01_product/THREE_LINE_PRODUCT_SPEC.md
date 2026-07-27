# 基准客户场景三线产品闭环规格

## Problem Statement

食品和农产品品牌方已经可以在一码通中配置产品、码、页面、活动、渠道和部分风险能力，但这些模块尚未形成一套可以稳定重建、真实运行和独立验收的标准产品闭环。

从消费者视角看，同一枚包装码的产品、生产批次、验真结果、权益资格和异常提示仍可能来自不同数据来源；页面可打开也不能证明首次验证、权益领取或后续转化已经被正确记录。

从品牌方视角看，点击企微或商城入口与确认添加、确认订单等结果尚未被清晰区分，转化分母、访客去重、归因窗口和外部数据可信边界不足以支持经营决策。

从渠道管理视角看，单次区外扫码、正式窜货线索、调查进度和人工结论尚未形成完整、可举证、可审计的业务生命周期，系统也不能用弱定位信号自动定性或处罚渠道。

从产品交付视角看，现有核心 E2E 依赖预置上下文，部分关键动作缺失时仍可通过；多数后端测试使用进程内数据库，无法单独证明 PostgreSQL RLS、真实浏览器、真实 API 和持久化结果共同成立。标准产品因此缺少一个从干净环境开始、覆盖增长转化、溯源与轻量验真、渠道防窜三条独立价值线的权威完成标准。

## Solution

构建一个统一的首个规格包，按以下纵向切片依次交付：

1. 基准底座；
2. 溯源与轻量验真；
3. 增长转化；
4. 渠道防窜；
5. 三线联合门禁。

基准底座提供可重复重建的基准数据包、隔离对照租户、正确的默认角色权限、唯一权威的产品与生产批次数据，以及可运行的 Admin、H5 和 Backend。

溯源与轻量验真采用未激活、有效、冻结、作废四种互斥码生命周期状态，区分首次验证、重复验证、首次参与和异常使用信号。消费者始终获得与权威生产批次一致的溯源信息；风险判断不自动等同商品真伪结论。

增长转化将有效页面访问和入口点击定义为意向事件，将权益领取成功、留资成功、企业微信官方回调确认、可信订单和净 GMV 定义为确认转化。后台分别展示意向漏斗、确认结果漏斗和经营日报，并提供明确的身份去重、归因窗口、归因快照和外部数据质量状态。

渠道防窜将位置观察、跨区观察和窜货线索分层记录。正式线索按可调查问题聚合，处理进度与调查结论分开，确认窜货必须有可靠流向、可靠观察、业务佐证和人工理由；系统不自动处罚渠道。

整个规格以一个统一产品验收套件作为最高测试 seam。该套件从干净 PostgreSQL 环境构建基准数据，通过真实浏览器完成 Admin、消费者 H5 和渠道角色旅程，同时观察真实 API，并在旅程结束后使用只读验证器核对数据库状态、归因、证据快照和审计记录。后端集成、迁移、RLS、并发和外部回调契约测试作为补充，不替代最高层产品证据。

## User Stories

1. As a 平台管理员, I want to create a new brand tenant with complete default roles and permissions, so that the tenant can use the standard product without hidden manual fixes.
2. As a 平台管理员, I want tenant initialization to be repeatable, so that environment rebuilds do not depend on historical Demo data.
3. As a 平台管理员, I want initialization failures to stop the acceptance run immediately, so that broken seeds or permissions cannot be hidden by later tests.
4. As a 品牌管理员, I want to create a brand, product, SKU and production batch, so that every packaging code has authoritative product facts.
5. As a 品牌管理员, I want to attach inspection reports and qualification certificates to the product or production batch, so that consumers can review trust information.
6. As a 品牌管理员, I want to generate and activate a code batch using my default role, so that normal onboarding does not require platform-level intervention.
7. As a 品牌管理员, I want every code batch to reference one authoritative production batch, so that H5 and Admin cannot display conflicting traceability facts.
8. As a 品牌运营人员, I want to configure and publish a consumer H5 page, so that an activated packaging code opens a usable experience.
9. As a 品牌运营人员, I want page modules to select which authoritative fields are displayed rather than duplicate their values, so that page editing cannot silently rewrite traceability.
10. As a 品牌管理员, I want the Admin, H5 and Backend applications to build and start together, so that a green acceptance result represents a runnable product.
11. As a 测试负责人, I want stable business identifiers for baseline objects, so that a failed journey can be reproduced and diagnosed quickly.
12. As a 测试负责人, I want the baseline dataset to rebuild twice without duplicates or stale state, so that the test result is deterministic.
13. As a 消费者, I want an unknown code to return a clear “not found” result without leaking brand or tenant data, so that I am protected from misleading information.
14. As a 消费者, I want an unactivated code to tell me it has not been enabled, so that early leakage is not presented as a normal verification.
15. As a 品牌风控运营人员, I want unactivated-code scans to be recorded as early-leakage observations, so that I can investigate packaging released before activation.
16. As a 消费者, I want the first valid verification to state that this is the platform’s first recorded verification, so that I understand the result without an absolute authenticity claim.
17. As a 品牌风控运营人员, I want only one verification to win the global first-verification race for a code, so that concurrent requests cannot create multiple first scans.
18. As a 消费者, I want repeated verification to show the original verification time and cumulative count, so that I can understand the code’s history.
19. As a 消费者, I want repeated verification to avoid exposing other consumers’ identity, location, device or network information, so that privacy is protected.
20. As a 消费者, I want a normal repeat scan to avoid being labelled counterfeit or abnormal, so that harmless rechecks do not create fear.
21. As a 消费者, I want an active code with abnormal-use signals to continue showing authoritative traceability, so that risk detection does not hide product facts.
22. As a 消费者, I want abnormal-use messaging to explain uncertainty and provide a support path, so that I know what action to take.
23. As a 品牌运营人员, I want benefits to pause when abnormal-use signals indicate abuse risk, so that marketing value is protected during investigation.
24. As a 品牌风控运营人员, I want abnormal-use signals to create an explainable risk record without automatically freezing or voiding the code, so that system inference remains separate from human action.
25. As a 消费者, I want a frozen code to retain traceability while stating that it is under review, so that a reversible investigation is distinguishable from permanent invalidation.
26. As a 品牌风控运营人员, I want an authorized freeze to be reversible and audited, so that temporary risk controls can be safely removed.
27. As a 消费者, I want a voided code to stop normal verification and benefits permanently, so that irreversible invalidation is clear.
28. As a 品牌管理员, I want voiding to require a reason and secondary confirmation, so that irreversible actions are deliberate and traceable.
29. As a 消费者, I want viewing traceability to remain login-free, so that trust information is easy to access.
30. As a 消费者, I want only visits that successfully load an eligible H5 experience to count as valid page visits, so that conversion metrics reflect real opportunities.
31. As a 品牌运营人员, I want invalid, frozen, abnormal-benefit-paused, robot and test traffic excluded from the growth denominator, so that unavailable journeys are not counted as conversion failures.
32. As a 品牌运营人员, I want repeated scans and unique visitors reported separately, so that diagnostic volume does not distort conversion rates.
33. As a 消费者, I want an anonymous visitor identity before login or consent, so that basic browsing does not require personal information.
34. As a 消费者, I want my identity to upgrade only when there is an authoritative claim, lead, enterprise-WeChat or order relationship, so that weak device signals do not merge unrelated people.
35. As a 品牌运营人员, I want anonymous cross-device visits to remain separate unless there is reliable linkage, so that UV is not artificially reduced.
36. As a 消费者, I want to claim an eligible benefit exactly once under the activity rules, so that retries do not issue duplicate value.
37. As a 消费者, I want to see purpose, scope and withdrawal information before submitting lead data, so that my consent is informed.
38. As a 合规负责人, I want lead data and its consent version, scenario and timestamp stored together, so that collection is auditable.
39. As a 品牌运营人员, I want enterprise-WeChat entry clicks recorded as intent rather than confirmed conversion, so that click metrics are not overstated.
40. As a 品牌运营人员, I want only a verified and idempotently handled official add-contact callback to count as confirmed enterprise-WeChat conversion, so that the result is trustworthy.
41. As a 品牌运营人员, I want an enterprise-WeChat state value to attribute the activity or entry without claiming anonymous person-level proof, so that attribution respects the platform’s actual capability.
42. As a 品牌运营人员, I want marketplace redirects recorded as intent only, so that outbound clicks are not mistaken for orders.
43. As a 品牌运营人员, I want standard order imports to validate source, identity, amount and duplication, so that imported outcomes are trustworthy.
44. As a 品牌运营人员, I want cancellations and refunds to reduce net GMV while retaining the original order and audit history, so that reporting reflects current business value.
45. As a 品牌运营人员, I want benefit, lead and enterprise-WeChat results attributed to the last eligible entry within a default seven-day window, so that short-cycle conversion is consistent.
46. As a 品牌运营人员, I want orders and GMV attributed to the last eligible entry within a default thirty-day window, so that delayed purchase behavior can be measured.
47. As a 品牌运营人员, I want results outside the attribution window retained but left unattributed, so that the system does not invent campaign credit.
48. As a 品牌运营人员, I want attribution to preserve the activity, page version, product, code batch, channel, rule and window snapshot, so that later configuration changes do not rewrite history.
49. As a 品牌运营人员, I want a visitor-cohort funnel and a result-occurrence daily report, so that campaign effectiveness and daily operations are not mixed.
50. As a 品牌运营人员, I want an in-progress cohort labelled as still collecting results, so that incomplete windows are not mistaken for final conversion rates.
51. As a 品牌运营人员, I want external data status to distinguish not connected, synchronized zero, incomplete coverage and failed synchronization, so that zero is not confused with missing data.
52. As a 品牌运营人员, I want each external result to show source, coverage and last synchronization time, so that I can judge data reliability.
53. As a 渠道运营人员, I want a versioned relationship between distributor, code batch, authorized region and effective period, so that each scan can be compared with the correct historical flow.
54. As a 渠道运营人员, I want every scan location to retain source, accuracy, time and provider version, so that location uncertainty remains visible.
55. As a 消费者, I want denial of browser location permission to preserve the basic traceability journey, so that optional location collection does not block product information.
56. As a 渠道运营人员, I want an in-region scan to remain a normal location observation, so that routine traffic does not create noise.
57. As a 渠道运营人员, I want a single low-quality out-of-region scan to create only a cross-region observation, so that weak evidence does not open a formal case.
58. As a 渠道运营人员, I want sustained deviations grouped by distributor, code batch, route and time window, so that one investigable problem does not become hundreds of duplicate cases.
59. As a 渠道运营人员, I want a strong impossible-travel pattern for one code to be eligible for its own clue, so that high-value evidence is not hidden by aggregation.
60. As a 渠道运营人员, I want each clue to snapshot the flow, expected region, observations, evidence quality, rule and threshold versions, so that later master-data edits do not overwrite historical facts.
61. As a 经销商, I want to see only clues related to my own scope, so that I can respond without seeing other channels’ data.
62. As a 经销商, I want to upload transfer, order, logistics or explanation evidence, so that normal business movement can be demonstrated.
63. As a 经销商, I want to be prevented from deciding or closing a clue involving myself, so that adjudication remains independent.
64. As a 品牌风控运营人员, I want clue progress recorded separately from investigation outcome, so that “closed” is not mistaken for “confirmed diversion.”
65. As a 品牌风控运营人员, I want to select evidence-insufficient, false positive, normal transfer, master-data error or confirmed diversion outcomes, so that investigations have precise business conclusions.
66. As a 品牌风控运营人员, I want confirmed diversion to require valid flow, reliable observations, business corroboration and a written reason, so that the decision is defensible.
67. As a 品牌风控运营人员, I want a closed clue to reopen when new evidence arrives while preserving the original outcome, so that history is never silently rewritten.
68. As a 品牌管理员, I want the system to notify, aggregate and recommend without automatically freezing codes, disabling channels or applying penalties, so that irreversible business action remains human-controlled.
69. As a 代运营人员, I want to work only within explicitly authorized tenants and capabilities, so that cross-client assistance does not weaken tenant isolation.
70. As a 对照租户用户, I want my own similarly named records to remain isolated, so that identifier guessing or name collisions cannot expose the baseline tenant.
71. As a 平台支持人员, I want cross-tenant support access to require an explicit bypass workflow and audit record, so that platform intervention is accountable.
72. As a 安全负责人, I want every business table and attachment involved in the three lines tenant-scoped, so that application filtering and PostgreSQL RLS provide dual isolation.
73. As a 测试负责人, I want one product acceptance suite to provision data, drive real journeys and verify persistence, so that product completion has one authoritative seam.
74. As a 测试负责人, I want every hard-gate scenario to produce browser, API and database evidence, so that a screenshot or HTTP 200 cannot pass alone.
75. As a 测试负责人, I want acceptance outcomes limited to passed, failed or pending external integration, so that “mostly passed” cannot hide release risk.
76. As a 产品负责人, I want growth, traceability and light verification, and channel anti-diversion evaluated independently, so that one strong line cannot compensate for another broken line.
77. As a 产品负责人, I want any failed or pending hard gate to block standard product completion, so that the release claim remains credible.
78. As a 研发人员, I want each tracer bullet to deliver a user-visible vertical result, so that backend-only or UI-only stages do not accumulate as untestable work.
79. As a 研发人员, I want ambiguous historical verification, attribution and diversion data left unclaimed, so that migrations do not fabricate business facts.
80. As a 研发人员, I want each database migration reversible and validated on clean PostgreSQL, so that schema evolution is safe.

## Implementation Decisions

1. The product is delivered as one specification containing four vertical slices: baseline foundation, traceability and light verification, growth conversion, and channel anti-diversion. A final combined gate follows the four slices.
2. The delivery order is fixed. Baseline foundation blocks all other slices. Traceability and light verification establishes the authoritative scan and consumer-result contract. Growth consumes eligible visits and verified identities. Anti-diversion consumes authoritative scans, locations and channel flow.
3. Each tracer bullet crosses the highest relevant product layers: reachable UI, API contract, domain rule, persistence, tenant scope, permission and audit. Work is not organized as “all backend first, all UI later.”
4. The baseline foundation provides a deterministic functional dataset of approximately 20–50 codes with stable business identifiers. A separate volume dataset handles performance and load; volume data does not affect functional acceptance.
5. The baseline dataset contains one full brand tenant and one minimal isolation-control tenant with similarly named records. The control tenant exists only for negative isolation tests.
6. Initialization creates tenant roles and permissions through supported product paths. Brand administrators receive the permissions required for normal code generation; platform bypass is not used to hide missing tenant permissions.
7. Initialization and baseline generation must succeed twice from a clean environment without duplicate data. Historical Demo data is not an acceptance prerequisite.
8. Admin, H5 and Backend builds are common release gates. A slice cannot pass when a user-facing application fails to build or start.
9. Product, SKU and production batch records are the authoritative traceability source. Page configuration selects presentation modules and visible fields but does not own duplicate product, batch, origin, date, inspection or certificate facts.
10. Code lifecycle contains exactly four mutually exclusive states: unactivated, active, frozen and voided. Generation, export, printing and delivery belong to code-batch delivery progress. First/repeat verification are events. Abnormal use is a risk signal. Not-found is a query result.
11. Existing code-state values are migrated only when their meaning is unambiguous. Generated or created maps to unactivated; activated maps to active; frozen maps to frozen; revoked maps to voided. Event-like or delivery-like legacy values must be normalized into their proper dimensions rather than retained as lifecycle states.
12. First verification is global per issued code and uses an atomic write so concurrent requests cannot create multiple first-verification events. First participation remains an activity-and-consumer rule and never rewrites first verification.
13. The public resolver returns one explicit consumer-result contract for HTML and JSON clients. Result payloads identify query result, lifecycle state, verification event, cumulative count, authoritative traceability and benefit eligibility as separate fields.
14. Unknown and unactivated codes do not receive normal verification or benefit access. Unknown codes do not reveal tenant or product information.
15. Frozen codes retain authoritative traceability and a review message while benefits are paused. Voided codes do not receive normal verification or benefits and cannot return to another lifecycle state.
16. Active codes with abnormal-use signals retain traceability, receive a cautious risk message and support path, and have exploitable benefits paused. The signal does not automatically freeze, void or declare counterfeit.
17. Risk records store matched facts, evidence quality, rule version and risk level. Platform facts, risk judgment and human disposition remain separate in storage and UI.
18. Freeze and unfreeze require explicit authorization and audit. Void is restricted to brand administrators by default, requires reason and secondary confirmation, and is irreversible.
19. The growth event model separates intent events from confirmed results. Valid page visits, benefit-entry clicks, lead-entry clicks, enterprise-WeChat entry clicks and marketplace redirects are intent. Successful claim, successful lead submission, verified add-contact callback, trusted order and net GMV are confirmed results.
20. A valid page visit requires successful H5 load, an activity-eligible active code, no abnormal-benefit pause, and traffic not identified as robot or internal test. Raw resolver requests and scan counts remain diagnostic metrics.
21. The headline growth denominator is unique visitors with valid page visits. Adjacent-step conversion rates are shown as secondary diagnostics. Raw scan count never replaces the headline denominator.
22. Anonymous visitors receive a first-party anonymous visitor identifier. Identity can be upgraded through an authoritative consumer, lead, enterprise-WeChat or trusted external relationship. IP, device fingerprint or location alone cannot merge consumers, and anonymous cross-device visits remain separate.
23. Confirmed results are written only after the authoritative domain write succeeds or a trusted callback/import is validated. Client success messages and request initiation do not create confirmed results.
24. Claim, lead, callback and import operations use business idempotency keys appropriate to their source. Duplicate requests, callback retries and duplicate order files do not duplicate outcomes.
25. Lead collection stores consent scenario, version, purpose, timestamp, withdrawal state and protected data. Sensitive identifiers continue to use the existing encryption and hashing boundaries.
26. Enterprise-WeChat confirmed conversion requires successful signature verification, decryption and idempotent handling of the official add-external-contact change event. A half-added contact, QR display or click is not confirmed conversion.
27. Enterprise-WeChat state is an activity- or entry-level attribution key. It does not prove that an anonymous H5 visitor and an external contact are the same person.
28. Marketplace redirect is always an intent event. The product does not infer an order from a successful redirect.
29. External orders enter through the standard validated import boundary in this specification. Source, external order identifier, consumer or matching identifier, amount, status, timestamps and duplication are validated.
30. Order cancellation and refund adjustments update net GMV without deleting the original order, attribution or audit history.
31. Benefit, lead and enterprise-WeChat results use a tenant-configurable default seven-day attribution window. Orders and GMV use a tenant-configurable default thirty-day window.
32. Attribution selects the last eligible entry within the applicable window. Results outside the window remain part of total business outcomes but are not forced onto an activity, page, code batch or channel.
33. Attribution persists a snapshot of tenant, identity scope, activity and version, page and version, product, SKU, production batch, code batch, channel, entry key, rule and window.
34. Growth reporting exposes a visitor-cohort funnel and a result-occurrence daily view. Cohorts still inside their attribution window are marked as collecting. The two time models do not share an unlabeled conversion rate.
35. External data quality distinguishes not connected, synchronized zero, incomplete coverage and synchronization failure. Each external metric exposes source, coverage and last synchronization time.
36. Channel flow is versioned by distributor, code batch, authorized region and effective period. Location comparison uses the version valid at observation time.
37. A location observation records server-observed time, source, coordinate or area, accuracy or radius, provider and provider version, plus controlled network-risk features. Denied location permission does not block traceability.
38. A cross-region observation represents a meaningful deviation with its uncertainty preserved. One low-quality IP deviation or an uncertainty area overlapping the expected region cannot directly form a formal clue.
39. A diversion clue represents one investigable problem, not one scan. The aggregation key considers distributor, code batch, abnormal route and time window; a strong impossible-travel pattern for one code may form a standalone clue.
40. Clue creation stores an immutable evidence snapshot containing codes, products, batches, channel flow, expected regions, location observations, rules, thresholds, evidence quality and aggregation reason.
41. Clue workflow progress and investigation outcome are separate fields. Progress supports awaiting verification, investigating, closed and reopened. Outcomes support confirmed diversion, normal transfer or circulation, master-data error, false positive, and insufficient evidence with continued observation.
42. Confirmed diversion requires an auditable channel-flow baseline, sufficiently reliable cross-region observations, at least one business corroboration item, and a written human reason.
43. Distributor users can view only clues in their own scope and can add comments and evidence. They cannot adjudicate, close or mark their own clue as false positive.
44. Brand administrators and explicitly authorized risk/channel operators can investigate and close clues. Agency users require explicit tenant authorization. Platform administrators intervene only through an audited support workflow.
45. Closed clues can reopen when new evidence arrives. Original progress history, outcome, evidence and snapshots are append-only from the business perspective.
46. Automatic actions are limited to notification, risk prioritization, deadline assignment, observation aggregation and recommendations. The system does not automatically freeze or void codes, disable distributors, impose penalties or confirm diversion.
47. Multi-tenant isolation remains dual-layer: all new business records carry tenant scope, application queries filter by tenant, PostgreSQL RLS enforces row isolation, and object storage remains tenant-partitioned.
48. High-risk actions and sensitive exports record actor, tenant, reason, time, before/after state and support context where applicable.
49. User-visible incomplete capabilities are hidden using tenant-scoped capability switches. Switches cannot suppress acceptance scenarios that belong to the completed specification.
50. Each tracer bullet may merge when its own user-visible journey and supporting tests pass. The overall standard product is complete only when baseline reconstruction, all three independent lines, isolation/permission/audit gates and required external smoke tests pass.
51. Database changes use reversible Alembic migrations. Clearly mappable historical records are migrated. Ambiguous first-verification, attribution or diversion history is marked legacy/unattributed or excluded from new metrics; the migration never fabricates business conclusions.
52. Demo and baseline data are regenerated under the new contract. The specification does not build a generic legacy-customer migration product.
53. Existing PRD, API and test statements that conflict with the confirmed product models are revised in the same implementation slice. Compatibility with an incorrect business meaning is not a goal.

## Testing Decisions

1. The primary testing seam is one unified product acceptance suite. It starts from clean PostgreSQL, provisions the baseline and isolation-control tenants, drives real Admin/H5/channel journeys, observes real API behavior, and verifies persisted state through a read-only evidence verifier.
2. The unified suite is the only source that can declare a product line or the whole specification complete. Lower-level tests localize failures and cover combinatorial edges but do not replace the highest seam.
3. A good test asserts externally observable business behavior: what an authorized user can do, what a consumer sees, which API contract is returned, which durable business fact is stored, and which action is denied. It does not assert ordinary explanatory copy, internal helper calls or component layout.
4. Every hard-gate scenario produces three evidence layers: browser result, API request/response summary, and database assertion. Missing evidence fails the scenario even when another layer appears correct.
5. The product acceptance suite enters pages through real user-reachable navigation or packaging-code URLs. It does not substitute direct internal URLs for a user journey.
6. Critical actions are unconditional assertions. A missing claim, submit, confirm or investigation control fails the test; tests do not silently skip the action when the control is absent.
7. Baseline provisioning runs twice from clean state and verifies stable business identifiers, expected counts and absence of duplicate dirty data.
8. PostgreSQL is required for release evidence because tenant RLS, transaction semantics, atomic first-verification and migration behavior cannot be proven by SQLite.
9. Existing Playwright core-flow and channel-flow journeys are extended or consolidated rather than replaced with a second browser framework.
10. Existing core scan integration tests provide prior art for resolver, scan event, benefit and analytics assertions, but direct database shortcuts and permissive optional assertions are removed from hard-gate journeys.
11. Existing first-scan service tests provide prior art for atomic first-verification. New concurrency coverage proves exactly one first event under parallel requests.
12. Existing code-state API and service tests provide prior art for lifecycle transitions. They are revised to the four-state lifecycle and cover illegal transitions, freeze recovery and irreversible void.
13. Existing resolver public/API tests provide prior art for unknown, unactivated, active, frozen and voided consumer results. HTML and JSON contracts must agree on business meaning.
14. Existing benefit, consent, enterprise-WeChat, GMV and campaign analytics tests provide prior art for growth boundaries. Coverage is extended for idempotency, source trust, attribution windows, refunds and data-quality status.
15. Existing channel-flow browser coverage and channel/diversion service tests provide prior art for channel scope. Coverage is extended from configuration visibility to observation, aggregation, evidence, outcome, reopening and audit.
16. Existing RLS API, SQL tamper-prevention and platform cross-tenant tests provide prior art for isolation. Every new record type and attachment is added to the isolation matrix.
17. Existing clean-database migration tests remain a hard gate. Migrations must upgrade a clean PostgreSQL database, match models and support the documented downgrade boundary.
18. Migration tests include representative legacy values and prove that ambiguous history is not converted into first verification, confirmed conversion or confirmed diversion.
19. Role tests cover both positive and negative paths for brand administrator, activity operator, risk/channel operator, distributor, agency, control-tenant user and platform support.
20. External automated tests use contract-faithful services for enterprise-WeChat signature/decryption/retry, browser location grant/deny/accuracy, and order import duplicate/refund scenarios.
21. Release readiness also requires a non-production real enterprise-WeChat add-contact callback and a real browser geolocation-permission smoke test. A specific order connector requires a real smoke only if the product later claims support for that connector.
22. External integration status is `pending_external` until the required real smoke completes. `pending_external` blocks standard product completion just like `failed`.
23. Growth tests verify intent and confirmed outcomes independently; a click can never satisfy a confirmed-result assertion.
24. Funnel tests use deterministic event times to prove seven-day and thirty-day boundaries, last-eligible-entry selection, outside-window unattributed results and immutable attribution snapshots.
25. Data-quality tests distinguish no connection, synchronized zero, partial coverage and failed synchronization.
26. Verification tests cover not found, unactivated, first, repeat, abnormal active, frozen and voided results, plus first-participation independence and privacy non-disclosure.
27. Anti-diversion tests cover in-region observation, low-quality single deviation, aggregated formal clue, distributor evidence, insufficient evidence, false positive, normal transfer, master-data error, confirmed diversion and reopening.
28. Anti-diversion tests prove that clue creation does not automatically freeze codes, disable channels or create penalties.
29. Audit tests verify actor, reason, time and before/after state for freeze, unfreeze, void, confirmed diversion, support bypass and sensitive export.
30. Acceptance output is both machine-readable and business-readable. Each record includes scenario, dataset version, status, release-blocking flag, tenant, role, stable business identifiers, evidence references, code version, execution time and failure reason.
31. Allowed acceptance statuses are `passed`, `failed` and `pending_external`. There is no “mostly passed.”
32. Growth, traceability and light verification, and channel anti-diversion produce separate gate summaries. Any failed or pending line blocks overall completion.

## Out of Scope

- Real customer recruitment, commercial contracting, field pilots, packaging rollout and operating execution.
- Customer go-live checklists, customer preview/approval workflow and 7/14/30-day pilot reviews.
- Existing-code takeover, customer domain or CNAME lifecycle, legacy-code migration and re-labelling workflows.
- Outer/inner dual-code mode as a delivered product flow.
- Production-line coding, printer integration and complete printing-vendor collaboration.
- A self-owned commerce system, payment, logistics, after-sales and settlement.
- Marketplace-specific order connectors; this specification includes only the standard validated order import boundary.
- Complex CRM/ERP integration and generic legacy-system migration.
- Points mall, advanced membership, lottery, cash red packets and financial settlement.
- AI-assisted configuration, regional-brand advanced capabilities, white-label, multilingual and private deployment.
- Advanced location intelligence that claims physical authenticity or automatic diversion proof.
- Generic P2 UI cleanups and unrelated security, performance or refactoring initiatives.
- Production-scale load testing inside the functional baseline dataset; load uses a separate capacity dataset.

## Further Notes

- This is a standard product-development specification, not a pilot plan.
- The canonical domain terms are: base customer scenario, baseline dataset, isolation-control tenant, code lifecycle, first verification, repeat verification, first participation, abnormal-use signal, platform fact, risk judgment, human disposition, intent event, confirmed conversion, valid page visit, trusted external feedback, attribution window, attribution snapshot, location observation, cross-region observation, diversion clue, clue progress, investigation outcome, confirmed diversion, evidence snapshot, vertical slice, three-line independent gate and standard product completion.
- Ordinary QR codes can prove platform-issued code facts and support risk signals; they do not prove the physical item is absolutely authentic.
- Location observations preserve uncertainty. A refused location permission does not block traceability, and one weak out-of-region signal cannot confirm diversion.
- Enterprise-WeChat state supports activity- or entry-level attribution. It does not establish anonymous scan-to-person identity by itself.
- The implementation order remains baseline foundation, traceability and light verification, growth conversion, channel anti-diversion, and the combined gate.
- CodeGraph has been initialized for the repository and its machine-local index is ignored by Git. Agents should use the index before text search when locating affected symbols and call paths.
