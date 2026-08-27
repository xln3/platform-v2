# 知识中间件故障手册

## 判定原则

先保护已验证的本地 release，再恢复增量能力。不要为了消除告警切换到未验证 snapshot，不要把模型失败伪装成“未识别”，也不要把 upstream 数据直接覆盖 local-ahead 变更。

## 常见错误

| 现象/错误码                                                                             | 含义                                            | 处理                                                                                         |
| --------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `model_denied_by_data_policy`                                                           | 调用方未允许外发或数据级别禁止                  | 使用确定性结果；确认政策后再显式允许                                                         |
| `model_unavailable`                                                                     | 未配置模型 gateway                              | deterministic/降级继续；配置后做低风险 smoke                                                 |
| `model_timeout`、`provider_unavailable`                                                 | provider 或网络故障                             | 检查 fail/degrade 契约、fallback 和预算；不改知识状态                                        |
| `model_invalid_json`、`invalid_model_output`                                            | 输出不符合 schema 或引用非法                    | 保留 deterministic；检查 prompt/model version                                                |
| `cost_budget_exceeded`、`cost_budget_unverifiable`                                      | 费用超过预算或 provider 没有返回可核验费用      | 不采用模型结果；配置定价 adapter 或调整明确预算                                              |
| `tool_not_allowed`、`tool_failure`、`tool_round_limit`                                  | 工具越权、执行失败或循环                        | 检查部署 allowlist；不要扩大任意工具权限                                                     |
| `semantic_cache_*_failed`、`observation_persistence_failed`、`trace_persistence_failed` | 可选运行反馈存储失败                            | 当前 decision 可继续；恢复数据库并核对积压，不声称反馈已持久化                               |
| `knowledge_release_mismatch`                                                            | 调用方要求的 release 不是 active                | 重试指定 release 或明确升级，不静默换版本                                                    |
| `immutable_release_content_mismatch`                                                    | 同 release id 字节发生变化                      | 立即停止激活；从备份恢复；创建新 release id                                                  |
| `release_materialization_mismatch`                                                      | artifact 与该 release 的数据库对象/断言不一致   | 两边指针保持原位；核查 migration/backfill/change set，禁止只移动 artifact pointer            |
| `domain_quality_gate_failed`                                                            | 本体、证据或引用未通过                          | 修正 change set；不得在 API 外绕过 gate                                                      |
| `historical_replay_gate_failed`                                                         | 品牌变更缺少可重复回放报告，或新错误超过预算    | 用截止时间以前的数据重跑；保存评测集 hash；修复退化或由审核人降低变更范围，不能伪造 `passed` |
| `change_set_has_conflicts`                                                              | 三方合并同字段双写                              | reviewer 逐字段裁决并生成新 change set                                                       |
| `contradictory_authoritative_evidence_requires_resolution`                              | proposal 同时存在高等级支持和反对证据           | 延期/拒绝并核查冲突；不得删除反对证据后强行批准                                              |
| `proposal_already_adjudicated`                                                          | 尝试覆盖终态裁决                                | 按新证据/政策/人工 override 重开 candidate，并创建新 proposal                                |
| `change_evidence_lineage_*`                                                             | change 没有完整匹配 proposal evidence           | 补齐全部 evidence public ID，重新创建 change set                                             |
| `append_only_table:*`                                                                   | 尝试更新历史表                                  | 改为追加新事件/裁决，不关闭 trigger                                                          |
| connector `degraded`/`failed`                                                           | 远端失败、坏 hash 或发布回读不一致，但 LKG 可用 | 本地继续运行；保留 run receipt；修复远端后从共同 base reconcile                              |

## Readiness 故障

readiness 同时检查 PostgreSQL 可达和 active release 可验证。模型 gateway 是可选项，disabled 不会让 deterministic 服务不 ready。release 未初始化或 hash 错误时 readiness 为 `not_ready`；health 为 `degraded`。

## 中间件短暂不可用

GEO 品牌请求捕获受控 repository/release/SQLAlchemy 错误。调用方选择 `degrade` 时优先使用已经通过 `/releases/{id}/replica` 安装并验哈希的完整 LKG；注册了领域包时可以处理新的确定性请求。没有领域包时只能复用完全相同请求的内容寻址缓存。两种情况都在 `knowledge_reasoning.degradation` 披露故障，且不能声称观察或 trace 已写入。选择 `fail` 时返回 503。程序错误不被宽泛吞掉。

## 数据库故障

禁止通过禁用 RLS 或 append-only trigger 恢复写入。先切 deterministic/LKG，保留失败 trace 的稳定错误码。恢复数据库后检查 migration head、tenant context、积压候选和 connector cursor，再恢复治理写入。

## 模型成本或时延异常

检查 model_call_count、model_latency_avg_ms、model_cost_usd、cache_hits 和 inference trace。降低模型流量时优先切换 policy 或 adoption，而不是改变输出状态。超过请求 max latency/cost 时模型假设可以返回，但不会被采用。

## SiliconIndex 故障

同步器对坏 hash、重复 ID、断裂引用、跨 origin endpoint、不可排序/倒退版本和官方 schema 失败 fail closed。失败不会推进 CURRENT。API 请求不应出现访问 Render 的连接或超时日志；若出现，视为架构回归。`1.2.0` snapshot 缺失本地 schema 时可使用已验证的内容寻址共享 schema bundle，但不得修改旧 release 目录；`1.1.x` 兼容校验必须明确标为 legacy manual validation。

外发发布失败时保留临时 clone 日志和 connector receipt。禁止直接在长期工作树改公共 JSON、禁止用 caller 自报 approval、禁止 force push。若目标版本已经公开，先比较完整内容 hash：相同才可幂等记为 `already_published`，不同则是不可变版本冲突。

## 升级回退

代码回退和知识回滚是两个动作。先回滚 active knowledge release，再回退应用版本。数据库 migration `s17_0001` 在存在知识历史后故意拒绝 downgrade；`s17_0002` 在存在 inference trace 时也拒绝删除 adoption/tool 血缘；`s17_0004` 的 release membership 是正确回滚所必需的数据，不能在存在多个 release 时删除。恢复旧应用应保持 schema 向后兼容，不能删除审计历史。
