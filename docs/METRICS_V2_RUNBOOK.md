# GEO 指标 V2 运行手册

本手册对应 `QUERY_COHORT_METRICS_V2.md` 的迁移、定义加载、独立 worker、历史回放、shadow 验证、正式发布和回滚。所有命令默认从 `platform-v2` 根目录执行。

当前交付边界：仓库工具只会加载 `experimental` 定义并构建/发布 `shadow` 快照。没有明确的生产部署与指标口径批准时，不得迁移生产库、启用生产 worker、发布定义或切换 `official` 指针。生产激活状态应记录为“待授权生产激活”。

## 1. 安全不变量

- 不修改或重新采集原始 query/answer，也不更新 V1 `metric_trace`、`metric_daily` 或旧 analysis。
- 先冻结统一的 UTC `as_of`，再按稳定 keyset cursor 回放；不得跳过难样本来改善覆盖率。
- decision、event、evaluation、snapshot 和 contribution 均追加写；定义只允许 `draft -> experimental -> published`，发布后不可变。
- 模型失败、预算耗尽、证据不可恢复、输出非法或 judge 分歧都保持为可追踪的 unknown/review，不得用关键词、正则或 V1 值兜底。
- 所有正式消费者只读同一个已冻结 V2 snapshot set。GET、前端、报告和导出不得现场重算。
- `official` 只能通过带预期 generation 和 set hash 的 CAS 发布；回滚目标只能是上一份已验证的 V2 set。
- 物理删除、历史表清理和生产数据销毁不在本次授权范围内。

## 2. 变更前检查

确认工作树、唯一 Alembic head、依赖服务和配置。不要清理与本任务无关的已有改动。

```bash
git status --short
.venv/bin/alembic heads
.venv/bin/alembic current
docker compose -f compose.yaml -f deploy/s02/compose.pgvector.yaml ps
```

预期 Alembic head 为 `s18_0001_geo_metrics_v2`。若当前数据库不是预期基线，先停止；不得使用 `stamp` 绕过迁移。

生产变更还必须先完成一次受限备份和独立恢复验证，并记录备份 hash。仓库已有备份单元可供获授权操作员使用：

```bash
sudo systemctl start geo-platform-v2-backup.service
sudo systemctl status geo-platform-v2-backup.service --no-pager
```

没有生产授权时，只在隔离数据库执行以下步骤。

## 3. 迁移与权限验证

在空的隔离 PostgreSQL 以及一份隔离的现有基线副本上分别执行：

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/pytest -q \
  tests/integration/test_metrics_v2_migration.py \
  tests/integration/test_metrics_v2_rls.py \
  tests/integration/test_metrics_v2_repository.py \
  tests/integration/test_metrics_v2_snapshot_atomicity.py
```

迁移建立 V2 任务、policy、证据、job/attempt/decision、查询事实、语义清单/事件、指标定义、evaluation、snapshot set、三层 contribution、publication 和 recompute 表，并启用 RLS、最小 ACL、追加写与生命周期约束。

只有一次性隔离数据库允许用下面的命令验证 downgrade/re-upgrade。生产回滚不得删除 V2 schema 或历史证据。

```bash
.venv/bin/alembic downgrade s17_0005_credential_boundary
.venv/bin/alembic upgrade head
```

## 4. 加载 experimental 定义

先执行只读校验：

```bash
.venv/bin/python tools/seed_metrics_v2_definitions.py
```

预期输出为 50 个 artifact：14 个 DecisionTask、2 个 judge policy、34 个 MetricDefinition，且 `mode=dry_run`、`target_status=experimental`、`official_activation=false`。

在已迁移的目标库显式加载：

```bash
.venv/bin/python tools/seed_metrics_v2_definitions.py --apply
```

该命令使用事务 advisory lock；同 hash 重跑会复用，已有同名版本但 hash 不同会失败。它永远不会发布定义或移动 official 指针。

加载后用 owner 只读检查：

```sql
SELECT status,count(*)
FROM analytics.semantic_decision_task_definition_v2
GROUP BY status ORDER BY status;

SELECT status,count(*),count(calibration_artifact_hash) AS calibrated
FROM analytics.semantic_judge_policy_v2
GROUP BY status ORDER BY status;

SELECT status,count(*)
FROM analytics.metric_definition
GROUP BY status ORDER BY status;
```

## 5. 独立 worker

仓库提供以下独立 Temporal worker 和生产单元：

| 职责                                     | 队列                       | systemd 单元                              |
| ---------------------------------------- | -------------------------- | ----------------------------------------- |
| 语义判定和模型 I/O                       | `geo-platform-v2-decision` | `geo-platform-v2-decision-worker.service` |
| 纯确定性 evaluation/snapshot/publication | `geo-platform-v2-metrics`  | `geo-platform-v2-metrics-worker.service`  |
| 冻结快照报告和 LibreOffice               | `geo-platform-v2-report`   | `geo-platform-v2-report-worker.service`   |

配置样例位于 `deploy/production/*-worker.env.example`。判定预算默认是零；在预算与 policy 未获批准前保持零会让需要模型的任务诚实排队/unknown。

隔离环境可以分别启动：

```bash
.venv/bin/python -m workflows.workers.decision
.venv/bin/python -m workflows.workers.metrics
.venv/bin/python -m workflows.workers.report
```

获授权的 systemd 安装应由发布系统复制经过审查的 env 与 unit，然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now \
  geo-platform-v2-decision-worker.service \
  geo-platform-v2-metrics-worker.service \
  geo-platform-v2-report-worker.service
sudo systemctl status \
  geo-platform-v2-decision-worker.service \
  geo-platform-v2-metrics-worker.service \
  geo-platform-v2-report-worker.service --no-pager
```

检查日志时只能记录 public ID、workflow/job/decision/set ID、错误码和 hash，不得记录完整 query/answer/source、prompt、密钥或模型原始响应。

## 6. 历史回放

### 6.1 固定范围与预算

对每个 tenant/project 固定一个 UTC `as_of`，先跑 semantic 计划：

```bash
.venv/bin/python tools/run_metrics_v2_backfill.py \
  --tenant-pub-id tnt_EXAMPLE \
  --project-pub-id prj_EXAMPLE \
  --stage semantic \
  --as-of 2026-08-27T00:00:00Z \
  --batch-size 100
```

保存 dry-run JSON。必须审查 `candidate_count`、`prepared_count`、`preparation_unknown_count`、原因分布、预计原子 decision 数、每日模型预算、`batch_hash` 和 `next_cursor`。待量与预算不匹配时调整容量或保持排队，不得筛选样本。

### 6.2 应用一个 semantic page

从同一次 fresh dry-run 输出复制精确的 `selection_hash` 和 `confirm_token`：

```bash
.venv/bin/python tools/run_metrics_v2_backfill.py \
  --tenant-pub-id tnt_EXAMPLE \
  --project-pub-id prj_EXAMPLE \
  --stage semantic \
  --as-of 2026-08-27T00:00:00Z \
  --batch-size 100 \
  --selection-hash SHA256_FROM_PLAN \
  --confirm-token TOKEN_FROM_PLAN \
  --apply --wait
```

每页成功后用返回的 `next_cursor` 做下一次 fresh dry-run。selection 变化会拒绝应用。workflow history 只包含引用与 hash，不包含完整正文。

### 6.3 回放确定性指标

semantic 回放完成或所有缺失均已明确归类后，对同一 `as_of` 执行 metrics dry-run，再用其精确确认值应用：

```bash
.venv/bin/python tools/run_metrics_v2_backfill.py \
  --tenant-pub-id tnt_EXAMPLE \
  --project-pub-id prj_EXAMPLE \
  --stage metrics \
  --as-of 2026-08-27T00:00:00Z \
  --batch-size 500
```

```bash
.venv/bin/python tools/run_metrics_v2_backfill.py \
  --tenant-pub-id tnt_EXAMPLE \
  --project-pub-id prj_EXAMPLE \
  --stage metrics \
  --as-of 2026-08-27T00:00:00Z \
  --batch-size 500 \
  --selection-hash SHA256_FROM_PLAN \
  --confirm-token TOKEN_FROM_PLAN \
  --apply --wait
```

重复投递必须幂等；显式重判生成 supersedes 链，不能 UPDATE 旧 decision。构建中途到达的新 answer 不得进入既定 `as_of`。

## 7. Shadow 快照、导出与对账

经鉴权 API 请求明确 scope；请求只接受 `publication_channel=shadow` 并返回 202/job，不同步构建：

```http
POST /api/v2/metrics/projects/prj_EXAMPLE/snapshot-requests
Content-Type: application/json

{
  "window": {"start": "2026-08-01", "end": "2026-08-27"},
  "filters": {"model": [], "region": [], "mode": []},
  "focal_entity_ids": ["entity_EXAMPLE"],
  "aggregation_method": "query_macro",
  "publication_channel": "shadow",
  "idempotency_key": "review-20260827-example"
}
```

轮询 `/api/v2/metrics/snapshot-jobs/{job_pub_id}`，再读取 `/api/v2/metrics/snapshot-sets/{set_pub_id}`。所有响应应为 `Cache-Control: private, no-store`。

对每个 snapshot 验证：

- 每个候选 answer 恰有一个 `included_hit/included_miss/excluded/not_applicable/analysis_unknown` 状态；
- answer、query、design-cell 三层贡献和权重方程闭合；
- unknown 只进入缺失界限，adjudication sensitivity 只反映判定方法与校准 artifact；
- pagination 不改变总计或 contribution hash；
- `snapshot_hash`、三类 contribution set hash、dependency bundle hash 和 set hash 可重复；
- XLSX 的 `README/METRICS/QUERIES/ANSWERS/DECISIONS/EVENTS/EXCLUSIONS/DESIGN_CELLS/HASHES` 能重算相同结果，公式前缀文本已转义；
- 页面、Analytics、BrandRank、SOP、目标、前后对比、报告与导出显示同一个 set ID/hash。

创建私有、有时效、可审计的导出：

```http
POST /api/v2/metrics/snapshot-sets/{set_pub_id}/exports
Content-Type: application/json

{"format":"xlsx"}
```

## 8. 校准与定义发布门

在发布任何定义前，为每个 DecisionTask/确定性快路运行冻结金标集，保存样本版本、任务/策略 hash、选择性准确率、覆盖率、弃权率、分歧率、证据失败率、成本和漂移结果。事实任务还要冻结证据检索协议和 verification-as-of。

judge policy 的已发布版本必须在 artifact 本身包含非空 `calibration_artifact_hash`。如果 experimental 版本没有该 hash，不得原地补字段；应创建新的版本化 policy artifact、重新 seed，并保留旧版本。所有发布必须只做生命周期变化、同事务写 publication outbox、记录操作者与审批证据，并让数据库约束再次验证 hash。禁止用临时 SQL 绕过该门。

定义发布前必须同时满足：

- 任务 DAG 无环，父任务版本存在且可发布；
- task/rubric/prompt/schema hash 与校准记录完全一致；
- policy 与 task 兼容，calibration artifact 存在且 hash 一致；
- metric 定义依赖的 task 均已发布；
- 核心 metric 覆盖率、unknown 界限和全部对账门通过；
- 没有关键词/正则、V1 或现场聚合作为正式 fallback。

定义、policy 或 rubric 的任何语义变化都必须增加版本；已经发布的行不可修改或删除。

## 9. Official 原子切换

这一节只供获授权的生产发布执行。先记录当前 official publication 的 generation 和上一份 V2 set ID/hash，再对候选 set 执行完整 smoke。候选 set 必须 `state=ready`，成员 snapshot 全部 ready，metric/task/policy 均 published，所有支撑 decision accepted，policy 带校准 artifact。

CAS 请求：

```http
POST /api/v2/metrics/operations/snapshot-sets/{set_pub_id}/publish
Content-Type: application/json

{
  "publication_channel": "official",
  "expected_generation": 7,
  "expected_snapshot_set_hash": "64_HEX_CHARACTERS"
}
```

generation 或 hash 不一致返回 conflict，必须重新读取状态并复核，不能盲目重试。成功后执行只读 smoke：

- current official API、每个消费者页面、XLSX、DOCX/PDF 均为同一个 set ID/hash；
- contribution 明细能追到 query/answer、decision、rubric、证据与 event；
- 跨租户 public ID 猜测返回 404；
- API 读取时即使禁用 snapshot engine 和模型 client 仍成功；
- 停止 report worker 不影响 snapshot，停止 metrics worker 不影响采集/判定，停止 analysis/decision 不影响已有 snapshot/报告读取；
- outbox/evaluation backlog、unknown/coverage、hash mismatch、judge abstention/disagreement/evidence failure、report validation 告警正常。

## 10. 回滚与故障处理

业务回滚使用 publication CAS 把同一 scope 指向上一份已验证 V2 set；不要恢复 V1 公式，不要删除失败 set，也不要 downgrade 生产 schema。回滚后再次核对所有消费者的 set ID/hash，并保留失败候选及日志供审计。

常见失败按以下方式处理：

- `metrics_v2_seed_hash_conflict`：同名版本语义已漂移；增加版本或恢复正确 artifact，不能覆盖。
- `blocked_on_dependency`：补齐/发布父 DecisionTask，保留叶任务 pending。
- `judge_disagreement`、`review_required`：进入人工复核；相关能力为 unknown。
- `model_not_configured`、预算耗尽、熔断：恢复配置/预算后幂等续跑；禁止弱规则 fallback。
- `evidence_retrieval_failed`：恢复冻结证据或明确 unknown；不能记为 unsupported claim。
- `metric_snapshot_set_not_ready`：修复缺失 evaluation/decision 后生成新 set；报告不能走 legacy。
- `metric_publication_conflict`：重新读取 generation/hash，确认没有并发发布后再提交新 CAS。
- 任一 hash mismatch 或对账失败：停止发布，一次即告警，保留输入与 hash 以精确重放。

## 11. 验收命令

合并前至少运行主设计文档第 30.11 节的完整命令：

```bash
.venv/bin/ruff check api domain workflows migrations tests
.venv/bin/mypy api workflows domain
.venv/bin/pytest -q \
  tests/unit/test_query_context_v2.py \
  tests/unit/test_semantic_events_v2.py \
  tests/unit/test_decision_task_definition_v2.py \
  tests/unit/test_semantic_judge_policy_v2.py \
  tests/unit/test_semantic_decision_validation_v2.py \
  tests/unit/test_semantic_decision_adjudication_v2.py \
  tests/unit/test_metric_definition_v2.py \
  tests/unit/test_metric_evaluator_v2.py \
  tests/unit/test_metric_weighting_v2.py \
  tests/unit/test_metric_snapshot_hash_v2.py \
  tests/unit/test_metric_missing_bounds_v2.py \
  tests/unit/test_metric_adjudication_sensitivity_v2.py
.venv/bin/pytest -q \
  tests/integration/test_metrics_v2_migration.py \
  tests/integration/test_metrics_v2_rls.py \
  tests/integration/test_metrics_v2_repository.py \
  tests/integration/test_metrics_v2_outbox_temporal.py \
  tests/integration/test_semantic_decision_v2_outbox_temporal.py \
  tests/integration/test_semantic_decision_v2_backfill.py \
  tests/integration/test_metrics_v2_snapshot_atomicity.py \
  tests/integration/test_metrics_v2_api.py \
  tests/integration/test_metrics_v2_export.py \
  tests/integration/test_metrics_v2_report_binding.py \
  tests/integration/test_metrics_v2_backfill.py
pnpm --filter @geo/customer-web test
pnpm check:api
pnpm typecheck
pnpm test:python
pnpm test
pnpm exec playwright test tests/e2e/customer-metric-trace.spec.ts
```

生产授权前最后一项始终记录为：`待授权生产激活`。这不是代码失败，也不能被本地 shadow 结果替代。

## 12. 2026-08-27 隔离验收记录

本次实施分别在空隔离库和从 `s17_0002_knowledge_trace_details` 起步的历史隔离库完成升级，
两条路径均到达唯一 head `s18_0001_geo_metrics_v2`；未读取或修改生产客户数据。

- 50 个 experimental artifact 加载成功：14 个 DecisionTask、2 个 judge policy、34 个 MetricDefinition；`official_activation=false`。
- 合成历史回放包含 2 个候选答案：1 个可执行、1 个因旧 analysis 不可恢复而显式 unknown。零模型预算下，14 个原子判定全部 abstained，可执行答案形成 partial manifest。
- metrics 回放为 2 个实体 × 34 个指标持久化 68 个 `analysis_unknown` evaluation；`outcome_value` 为 JSON null，不是未命中或 SQL NULL。
- shadow 构建持久化 68 个 experimental snapshot，以及 answer/query/design-cell 各 68 条贡献；set 为 `mss_b1c375f590e4031d9e6585b2b8`，set hash 为 `b1c375f590e4031d9e6585b2b8a1092072d7ff28197eaddefc5e4067b65b39fb`，shadow generation 为 1。它只证明失败闭合与确定性链路，不具备 official 资格。
- 快速 Python 车道 `2820 passed, 57 deselected`；指标/语义判定真实 PostgreSQL 车道 20 个通过，owner-loss 恢复真实 PostgreSQL 车道 3 个通过，报告绑定车道 22 个通过。
- 真实 Temporal 的 Analysis/S02/Report 分离、人工信号、worker 重启和错误租约拒绝 8 个通过。
- customer-web 51 个测试、operations-web 173 个测试、API client 142 个测试、浏览器运行时安全测试 13 个通过；13 个前端工作区 typecheck/build、Python mypy 374 个源文件、Ruff、CI workflow guard 和 24 条可观测性告警检查通过。
- OpenAPI SHA-256 为 `dfda68628aed14981d9a19d6348d35994d5f28d0314293247835daca595f27f0`；生成 TypeScript schema SHA-256 为 `62e1183480d90771e629513c2ffd41de3d1a2320b56b198b10ab533cb251b958`。
- 全局 `pnpm check`、生成 manifest、frontend contract、五 SPA production bundle、生产 route、E2E artifact DLP 和不可变 Nginx alias 守卫均通过。

生产状态：`待授权生产激活`。仍需指定 tenant/project、批准模型预算、完成冻结金标校准并生成带 `calibration_artifact_hash` 的新版本 policy，随后才能执行真实历史回放、定义发布、official CAS 与生产只读 smoke。
