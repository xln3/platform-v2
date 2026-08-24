# ADR-0008: 三采集来源 v2 领域、身份与执行隔离契约

Status: Accepted · 2026-08-24

Owner: GEO Platform

## Context

现有采集合同只表达平台、地域、模式和运行触发来源。它不能把 Provider API、消费者 Web 和消费者 App 作为互不替代的采样维度。现有配置、任务、Temporal payload、配额、证据和报告也没有共享的 surface、主样本或 submission identity。

本决定补充 ADR-0002、ADR-0003 和 ADR-0007。阶段 0 事实基线见 `docs/audits/COLLECTION_THREE_SURFACE_STAGE0_BASELINE_20260824.md`。

## Decision

### 1. 术语真源

1. 内部唯一采集来源字段是 `collection_surface`。它只允许 `provider_api`、`consumer_web`、`consumer_app`。
2. `CollectionRun.source` 保留为 `run_trigger_source` 语义。它只表达 manual、schedule、retry、training 等触发来源。
3. `platform` 表达平台家族。`product_variant` 表达平台内具体产品。`interaction_mode` 表达经能力声明的交互模式。
4. `province_code` 表达业务地域。`route_id` 和 `route_revision` 表达执行出口。执行 route 不改写业务地域。
5. `capture_channel` 表达 API payload、DOM、截图或设备画面等证据传输方式。它不能推导 `collection_surface`。
6. 正式结果同时保存 `requested_surface`、`observed_surface`、requested/observed product。两者不一致时结果隔离，不能进入正式 answer、analysis、denominator 或 report。
7. 三个 surface 之间禁止 fallback。surface 内部可以按冻结策略更换资源，但不能改变被观测产品。

### 2. 配置和身份语法

1. v2 配置 schema 固定为 `collection-config-v2`。配置是不可变 canonical revision。服务端单点规范化并计算 hash。
2. 配置只显式列出 `collection_targets`。target key 固定为 `platform + collection_surface + product_variant`。target 内显式列出允许的 interaction modes。
3. campaign 在一次排程或人工启动被接受时冻结 config revision、question set revision、target membership、province、mode、window 和 sample ordinals。
4. sampling leg key 固定为 `target_key + province_code + interaction_mode`。
5. primary slot key 固定为 `campaign + question_slot + sampling_leg + sample_ordinal + window + role`。`role` 只允许 primary、supplementary、topup。补采不能无痕替换 primary。
6. logical item 对应一个冻结 slot。attempt 表达物理执行尝试。attempt id、worker id、物理资源和当前时间都不能改变 logical identity。
7. submission operation key 固定为 `slot_key + operation_generation`。同一 generation 最多进入一次外部 submit 边界。
8. `CONFIRMED_SENT` 和 `SEND_UNKNOWN` 都禁止再次发送。只有 gateway 证明 `CONFIRMED_NOT_SENT` 且恢复政策明确允许时，才可创建下一 generation。
9. answer、evidence、analysis、export 和 report 必须通过稳定 FK/identity 回溯到 config、campaign、target、slot、operation 和 requested/observed product。

### 3. Capability registry

1. capability 唯一 key 是 `platform + collection_surface + product_variant + interaction_mode`。
2. 每条记录必须包含 registry schema version、capability revision、`supported/pilot/unsupported`、production allowed、region policy revision、required typed resource kinds、evidence/capture schema、产品版本约束和机器可读 reason。
3. 静态 `unsupported` 在 candidate/freeze 前拒绝。`pilot` 不能默认进入 production。
4. 动态 readiness 不是 capability 状态。它是 activation/start 时按 binding、grant、resource、quota、route 和 worker compatibility 生成的版本化 admission snapshot。
5. fake target、fixture 和 mock 只能证明合同纵切。它们不能把 capability 标记为 live。

### 4. Quota scope registry

1. 配额使用版本化 policy/scope registry，不使用一个账号的单一累计计数代表全部限制。
2. 每个 scope 定义必须包含 `scope_kind`、subject resolver、适用 surface/product/mode、共享或独立策略、window kind、timezone、边界规则、unit、limit source 和 send-truth settlement policy。
3. canonical bucket key 固定为 `policy_revision + scope_kind + subject + dimension_key + window_start + window_end`。
4. 一次 operation 必须解析全部适用 bucket，并按 canonical lock order 在一个短事务内全部预占。任一 bucket 不足时全部不占用。
5. reservation、settlement 和 release 都以 operation/bucket 唯一 effect 记账。answer 缺失不能反推未发送，也不能释放已发送或 unknown 的用量。

### 5. v1 冻结与 v2 隔离

1. 现有 `geo_collection`、`GeoCollectionWorkflow`、旧 DTO、旧 business key、旧 patch marker 和旧 activity command shape 定义为 v1 冻结合同。不得给 v1 writer 增加必填 surface，也不得改变旧命令序列。
2. 所有 v1 历史正式采集事实按用户确认解释为 `consumer_web`。该解释通过版本化 reader/backfill overlay 提供，不改写旧 config JSON/hash、sealed raw artifact 或 Temporal history。
3. 生产库 498 个 v1 run 全部处于运行终态。3,104 个 task 全部隶属终态 run。34 个遗留 `awaiting_intervention` task 没有 answer，并被本决策声明为已关闭的遗留 outcome，不允许重新打开。
4. v1 禁止新启动、schedule、reset、retry 或 reopen。v1 task queue 不再承接采集 poller。若以后出现旧 history 或 reset/retry 请求，系统必须隔离并 fail closed。
5. v2 使用新 outbox workflow type `geo_collection_v2`、新 Temporal workflow type `GeoCollectionV2Workflow`、新 task queue `geo-platform-v2-collection-v2` 和显式 `collection-workflow-v2` payload。名称变化须由后续 ADR supersede，不能静默改义。
6. v2 writer 不允许 surface 默认值。v1 reader 不能成为 v2 writer 的 fallback。新旧 queue、payload 和 worker 必须部署隔离。

### 6. 旧真实 history 风险接受

1. 当前生产 Temporal 中不存在可导出的旧 collection history。仓库也没有脱敏的真实 collection replay corpus。
2. 用户于 2026-08-24 接受 `unverified_legacy_v1_history_replay` 为不可恢复的遗留风险。任何文档或测试不得把 synthetic replay 描述成真实 replay 通过。
3. 该风险不再阻塞 v2 领域、schema、fake-target、shadow 和产品开发。其依据是 v1 run/task 已按本决策关闭，且 v1 reset/retry/start 被禁止。
4. 该风险不授权把 v1 history 路由到 v2 worker，也不授权恢复任何 v1 外部副作用。

### 7. Retention、归档和发送边界

1. Temporal `default` namespace 保持 30 天热保留。阶段 0 的 history archival 和 visibility archival 均为 disabled。
2. v2 history/visibility 归档的存储 URI、加密、访问审计、生命周期和 replay/restore 验证在 live rollout 前完成。阶段 0 不启用或伪造归档。
3. 本 ADR 不授权真实发送。`provider_api`、`consumer_web` 和 `consumer_app` 的真实 canary 必须另获用户明确授权，并同时具备 active target、formal binding、typed grant、current fence、全部 quota reservation 和 durable operation。
4. 未授权时只允许 read-only audit、shadow 和 fake target。不得向真实 provider、网页或 App 提交查询。

## Consequences

- v2 migration 必须 additive，并从当前唯一 Alembic head 继续。
- 当前代码中的旧 `channel=api` 不能迁入 canonical v2 facts。
- 当前没有任何 target 满足 v2 live admission。旧 Web adapter 可作为迁移输入，但不是正式 v2 binding 或 live capability。
- analytics 和 report 必须按 surface 维护 configured、collectable、attempted、confirmed 四类分母，并只对严格 matched slots 做跨 surface 比较。
- 本 ADR 接受的是不可恢复的 v1 replay 证据缺口，不是 v2 发送、rollout、归档或数据正确性的豁免。
