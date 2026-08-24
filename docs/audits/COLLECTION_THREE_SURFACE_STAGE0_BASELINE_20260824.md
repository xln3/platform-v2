# 三采集来源阶段 0 基线审计（2026-08-24）

## 结论

阶段 0 文档门已完成。冻结审计提示词指纹、仓库 HEAD、唯一 Alembic head、生产事实计数、Temporal 保留状态、资源现状和聚焦测试证据均已核验。经用户明确确认，生产 `default` namespace 已从 24 小时延长为 720 小时；该在线配置更新没有重启服务或启动 workflow。没有 migration、产品功能部署、采集服务启停或真实外部发送。

旧真实 collection history 已不可取得。用户接受其为 `unverified_legacy_v1_history_replay` 遗留风险，并同意它不再阻塞 v2 开发。v1 全部 run 已终态，所有 task 均隶属终态 run；v1 reset/retry/reopen/start 被 ADR-0008 禁止。

## 权威输入与冻结门

- 已按顺序完整读取主入口 `03-three-surface-collection-contract-and-collectors.md` 及五个强制附件。
- 冻结文件 `docs/audits/COLLECTION_EXECUTION_GRANT_IMPLEMENTATION_PROMPT_20260821.md`：SHA-256 `6ea3bdec14175dd3b58a122840754feaeba0cba4777eadc9520bbced3001de1b`，5,847 行，705,881 字节；三项均与期望一致。
- 当前仓库 HEAD：`87a35c7`。

## 工作树保护

开工前工作树已脏。已有改动属于其他任务，本阶段不覆盖、不格式化、不生成共享产物，也不执行 reset、checkout 或 clean。

- 开工基线已有 17 个 tracked 文件被修改，集中在 README、quotation API/renderer/service、Operations quotation UI、OpenAPI/client 生成物、quotation 文档/工具/测试；当时 `git diff --stat` 为 329 insertions、68 deletions。
- 已有 untracked 内容包括 `.agents/`、quotation assets/protocol/redline、多个 Codex incident 草稿、两份旧 collection 审计材料、quotation 工具/测试，以及 `fence`、`protocol`。
- 本任务另修改 `deploy/production/compose.yaml`，显式固化 `DEFAULT_NAMESPACE_RETENTION: 720h`；它和既有报价改动无重叠。本阶段文档子任务仅新增 ADR-0008 和本审计。

## Schema、Alembic 与生产事实

- 仓库和生产数据库均只有一个 Alembic head：`s06_0038_w_review`。
- 当前 schema 中，`collection_surface`、observed surface、product variant、campaign、slot、sample ordinal、submission operation、typed grant/binding 等 v2 目标列计数为 0；对应 v2 命名表计数也为 0。
- 生产事实：498 `collection_run`、3,104 `collection_task`、1,492 `analytics.answer`。
- task→run 孤儿为 0。
- run 状态：198 completed、196 completed_with_failures、94 failed、7 cancelled、3 skipped；非终态 run 为 0。
- task 状态：1,414 completed、1,511 failed、145 done、34 legacy awaiting_intervention。34 行均无 answer 且父 run 已终态；ADR-0008 将其冻结为关闭的遗留 outcome，不改写历史标签，也不允许 reopen/reset/retry。

## Temporal 基线

- `default` namespace 热保留为 720 小时（30 天）。history archival 和 visibility archival 均 disabled；归档设计与验证待 live rollout。
- 生产 visibility 中 collection-like workflow 数为 0。仓库没有真实 collection history fixture；现有 `test_collection_mode_segment_patch.py` 只在测试中生成 synthetic history。
- v1 collection queue `geo-platform-v2-production` 当前 workflow poller=0、activity poller=0；collection worker 与 recurring scheduler 均 inactive/dead。
- 当前 collection worker 注册五个 v1 workflow class：PlatformHealth、GeoCollection、HumanIntervention、PlatformSessionLifecycle、AccountRevocation。Worker 未配置 Build ID/Deployment Version，属于 unversioned v1 注册。
- 旧数据库 start receipts 和业务事实仍在，但真实 Temporal history 已不可恢复。因此不宣称真实 replay 通过。
- v1 run/task 已关闭且 ADR-0008 禁止 start/reset/retry。若旧 history 后续重新出现，必须隔离，不能路由到 v2 worker。

## 资源与 capability 基线

- 4 个 phone account 为 active。
- 仅豆包存在 2 条 legacy governance account→browser 匹配关系；两者 runtime 均 idle，但 day/week/year quota 均未配置。
- resident browser 共 8 个：DeepSeek 3 idle、豆包 1 idle + 1 captcha、通义 1 idle、文心 2 idle、元宝 1 idle。
- region 状态为 1 ok、2 down；active browser fence 为 0。
- 当前只有 `platform × mode` 代码常量，没有版本化 `platform × surface × product_variant × mode` capability registry。
- Provider API 和 Consumer App 没有正式 binding/resource。现有 Web adapter、env/CDP 和 legacy governance 行都只是 v2 迁移输入。当前 v2 live capability 为 0。

## 主提示词现状复核

| 主题 | 结论 | 当前证据 |
| --- | --- | --- |
| config | 仍成立 | `ConfigDraft` 只有 regions/models/modes；没有 surface/target schema。 |
| 任务身份 | 仍成立 | business key 仍为 config hash + query + model + region + mode。 |
| workflow payload | 仍成立 | `CollectionTaskInput` 没有 surface；analysis dimensions/context 仍写 `channel=api`。 |
| workflow patch | 仍成立 | v1 仍按 adapter、region、mode patch 分段；不能直接加必填字段。 |
| analytics | 仍成立 | sampling/answers 聚合没有 surface，也没有 v2 四分母。 |
| provenance | 仍成立 | `CaptureChannel` 只有 API/WEB，且仍被当作业务 channel 使用。 |
| quota | 仍成立 | admission 先读 day/week/year 累计；成功结果提交后才另事务递增，没有 operation 级原子预占。 |
| 结果事务 | 部分改善但风险仍在 | answer/evidence/analysis outbox 已同事务；governor outcome 仍在 commit 后 best-effort 独立事务。 |
| Web fencing | 仍成立 | DB token 单调并有 heartbeat；token 未贯穿每个 click/submit，heartbeat 丢失只日志后返回。 |
| 外部发送 | 仍成立 | 豆包 `_submit_and_confirm` 可进行两次发送尝试；send/capture 没有 durable operation 隔离。 |
| 正式绑定 | 仍成立 | 豆包强制 legacy governance；其余平台仍允许 unmanaged env fallback，且存在 governance-off 开关。 |
| region/relay | 有局部变化，核心风险仍在 | HTTP probe 已在 DB 行锁外且有滞回；仍是 region 级 gate，没有 monotonic probe generation，陈旧结果仍可能后写。 |
| Temporal | 仍成立，风险已接受 | 只有 synthetic replay；真实 v1 history/corpus 不存在。风险不再阻塞 v2 开发，但不得宣称已验证。 |
| 重复采样 | 仍成立 | `ConfigLauncher` 仍循环启动 N 个 run，没有稳定 sample ordinal。 |
| 主批次/报告 | 仍成立 | formal review 仍按时间范围内最新 bounded repetitions 选样，没有冻结 campaign/primary slot manifest。 |

## 聚焦测试证据

命令共同前缀：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

1. `tests/unit/test_account_governor.py tests/unit/test_browser_router_governance.py tests/unit/test_resident_browser.py tests/unit/test_collection_governance_wiring.py`：97 passed。
2. `tests/unit/test_doubao_adapter.py -k 'wall_aborts_remaining_items or captcha_pause or captcha_marks_pause'`：3 passed，55 deselected。

合计 100 passed。该结果证明当前 v1 治理/fencing/captcha 聚焦基线没有回归；它不证明真实 history replay、v2 identity、typed grant、quota reservation 或 submit-once 已实现。

## 阶段边界

- ADR-0008 已冻结术语、identity grammar、capability schema、quota scope registry、v1/v2 隔离、遗留 replay 风险接受、30 天热保留和无真实发送边界。
- 阶段 1 可从 additive schema 和 canonical domain contract 开始。
- 生产 Temporal namespace retention 已在线更新为 720 小时；部署 compose 已同步但未重启/重建服务。其余改动均未提交、未推送、未部署；未启停采集服务，也未向真实 provider/Web/App 发送查询。
