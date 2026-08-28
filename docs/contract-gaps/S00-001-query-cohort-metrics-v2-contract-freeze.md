# S00-001 — Query Cohort、回答语义事件与指标 V2 契约冻结候选

- Reporter/session: S00/S04 contract audit
- Status: `designed_uncommitted`
- Contract/ADR affected: `docs/QUERY_COHORT_METRICS_V2.md`
- Blocking work: 指标 V2 阶段 02—05、Service 3 正式事实层、S03 指标投影
- Owning session: S00/S04 负责共享契约；S04 是 migration 与生成物单写者
- Date: 2026-08-27

## 1. 状态声明与冻结输入

本文是阶段 01 的自包含冻结候选，不是已接受或已提交事实。当前状态只能写成
`designed_uncommitted`，不得写成 `committed_verified`，也不得据此宣称阶段 02 已解锁。

冻结候选输入如下：

```text
design_path = docs/QUERY_COHORT_METRICS_V2.md
design_status = untracked_worktree_input
design_revision = 4
design_sha256 = 3646d419526df99195f2894297d9993e46f62fec616fe9ea76374e294a43326b
git_head = 806d91348a23b298eed6bff328440e8d51eeaf27
git_origin_master = 806d91348a23b298eed6bff328440e8d51eeaf27
database_current_at_initial_audit = s17_0002_knowledge_trace_details
latest_worktree_head = s18_0001_geo_metrics_v2
latest_database_current = s18_0001_geo_metrics_v2
latest_migration_status = other_session_active/worktree_applied_not_accepted
observed_at = 2026-08-27 Asia/Shanghai
```

设计文件的任意字节变化都会使上述 hash 失效，并使本文退回待复核。阶段门只接受：

1. 上述精确设计输入与本文进入同一可追溯 Git 基线；
2. 本文四个硬 gap 均有明确裁定和验证证据；
3. 当前工作树 migration 写者停止后重新确认唯一 accepted head；
4. 阶段 01 交付被标记为 `committed_verified`。

一个只引用未跟踪设计文件的短说明不能形成权威合同。本文提交前仍只是工作树事实。

## 2. 当前基线审计

当前 **Git 提交基线** 没有指标 V2 物理实现。本文初次审计时，V2 表名、独立
Decision/Metrics worker、`/api/v2/metrics` 路由和规定的 V2 测试只存在于设计输入中。
本文形成后，另一个并发会话越过尚未满足的阶段 01 门，开始写入未跟踪的
`domain/metrics/v2/`、`api/geo_platform/metrics_v2/`，并修改共享热点
`api/geo_platform/tenancy/runtime_acl.py` 与已提交路由 bundle
`api/geo_platform/s02_routers.py`。这些文件统一归为
`other_session_active/worktree_in_progress`：本文不接管、不验证、不把它们写成已接受实现，
阶段 02 仍保持锁定。该活跃会话曾在 `metrics_v2.repository` 尚不存在时接入路由，造成
`pnpm test:python` 的 45 个收集错误；随后补齐 repository，并修正一项权重校验后，本文最新
观测的快速车道为 `2768 passed, 33 deselected in 111.71s`。这只能证明该次未提交工作树运行
通过，不能把越过阶段门、共享热点单写者和未接受 migration 的改动提升为稳定基线。现有已提交
正式路径仍是：

- `domain/scoring/analyzer.py` 的字面提及、单一 rank 和粗粒度 sentiment；
- `domain/metrics/core.py` 的 V1 `MetricRegistry`；
- `workflows/activities/s02.py` 在回答分析 activity 内计算单回答 V1 指标；
- Analytics、Customer、BrandRank 和 report 各自存在聚合或兼容公式；
- `workflows/workers/s02.py` 仍混合回答分析、证据、调查和 LibreOffice 报告活动；
- 已提交基线没有 V2 migration、publication pointer、历史 replay 或 official cutover；当前工作树
  虽随后出现并应用了未跟踪 migration，仍没有提交、阶段门或端到端验证证据。

另有并发 knowledge 会话创建了未跟踪文件，随后指标会话又在阶段门未满足时创建并实际应用
了第二个未跟踪 migration：

```text
migrations/versions/s17_0003_knowledge_version_immutability.py
migrations/versions/s18_0001_geo_metrics_v2.py
worktree_alembic_head = s18_0001_geo_metrics_v2
database_current = s18_0001_geo_metrics_v2
chain = s17_0002_knowledge_trace_details -> s17_0003_knowledge_immutable -> s18_0001_geo_metrics_v2
ownership = other_session_active/worktree_applied_not_accepted
```

这两个文件都不是 accepted successor，也不是本合同可修改、提交或依赖的基线。共享数据库
到达该 revision 只证明未提交工作树 migration 被执行，不把阶段 01 或阶段 02 提升为
`committed_verified`。本会话不回滚或继续修改其他会话的数据库状态。S04 必须在并发写者完成
交接后重新确认已接受的提交链、空库重放、权限与 downgrade/restore，再决定保留、重建或撤回
该 successor；后续实现不得仅凭数据库 current 继续前进。

## 3. 冻结的业务不变量

正式统计固定为：

```text
查询上下文 × 智能语义判定 × 证据化事实/事件 × 版本化测量协议 × 确定性聚合
```

以下语义不可由实现会话改写：

1. 查询事实与回答事件分层保存；`primary_lens` 只导航，不决定分母。
2. 查询可同时具有多个 lens、requested operation、subtype 和相对实体 exposure role。
3. 未受管或歧义实体进入 unknown/review，不能作为未命中或自动进入知识 release。
4. 每个需要理解的结果先形成版本化、可弃权、可审核的原子 decision。
5. 每个回答有逐 capability manifest；零事件与未分析必须可区分。
6. recommendation list rank、market rank claim、pairwise preference、mention order 和
   source result rank 是五种不同事实。
7. Metrics Service 只消费冻结事实，不调用 LLM；GET 和 renderer 不重算项目指标。
8. 每个候选回答在每个指标中恰有一个 hit、miss、excluded、not applicable 或 unknown
   状态，所有分母、权重、排除项和 unknown 可枚举。
9. official 主估计量默认 `query_macro`；额外 retry/repeat 不增加 query 总权重。
10. planned、observed、query-applicable、semantic-known、outcome 和 aggregation 六层总体
    分开保存。
11. collection、query-context、semantic、evidence 四类 coverage 与 missing bounds 分开。
12. V2 先 shadow；只有通过对账、校准和审批后才 CAS 切换 official。
13. 历史签发物固定读取原合同；修正规则产生新事实和新 snapshot，不改旧值。

## 4. 逻辑对象冻结

| 对象                      | 唯一职责                               | 必须冻结的最小内容                                                                                                   |
| ------------------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `QueryContextFact`        | 查询多维上下文事实                     | query identity/text hash、lenses、operations、subtypes、brand structure、字典/release、decision refs、版本链         |
| `QueryEntityExposureFact` | 查询相对每个 focal entity 的暴露关系   | `brand_neutral/focal_named_only/focal_named_with_others/other_brand_named/unknown`、matched entity、fact hash        |
| `DecisionTaskDefinition`  | 定义“判断什么”                         | input/output schema、依赖 DAG、候选政策、method policy、rubric、evidence、abstention、adjudication、calibration gate |
| `JudgePolicy`             | 定义“如何判断”                         | deterministic/proposer/verifier/adjudicator/human pipeline、解析后的模型 revision、阈值、预算、分歧与禁止弱 fallback |
| `SemanticEvidenceBundle`  | 冻结 judge 实际可见证据                | truth-as-of、retrieval policy/query hash、CAS refs、URL、内容 hash、span、状态和 bundle hash                         |
| `SemanticDecisionJob`     | 幂等判定需求                           | task/subject/input/context/policy、generation、状态机、workflow refs、idempotency key                                |
| `SemanticDecisionAttempt` | 保存一次受控执行                       | role/method/model revision、prompt/rubric/schema hash、校验输出、短理由、成本与错误码；不保存思维链                  |
| `SemanticDecisionRecord`  | 最终原子判定                           | accepted/abstained/review/failed、结构化结果、证据、校准、policy/rubric、selected attempts、supersedes               |
| `AnswerSemanticManifest`  | 回答逐 capability 完成状态             | answer/input hash、query fact、task/extractor/dictionary bundle、逐能力状态、decision/event set hash                 |
| `AnswerSemanticEvent`     | 类型化回答事实                         | event type/value、subject/object、Unicode code-point span、excerpt hash、decision provenance、review                 |
| `MetricDefinition`        | 版本化测量协议                         | 类型安全 DSL、unit、query predicate、required capabilities/tasks、outcome source、aggregation、publication gate      |
| `MetricEvaluation`        | 逐 answer × focal entity × metric 结果 | 唯一 eligibility、reason codes、outcome、raw contribution、events/decisions 和 hash                                  |
| `MetricSnapshotSet`       | 原子冻结同一 scope 的指标集合          | tenant/project/window/as-of/filters/entities、design basis、dependency bundle、set hash                              |
| `MetricSnapshot`          | 一个 metric/entity 的不可变统计        | state/value、n/N、coverage、bounds、query count、method mix、calibration refs、三类 contribution hash                |
| `AnswerContribution`      | 全体候选回答贡献                       | hit/miss/excluded/not-applicable/unknown、三层权重、events/decisions、answer detail ref、hash                        |
| `QueryContribution`       | query-macro 验算                       | query numerator/denominator/value、unknown weight、query weight、cell/answer counts                                  |
| `DesignCellContribution`  | planned/observed 采集守恒              | query/model/region/mode、planned/effective/failed/known repeats、cell weight 和状态                                  |
| `MetricPublication`       | 唯一可变消费指针                       | scope、shadow/official、snapshot set、generation CAS、publisher/time                                                 |
| `MetricRecomputeJob`      | 增量/回放运行账                        | trigger/scope/definition、cursor、输入输出数、状态、错误、workflow refs、idempotency                                 |
| `MetricExportArtifact`    | 可验证导出                             | set/hash、格式、私有对象描述、回读校验、权限、保留和审计                                                             |

这些对象不可合并为一个不可约束 JSONB。query fact、decision、manifest/event、evaluation 和
snapshot 是不同事实层，不得用其中一层替代另一层。

## 5. 物理对象与不可变性冻结

实际 migration 至少实现或兼容扩展下列对象：

```text
analytics.query_context_fact_v2
analytics.query_entity_exposure_fact_v2
analytics.semantic_decision_task_definition_v2
analytics.semantic_judge_policy_v2
analytics.semantic_evidence_bundle_v2
analytics.semantic_decision_job_v2
analytics.semantic_decision_attempt_v2
analytics.semantic_decision_record_v2
analytics.answer_semantic_manifest_v2
analytics.answer_semantic_event_v2
analytics.metric_definition                       # additive extension only
analytics.metric_evaluation_v2
analytics.metric_snapshot_set_v2
analytics.metric_snapshot_v2
analytics.metric_contribution_v2
analytics.metric_query_contribution_v2
analytics.metric_design_cell_contribution_v2
analytics.metric_publication_v2
analytics.metric_recompute_job_v2
analytics.metric_export_artifact_v2
reporting.formal_report_production                # additive set id/hash binding
```

共同物理规则：

- 正式比率、权重、校准值使用 `NUMERIC(20,12)`，时间使用 `TIMESTAMPTZ`。
- 除真正全局 definition/policy 外，每行都有 tenant/project scope；新业务表启用 RLS 与
  FORCE RLS，PUBLIC 无业务表或函数权限。
- fact、definition、attempt、decision、event、evaluation、snapshot 和 contribution
  append-only，拒绝 UPDATE/DELETE；job 只按状态机更新，publication 只按 generation CAS。
- accepted decision 必须通过结构、证据和校准门；事件至少引用一个 accepted decision。
- answer manifest 即使 event count 为 0 也存在；一个 capability 失败不抹掉其他能力。
- evaluation 唯一键必须覆盖 answer、focal entity、metric definition 及其全部事实依赖。
- snapshot set 的 `as_of` 是硬读取上限；之后到达的数据只能进入新 set。
- canonical hash 版本固定为 `canonical-json-v1`：UTF-8、对象键排序、UTC ISO-8601、
  Decimal 无指数定点字符串、集合稳定排序、业务数组保序。
- hash 链为 set → snapshot → answer/query/design contributions → decision/event；分页、
  临时 job ID、模型调用顺序和导出格式不参与业务内容 hash。

## 6. 四个必须先裁定的共享硬 gap

### GAP-A：版本控制权威与设计 hash

当前设计输入未被 Git 跟踪，且 v3 总入口记录过更早的不同 hash。阶段 01 只能将精确 hash
`3646d419…a43326b` 的设计文件与本文一同纳入 Git；本文尚未包含足以独立替代该设计的完整
字段、约束/index/trigger、逐角色 ACL、指标 registry/DSL 与机器 manifest，因此不存在“只提交
本文”的捷径。任何一方继续写设计文件、只提交本文但不保存可读取输入、或提交后 hash 不同，
都不满足阶段 01。

### GAP-B：运行数据库角色与表级所有权

当前 runtime ACL 只有 `geo_api` 和 `geo_worker`，而冻结设计要求 analysis、decision、
metrics、report 和 reviewer/operations 最小权限隔离。接受 ADR 必须明确：

- 是否新增 `geo_analysis`、`geo_decision`、`geo_metrics`、`geo_report` 等运行角色；
- 若暂时共享进程，如何仍以不同连接身份保证 analysis 不能写 snapshot、metrics 不能写
  decision、report 不能写业务事实、API 不能伪造 attempt/event；
- 每张表、sequence、function 的 SELECT/INSERT/受控 UPDATE 白名单；
- provision、`runtime_acl.py`、migration reconcile 和 RLS/ACL 测试的单一真源。

在角色方案未接受前，不得创建一组由宽权限 `geo_worker` 任意互写的新表并声称满足隔离。

### GAP-C：transactional outbox 与多下游交付

现有 `integration.outbox_event` 只有一个全局 `published_at`；第一个消费者标记发布后，
不能自然表达多个独立下游均已收到。接受 ADR 必须指定唯一方案及故障语义：

- 使用一个专用 domain-event router，在同一事务把一个 outbox event 扩展为一个或多个
  `workflow_start_command/workflow_signal_command`，成功后再写 receipt/发布状态；或
- 新增支持逐 consumer delivery/receipt 的 v2 outbox 物理合同。

无论选择哪种方案，都必须冻结 required consumer、receipt/idempotency key、重试、毒消息、
乱序、旧版本、correlation/causation 和“事实与事件同事务”规则。Decision、Metrics 和 Report
worker 不得通过竞争同一个 `published_at IS NULL` 队列造成事件丢失。

### GAP-D：共享输入、migration 与生成物单写者

实现前必须冻结三个只读输入投影：

1. query/answer：`query_pub_id/query_key`、原文/normalized text hash、project/run/config、
   model/region/mode/capture time/eligibility 与不可变 answer detail ref；
2. collection design：planned query × model × region × mode、repeat、weight、失败与 observed
   answer 的稳定绑定；S02 不得从时间顺序猜测 S01 slot；
3. knowledge/entity：明确 release ID/hash、字典 hash、managed candidate 与 unmanaged/review
   边界；不得读取当前 dirty BrandRank/knowledge 文件的隐含状态。

同时指定单写者：S04 在其他 migration 写者交接后决定实际 successor；S02 只提交 migration
需求和其 owned domain/API 实现；S03 只消费生成 client；S04 在共享写入停止后唯一运行
OpenAPI/TypeScript client 生成。不得预定 `s18_0001`，不得并行创建 head，不得手改 generated
schema。

## 7. 所有权与允许修改边界

| 范围                                                                               | Owner      | 冻结职责                                                             |
| ---------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------- |
| ADR、contract gap、migration policy、runtime roles、OpenAPI 生成政策               | S00/S04    | 接受四个 gap、单写者集成与阶段门                                     |
| `api/geo_platform/{analytics,evidence,reports,intelligence}`、对应 domain/workflow | S02        | query facts、decision/events、metrics runtime、Service 3/report 后端 |
| 四个 app、共享 UI packages、frontend/visual tests                                  | S03        | 仅消费稳定生成 client；不创建统计事实                                |
| collection/domain 与 Operations execution feature                                  | S01        | 提供 planned design/answer lineage 只读合同；S02/S03 不越权修改      |
| migration、`runtime_acl.py`、root config、generated OpenAPI/client                 | S04 单写者 | 所有生产者停止共享写入后一次集成                                     |

当前 dirty/active 文件必须避让，尤其包括 Operations visibility 手写类型/UI、knowledge/
BrandRank 在途文件和未跟踪 knowledge migration。阶段 01 不借用这些工作树改动作为已接受
实体字典或 migration 证据。

## 8. API 冻结候选

统一前缀为 `/api/v2/metrics`。GET 只读已存在 snapshot，不同步构建。

```text
GET  /catalog
GET  /projects/{project_pub_id}/snapshot-sets/current
POST /projects/{project_pub_id}/snapshot-requests
GET  /snapshot-jobs/{job_pub_id}
GET  /snapshot-sets/{set_pub_id}
GET  /snapshots/{snapshot_pub_id}
GET  /snapshots/{snapshot_pub_id}/queries
GET  /snapshots/{snapshot_pub_id}/contributions
GET  /semantic-events/{event_pub_id}
GET  /semantic-decisions/{decision_pub_id}
GET  /decision-jobs/{job_pub_id}
POST /snapshot-sets/{set_pub_id}/exports
POST /operations/snapshot-sets/{set_pub_id}/publish
POST /operations/recompute-jobs
POST /operations/semantic-decisions/{decision_pub_id}/overrides
```

固定边界：

- Customer 只能读取有权项目的 official/允许的 shadow，并只能请求 shadow。
- publish、recompute、override 使用独立 Operations 权限、idempotency 和 If-Match/CAS。
- 猜测存在但无权访问的 set/snapshot/decision/event/export ID 返回 404。
- contribution 使用绑定 snapshot、排序键和 filter hash 的 opaque cursor；分页不改变合计/hash。
- 总计来自 snapshot，不从当前页或筛选结果重算；响应同时返回 filtered count 与 snapshot
  candidate count。
- API、导出和客户投影不返回思维链、prompt、secret、完整 provider payload、内部对象 key、
  未审核 candidate 或未授权来源正文。

## 9. Domain event 冻结候选

事件名称固定为：

```text
query.context.classified.v2
semantic.decision.requested.v2
semantic.decision.input_ready.v2
semantic.decision.completed.v2
semantic.decision.abstained.v2
semantic.decision.review_required.v2
semantic.decision_task.published.v2
semantic.judge_policy.published.v2
answer.semantic_events.completed.v2
answer.semantic_events.review_required.v2
entity.dictionary.published.v2
metric.definition.published.v2
metric.snapshot_set.requested.v2
metric.snapshot_set.ready.v2
metric.snapshot_set.published.v2
```

通用 payload 只包含 event ID/type/time、tenant/project、subject public ID/version hash、
correlation/causation。判定事件可增加 task/subject/input/policy/job/decision 引用，但不得携带
完整 query/answer/source、prompt 或模型响应。消费者按 event ID 与领域唯一键双重幂等；重复、
乱序、延迟和旧版本不能倒退 current/publication。

## 10. 状态、方法、事件与错误词表

固定状态词表：

```text
fact classification       ready | review_required | failed
decision definition       draft | experimental | published | retired
decision job              pending | running | succeeded | abstained | review_required | failed
decision record           accepted | abstained | review_required | failed
manifest overall          ready | partial | review_required | failed
manifest capability       ready | abstained | review_required | failed | not_requested
metric definition         draft | experimental | published | retired | legacy
evaluation                included_hit | included_miss | excluded | not_applicable | analysis_unknown
snapshot                  ready | limited | insufficient | experimental | failed
snapshot set              ready | partial | failed
publication channel       shadow | official
recompute job             pending | running | succeeded | failed
derivation method         deterministic | model | hybrid | human
task method policy        deterministic_only | model_required | hybrid | human_required
```

固定事件类型：

```text
entity_mention
recommendation_relation
sentiment_or_stance
recommendation_list_rank
market_rank_claim
pairwise_preference
mention_order
source_result_rank
factual_claim
claim_evidence_verdict
citation_relation
risk_event
```

首批机器 reason/error code：

```text
collection_ineligible
query_lens_mismatch
query_operation_mismatch
exposure_mismatch
unknown_query_context
unknown_entity_resolution
semantic_analysis_failed
semantic_review_required
required_event_unknown
required_decision_missing
blocked_on_dependency
semantic_result_unknown
decision_failed
decision_abstained
decision_review_required
decision_not_calibrated
decision_policy_mismatch
judge_disagreement
model_output_invalid
model_unavailable_for_policy
judge_timeout
decision_budget_exhausted
chunk_analysis_incomplete
evidence_retrieval_failed
evidence_span_invalid
no_substantive_entity_event
entity_absent
recommendation_positive
recommendation_conditional_positive
recommendation_negative
recommendation_neutral_or_absent
rank_within_k
rank_above_k
target_not_in_ranked_list
no_rankable_list
target_not_ranked
no_applicable_claim
claim_supported
claim_unsupported
claim_contradicted
claim_unverifiable
claim_verification_unknown
no_pairwise_relation
historical_design_unknown
metric_snapshot_set_not_ready
metric_snapshot_set_hash_mismatch
metric_snapshot_scope_mismatch
metric_definition_not_published
metric_dependency_not_ready
metric_publication_generation_conflict
metric_publication_gate_failed
semantic_task_not_published
semantic_input_hash_mismatch
semantic_override_conflict
```

新增 code 必须版本化并有服务端/前端映射；自由文本只能作为附加审计说明，不能替代 code。

## 11. V1、shadow、cutover 与 rollback

1. V1 表、旧 API、旧代码和历史签发报告只读保留，标为 legacy；不删除、不批改。
2. V2 先写 query/decision/event/evaluation，再生成完整 shadow snapshot set。
3. 历史回放只读已有 query/answer/evidence，不重新采集，不用旧单一 rank 猜新 rank type。
4. V1/V2 差异按 query exposure、entity resolution、rank semantics、unknown、weight、
   collection missing 和真实 regression 分类；目标不是数值相等。
5. Analytics、Customer、BrandRank、SOP、target/delta、Operations 和 reports 全部接通同一
   set 后，才允许一次 official generation CAS。
6. official 不存在或状态不足时诚实返回 pending/insufficient，不自动回退 V1。
7. rollback 只 CAS 指回上一份已验证 V2 set；不删除 V2 事实，不改历史报告，不恢复混合
   V1 公式。
8. report request 固定保存 set ID/hash；renderer、DOCX/PDF/HTML/Customer/XLSX 不跟随 current。

## 12. 最小测试门

阶段 01 接受时至少要有一份机器可读 contract manifest 或等价测试，验证：对象名、状态词表、
reason code、事件名、owner、唯一 migration writer 和设计 hash。后续阶段必须新增设计指定的
query context、decision、semantic event、metric DSL/evaluation/weight/hash/missing bounds、RLS、
repository/outbox/Temporal、API/export/report binding、frontend trace 和 E2E 测试。

特别门禁：

- Git 提交基线没有设计指定的 V2 测试；并发工作树后来出现的 V2 代码/测试即使局部通过，
  在阶段门、所有权和共享热点裁定前也不能宣称阶段完成；
- 当前并发 V2 工作树的 Python 快速车道最新虽通过，但它在同次审计中曾先后出现缺 repository
  的 45 个收集错误和权重合同失败；阶段 01 接受前必须由原写者在合规阶段形成完整、可追溯且
  可重复验证的提交，不得把一次活跃工作树绿灯续作下游基线；
- migration 必须从 empty 重放到执行时唯一 accepted head，并验证 RLS/FORCE RLS/ACL；
- patch 掉模型 client 后 Metrics 仍可从冻结 decision 重建同一 hash；
- patch 掉 snapshot engine 后 GET 仍能读；
- report 缺指定 set 时返回 `metric_snapshot_set_not_ready`，不调用 legacy 公式；
- Customer/Analytics/BrandRank/SOP/report 的 official 调用图中 legacy 计算器为零。

## 13. 阶段 02 启动门

当前判定：`not_satisfied`。

只有以下全部成立，S02 才能开始阶段 02 业务实现：

- [ ] 精确设计 hash 与本文已经进入 Git，状态改为 `committed_verified`。
- [ ] GAP-A 至 GAP-D 均有 accepted resolution，而非实现者自行选择。
- [ ] 并发 knowledge migration 已完成交接；S04 重新确认唯一 accepted head 和实际 successor。
- [ ] migration、runtime ACL、outbox、OpenAPI/client 单写者已命名并停止争用共享热点。
- [ ] query/answer、planned design cell、knowledge/entity release 的只读合同可测试。
- [ ] S02 获得不会修改 S01/S03 文件的目录/文件清单与 fixture。
- [ ] 未提交 Operations visibility、knowledge、BrandRank、测试/CI 改动均保持原 owner。

在门满足前，允许的工作只有只读审计、本文/ADR 的契约收敛和不改变产品真相的 fixture
设计；不得创建 V2 migration，不得实现 Decision/Metrics 业务路径，不得让 S03 编写页面公式，
不得生成 OpenAPI/client，也不得把 Service 3 原型扩展成第二套 event/entity/rank schema。

## 14. Resolution and validation

```text
resolution = pending
validation = design hash observed; worktree fast lane 2768 passed/33 deselected; s18 head/current audited
stage_01 = designed_uncommitted
stage_02 = locked
production/application deployment = not attempted
shared development database migration = applied_unaccepted_by_other_session
commit/push = not authorized and not performed
```
