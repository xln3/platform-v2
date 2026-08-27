# Knowledge API 与事件契约

基础路径是 `/api/v2/knowledge/v1`。请求和响应由严格 Pydantic schema 生成 OpenAPI，未知字段被拒绝。独立客户端位于 `domain.knowledge_evolution.client.KnowledgeHttpClient`，不依赖 GEO 业务模块。

## 端点

| 能力 | 端点 | 权限 |
| --- | --- | --- |
| 运行时解析 | `POST /runtime/resolve` | `knowledge:resolve` |
| 批量观察 | `POST /observations:ingest` | `knowledge:observe` |
| 候选列表/重开 | `GET /candidates`、`POST /candidates/{id}/reopen` | read/review |
| 提案 | `GET/POST /proposals` | read/propose |
| 证据 | `GET/POST /evidence` | read/evidence |
| 裁决 | `POST /proposals/{id}/adjudications` | review |
| 变更集 | `POST /change-sets`、`POST /change-sets/{id}/approve` | review |
| 发布 | `GET/POST /releases` | read/publish |
| 激活/回滚 | `POST /releases/{id}/activate`、`POST /releases/{id}/rollback` | publish |
| connector | `GET/POST /connector-runs` | read/connector |
| 审计/事件 | `GET /audit-events`、`GET /events` | audit |
| 运维 | `GET /health`、`GET /readiness`、`GET /metrics`、`GET /domains` | health/read |

客户可解析和提交观察。operator/analyst 可读、提案和补证。reviewer 可裁决和读取审计。只有 admin 具有 connector、publish 和全局权限。四眼约束在 repository 层再次执行，不能靠伪造角色绕过。

## Runtime 契约

调用方必须给出 namespace、domain、task、items、policy id/version 和数据分级。跨系统共享品牌反馈使用 `namespace=shared`；旧 `geo-brandrank` 观察由脱敏迁移工具幂等复制，不再形成项目孤岛。默认策略是 `deterministic_only`，默认不允许外部模型，默认不采用模型推理。

模型采用需要三个显式条件：模型策略允许调用、`allow_external_model=true`、`adopt_model_inferred=true`。返回值分别包含 effective `decisions` 和未必采用的 `model_hypotheses`。每个 decision 带 release、policy、prompt、model、tool、evidence、confidence、status、scope 和 adopted 标识。

`on_model_failure=degrade` 返回确定性结果和稳定 degradation code。`on_model_failure=fail` 返回 503。超时、非法 JSON、工具错误、工具越权、工具轮次上限和 provider 不可用都有独立错误码。

`llm_assisted` 只把 unresolved input 交给模型并只允许这些 input 被采用。`confidential/restricted` 不允许外发。gateway 的 `GEO_KNOWLEDGE_LLM_MAX_RETRIES` 缺省为 1，覆盖 408、429、5xx 和传输失败；备用 endpoint 在单端点重试耗尽后使用。设置 max cost 但 provider 未返回可核验费用时，结果披露 `cost_budget_unverifiable` 且不采用。

cache read/write、observation 和 trace 失败分别披露 `semantic_cache_read_failed`、`semantic_cache_write_failed`、`observation_persistence_failed` 和 `trace_persistence_failed`。这些可选反馈故障不改变已经得到的当次 decision。调用方不能把 degradation 当成持久化成功回执。

## Observation 契约

观察批次最多 500 条。source reference 必须先做 SHA-256。idempotency key 只能包含安全字符。payload 上限为 64 KiB。服务返回 accepted、duplicate 和 receipt id。相同请求重试只返回 duplicate；不同应用的同一规范名称会聚合到同一 `shared` candidate，但 observation 仍保留各自不可逆来源收据。

调用方不得把完整回答作为 safe context。GEO 品牌接入给模型的上下文只保留命中名称前后各 160 字，并在发送前遮盖 URL、邮箱、电话和账号样式；名称未出现在回答中时不发送回答片段。持久 observation 只保存受控的领域、scope、任务、地域和受众字段，以及调用方摘要的不可逆 hash，不保存项目 ID、客户名、完整问题或完整回答。

批准 change set 前，每个 change 必须提交与对应 proposal 精确匹配的全部 `evidence_pub_ids`。存在 authoritative/primary `opposes` evidence 时 approval 返回冲突。终态 proposal 不能再次 adjudicate；reopen 只能由新 evidence version、新 policy version 或 reviewer 显式 `manual_override=true` 触发。

品牌 release 还必须提交 `historical_replay-v1`：`evaluation_set_hash`、带时区的 `time_cutoff`、评测请求数、基线/候选错误数、修复数、新错误数、允许的新错误数和 `passed=true`。缺字段或新错误超过预算时返回 `historical_replay_gate_failed`。该报告会进入不可变 release manifest。

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
