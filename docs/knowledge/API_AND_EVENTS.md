# Knowledge API 与事件契约

基础路径是 `/api/v2/knowledge/v1`。请求和响应由严格 Pydantic schema 生成 OpenAPI，未知字段被拒绝。独立客户端位于 `domain.knowledge_evolution.client.KnowledgeHttpClient`，不依赖 GEO 业务模块。

## 端点

| 能力          | 端点                                                            | 权限                |
| ------------- | --------------------------------------------------------------- | ------------------- |
| 运行时解析    | `POST /runtime/resolve`                                         | `knowledge:resolve` |
| 批量观察      | `POST /observations:ingest`                                     | `knowledge:observe` |
| 候选列表/重开 | `GET /candidates`、`POST /candidates/{id}/reopen`               | read/review         |
| 提案          | `GET/POST /proposals`                                           | read/propose        |
| 证据          | `GET/POST /evidence`                                            | read/evidence       |
| 裁决          | `POST /proposals/{id}/adjudications`                            | review              |
| 变更集        | `POST /change-sets`、`POST /change-sets/{id}/approve`           | review              |
| 发布          | `GET/POST /releases`                                            | read/publish        |
| 副本下载      | `GET /releases/{id}/replica`                                    | read                |
| 激活/回滚     | `POST /releases/{id}/activate`、`POST /releases/{id}/rollback`  | publish             |
| connector     | `GET/POST /connector-runs`                                      | read/connector      |
| 审计/事件     | `GET /audit-events`、`GET /events`                              | audit               |
| 运维          | `GET /health`、`GET /readiness`、`GET /metrics`、`GET /domains` | health/read         |

客户可解析和提交观察。operator/analyst 可读、提案和补证。reviewer 可裁决和读取审计。只有 admin 具有 connector、publish 和全局权限。四眼约束在 repository 层再次执行，不能靠伪造角色绕过。

## Runtime 契约

调用方必须给出 namespace、domain、task、items、policy id/version 和数据分级。跨系统共享品牌反馈使用 `namespace=shared`；旧 `geo-brandrank` 观察由脱敏迁移工具幂等复制，不再形成项目孤岛。默认策略是 `deterministic_only`，默认不允许外部模型，默认不采用模型推理。

模型采用需要三个显式条件：模型策略允许调用、`allow_external_model=true`、`adopt_model_inferred=true`。返回值分别包含 effective `decisions` 和未必采用的 `model_hypotheses`。每个 decision 带 release、policy、prompt、model、tool、evidence、confidence、status、scope 和 adopted 标识。

`on_model_failure=degrade` 返回确定性结果和稳定 degradation code。`on_model_failure=fail` 返回 503。超时、非法 JSON、工具错误、工具越权、工具轮次上限和 provider 不可用都有独立错误码。

`llm_assisted` 只把 unresolved input 交给模型并只允许这些 input 被采用。`confidential/restricted` 不允许外发。gateway 的 `GEO_KNOWLEDGE_LLM_MAX_RETRIES` 缺省为 1，覆盖 408、429、5xx 和传输失败；备用 endpoint 在单端点重试耗尽后使用。设置 max cost 但 provider 未返回可核验费用时，结果披露 `cost_budget_unverifiable` 且不采用。

cache read/write、observation 和 trace 失败分别披露 `semantic_cache_read_failed`、`semantic_cache_write_failed`、`observation_persistence_failed` 和 `trace_persistence_failed`。这些可选反馈故障不改变已经得到的当次 decision。调用方不能把 degradation 当成持久化成功回执。

## 客户端最后验证副本

`GET /releases/{id}/replica` 返回经过服务端重新验哈希的不可变完整 release，不是数据库当前行的临时拼接。`KnowledgeHttpClient` 可把它安装到调用方自己的内容寻址目录；安装时同时验证 release id、manifest hash、文档 hash 和领域 schema。服务短暂不可达或返回 5xx 时，客户端可以用注册的领域包在该副本上处理新的确定性请求，并返回 `last_known_good_replica` 降级原因。它不能在本地偷偷调用模型、提交观察或假装治理写入成功。

如果调用方没有安装领域包，只允许复用输入、必要上下文、知识版本、政策、提示词、模型和工具版本都完全相同的内容寻址响应缓存；不能把一个旧请求的答案泛化到新请求。客户端要求的 release 与副本不一致时必须失败，不得静默换版本。

## Observation 契约

观察批次最多 500 条。source reference 必须先做 SHA-256。idempotency key 只能包含安全字符。payload 上限为 64 KiB。服务返回 accepted、duplicate 和 receipt id。相同请求重试只返回 duplicate；不同应用的同一规范名称会聚合到同一 `shared` candidate，但 observation 仍保留各自不可逆来源收据。

调用方不得把完整回答作为 safe context。GEO 品牌接入给模型的上下文只保留命中名称前后各 160 字，并在发送前遮盖 URL、邮箱、电话和账号样式；名称未出现在回答中时不发送回答片段。持久 observation 只保存受控的领域、scope、任务、地域和受众字段，以及调用方摘要的不可逆 hash，不保存项目 ID、客户名、完整问题或完整回答。

批准 change set 前，每个 change 必须提交与对应 proposal 精确匹配的全部 `evidence_pub_ids`。存在 authoritative/primary `opposes` evidence 时 approval 返回冲突。终态 proposal 不能再次 adjudicate；reopen 只能由新 evidence version、新 policy version 或 reviewer 显式 `manual_override=true` 触发。

品牌 release 的影响报告由服务端领域包生成和执行，不接受调用方自行填写“通过”计数。报告使用 `historical_replay-v2`，绑定 `evaluation_set_hash`、带时区的 `time_cutoff`、评测请求数、基线/候选原子错误数、修复数、新错误数、允许的新错误数、候选状态 hash 和 `passed=true`。缺字段、状态 hash 不一致或新错误超过预算时返回 `historical_replay_gate_failed`。报告及其 hash 会进入不可变 release manifest。

激活前还会把 artifact 的领域逻辑视图与该 release 在 PostgreSQL 中的对象/断言成员关系逐项比较。两边不一致时返回 `release_materialization_mismatch`，artifact `CURRENT` 和数据库 active release 都不移动。回滚执行同一检查，因此回滚改变的不只是文件指针，也会把所有读取限定到目标 release 的数据库成员关系。

## `knowledge-event-v1`

`GET /events` 是按发生时间升序返回的 pull contract。消费者可传 `after` ISO-8601 时间和 `limit`。每个 envelope 包含：

- `schema_version=knowledge-event-v1`；
- event id/type/time；
- tenant、namespace 和 domain；
- resource type/id；
- bounded payload；
- payload SHA-256。

事件来自 append-only audit stream。消费者必须用 event id 幂等处理。`after` 是时间游标；同一时间戳下消费者仍应保存 event id 去重。当前第一阶段不承诺 broker push；需要 Kafka/NATS 时，adapter 从该契约发布，而不是读取内部表。

## 兼容性规则

v1 只允许增加可选字段。删除字段、改变状态语义、改变默认模型采用或更换哈希定义需要新 API/event major version。domain artifact schema 独立版本化，不能借 API 版本隐式变化。
