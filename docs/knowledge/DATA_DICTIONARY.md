# 知识演进数据字典

所有持久对象位于 PostgreSQL `knowledge` schema。每个租户对象都带 `tenant_pub_id`、`namespace` 和 `domain`。RLS 使用事务级 `app.tenant_pub_id`。业务调用不得直接查询这些表，应使用 v1 API。

## 核心对象

| 对象 | 用途 | 关键字段 | 历史语义 |
| --- | --- | --- | --- |
| `observation` | 保存一次安全、幂等的业务观察 | task、surface form、normalized key、source type、source ref hash、idempotency key、classification、visibility | append-only |
| `candidate` | 按租户、namespace、domain 和规范键聚合观察 | variants、observation/source count、priority、state、policy/evidence version | 可更新 read model |
| `candidate_observation` | 保存候选与观察的多对多血缘 | candidate id、observation id | append-only |
| `proposal` | 建议新增、修改、合并、拆分或退役知识 | target、payload、alternatives、confidence、model/prompt/policy provenance | 追加提案；状态只按工作流推进 |
| `evidence` | 保存支持、反对或中立证据 | URI、content hash、publisher、claim、summary、trust tier、classification | append-only |
| `adjudication` | 保存一次批准、拒绝或延期决定 | before/after、reason、policy version、decider | append-only，不覆盖旧裁决 |
| `change_set` | 表达相对 base release 的结构化变更 | changes、dependencies、conflicts、visibility、creator、approver | 审批后不可静默改写 |
| `knowledge_object` | 当前可查询的稳定对象版本 | stable id、type、attributes、origin、review/visibility/sync status、validity、version | 每次变化增加版本 |
| `assertion` | 表达带范围和证据的关系或属性 | subject、predicate、object/value、scope、evidence refs、epistemic status、confidence | 追加版本 |
| `knowledge_release` | 记录不可变发布产物 | release id、parent、schema、content hash、artifact URI、quality report | append-only |
| `release_activation` | 记录激活或回滚 | release、previous release、action、actor | append-only |
| `connector_run` | 记录 import/export/publish/reconcile | adapter、operation、base/upstream/local release、cursor、result、error | 每次运行一行 |
| `inference_trace` | 保存脱敏的推理血缘和计量 | request/input hash、policy、release、prompt/model/tool、adoption、latency、token/cost、cache、degradation | append-only |
| `semantic_cache` | 保存版本化语义缓存 | full cache key、structured value、hit count | release/policy/prompt/model/tool 变化即失效 |
| `audit_event` | 保存安全审计事实并投影为事件 | actor、action、resource、bounded receipt、occurred at | append-only |

## 认识状态

| `knowledge_status` | 含义 | 可否成为全局事实 |
| --- | --- | --- |
| `published` | 来自已发布公共知识 | 已是发布事实 |
| `reviewed_local` | 来自本机审核并发布的知识 | 仅在本机 release 范围成立 |
| `model_inferred` | 当前模型推理 | 不可自动升级；可被明确政策采用到当前请求 |
| `unresolved` | 证据或解析不足 | 进入候选，不进入正式榜单 |

`decision_scope` 独立表达 `request`、`project_staging`、`domain_candidate` 或 `global_release`。认识状态、作用域、关系和竞品资格不能互相替代。

## 状态机

候选主流程是：

`observed → aggregated → proposed → evidence_pending → review_ready → approved | rejected | deferred → local_published → exported → externally_published → reconciled | superseded`

当前数据库将事件历史与当前 read model 分开保存。拒绝或延期候选只有在新增 evidence version、policy version 变化或人工给出明确原因时才能 `reopen`。

人工重开必须由具有 review 权限的调用方显式提交 `manual_override=true` 和原因。普通周度任务既不能重复提案，也不能用相同 policy/evidence version 重开。proposal 一旦 approved/rejected/deferred 就保持终态；新证据可以追加，但必须重开候选并创建新 proposal，不能覆盖旧 adjudication。

## 哈希和幂等

`source_ref_hash`、`content_hash` 和 `payload_hash` 使用带 `sha256:` 前缀的 64 位十六进制摘要。观察唯一键包含租户、领域和 idempotency key。相同观察重复提交返回 duplicate，不增加候选次数。

release 的 `content_hash` 覆盖按 domain 排序并规范化编码的全部领域 artifact。manifest 保存该 hash、artifact 路径、父版本和质量报告；备份 manifest 另行逐文件校验。相同 release id 只能对应相同知识内容；内容不同时发布会失败。

每个 change 必须列出与其 approved proposal 精确绑定的全部 `evidence_pub_ids`。release quality report 保存 `change_set_pub_ids`，因此可以从 release 反向追溯变更集、proposal、evidence、adjudication、candidate 和 observation。

## 隐私字段

公开知识只允许公开表面名称、公开关系、公开证据 URL 和必要摘要。客户项目名、原始问题、完整回答、tenant、prompt、credential 和未脱敏上下文不能进入公共 connector export。

运行时观察只保存不可逆 source reference、短安全上下文和受限 payload。`confidential` 或 `restricted` 请求不能发送给外部模型。模型 provider 错误只保存稳定错误码，不保存响应体、密钥或异常详情。
