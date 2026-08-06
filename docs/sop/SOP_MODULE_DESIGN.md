# SOP 模块设计契约（S05）

**来源**：《GEO信源型文章通用写作与验证流程 SOP》v1.0（`/home/xln/geo-system/posting/baijiahao/prompts/GEO信源型文章通用写作与验证流程_SOP.md`）。
**目标**：在 platform-v2 内建立该 SOP 的全流程数字化工作区——每个阶段一个「监测页」（状态/指标仪表盘）+「操作台」（数据录入与操作界面），支撑 项目定义→查询词→基线→复盘→证据→机会→写作→发布前验证→发布→索引观察→同题复测→对比归因→持续实验→归档日志 的完整闭环。

**数据契约（已冻结）**：`migrations/versions/s05_0001_sop_workflow.py`（已 applied 到开发库，16 表 + 16 RLS policy，schema `sop`）。任何字段调整必须走新迁移，不得改 s05_0001。

## 1. 后端（api/geo_platform/sop/）

风格对齐 S02 面（reports/intelligence）：`router.py`（pydantic + APIRouter）+ `service.py`（psycopg 裸 SQL，`SopService(dsn=…)`），不用 ORM。参照样板：

- router 范式：`api/geo_platform/reports/router.py`（StrictModel extra="forbid"、`principal.require(...)`、领域异常→HTTPException code、cursor 分页）
- service 范式：`api/geo_platform/reports/service.py:30`
- 租户连接：`api/geo_platform/tenancy/psycopg.py` 的 `tenant_connection(dsn, tenant_pub_id)`；所有 SQL 显式带 `WHERE tenant_pub_id=%s` 双保险
- DSN 归一化：照抄 `reports/router.py` 的 `_dsn()`（`runtime_postgres_dsn` 回退 `postgres_dsn`，`postgresql+psycopg://` → psycopg 裸 DSN）
- pub_id：`api/geo_platform/tenancy/ids.py` 的 `new_pub_id(prefix)`
- 挂载：在 `api/geo_platform/s02_routers.py` import 并 include（additive 一行）
- 权限：`identity/policy.py` 的 `ROLE_PERMISSIONS` 增加 `sop:read` / `sop:write`（additive）：operator/analyst 给 read+write，reviewer 给 read，admin 已有 `*`，customer 不给

### pub_id 前缀

| 资源              | 前缀  | 资源              | 前缀  |
| ----------------- | ----- | ----------------- | ----- |
| project           | `spr` | pre_publish_check | `spc` |
| query_set         | `sqs` | publication       | `spb` |
| query_item        | `sqi` | index_observation | `sio` |
| baseline_answer   | `sbl` | retest_answer     | `srt` |
| retrieval_insight | `sis` | comparison        | `scm` |
| evidence_item     | `sev` | experiment        | `sex` |
| opportunity       | `sop` | work_log          | `swl` |
| article           | `sar` | article_version   | `sav` |

### 端点清单（前缀 `/api/v2/sop`，tag `sop`）

所有端点 `principal: Principal = Depends(get_principal)`；读 `principal.require("sop:read")`，写 `principal.require("sop:write")`。列表端点走不透明 cursor 分页（`cursor`+`limit`，响应 `{data, page:{next_cursor, has_more}}`，`ORDER BY pub_id`）。POST 接受可选 `Idempotency-Key` 头（16–128 字符；传入时按 tenant+operation+key 去重，重放返回首次结果，冲突 409 `idempotency_conflict`——参照 projects/reports 现有实现复用其模式；若实现成本过高可对部分端点只校验格式不去重，但必须在响应头回显并接受该头，缺口记入 S05 工作日志）。

错误码（HTTPException detail.code）：`not_found`(404)、`invalid_state`(409)、`validation_failed`(422 由全局信封处理)、`idempotency_conflict`(409)。

**项目（阶段0）**

- `POST /projects` 创建。body: name, brand_standard_name, brand_profile?, target_platforms?, success_definition?。created_by_pub_id 取 principal.user_pub_id。
- `GET /projects` 列表。query: status?。
- `GET /projects/{pub_id}` 详情。
- `PATCH /projects/{pub_id}` 更新 name/brand_standard_name/brand_profile/target_platforms/success_definition/status（全部可选）。更新 updated_at。
- `GET /projects/{pub_id}/dashboard` 监测页聚合（见 §3）。

**查询词（阶段1）**

- `POST /projects/{pub_id}/query-sets` 创建版本（version_no=该项目 max+1，status=draft）。body: note?。
- `GET /projects/{pub_id}/query-sets` 列表（含 items 数量）。
- `POST /query-sets/{pub_id}/items` 批量加词。body: `{items:[{query_text, layer, contains_brand?, intent?, persona?, decision_stage?, expected_facts?, priority?}]}`；ordinal 由服务层按现有 max+1 递增。**仅 draft 状态可写**，否则 409 `invalid_state`。
- `POST /query-sets/{pub_id}/freeze` draft→frozen（frozen_at=now）；同项目其它 frozen 版本→superseded。幂等：已 frozen 再调返回 200 原样。
- `GET /query-sets/{pub_id}/items` 列表。

**基线（阶段2）**

- `POST /projects/{pub_id}/baseline-answers` body 全字段（query_item_pub_id, sample_index?, platform, region?, account_label?, mode?, asked_at, capture_status, answer_text?, reasoning_summary?, search_terms?, search_results?, citations?, brand_mentioned?, mention_context?, key_facts?, evidence_ref?, note?）。同 (query_item, sample_index) 冲突 → 409 `invalid_state`。
- `GET /projects/{pub_id}/baseline-answers` query: query_item_pub_id?, platform?, capture_status?。

**复盘（阶段3）**

- `POST /projects/{pub_id}/insights` body: insight_type, payload?, note?。
- `GET /projects/{pub_id}/insights`

**证据账本（阶段4）**

- `POST /projects/{pub_id}/evidence` body: claim_text, source_name?, source_url?, source_level, verified_at?, can_prove?, cannot_prove?, allowed_public?, evidence_ref?。
- `GET /projects/{pub_id}/evidence` query: source_level?。
- `PATCH /evidence/{pub_id}` 全字段可选更新。

**内容机会（阶段5-6）**

- `POST /projects/{pub_id}/opportunities` body: target_query, current_gap?, current_sources?, brand_material?, needed_evidence?, recommended_platform?, expected_change?。
- `GET /projects/{pub_id}/opportunities` query: status?。
- `PATCH /opportunities/{pub_id}` 全字段+status 可选更新。

**文章（阶段7）**

- `POST /projects/{pub_id}/articles` body: title, opportunity_pub_id?（存在性校验）。
- `GET /projects/{pub_id}/articles` 列表（含最新 version_no、版本数、maturity_level 计算见 §3）。
- `GET /articles/{pub_id}` 详情+全部版本摘要（不含 body 全文，含 body_sha256/ readiness/publication_ready/checks 数）。
- `PATCH /articles/{pub_id}` 更新 title/status。
- `POST /articles/{pub_id}/versions` body: title, body, change_note?。version_no=max+1；**body_sha256 服务端计算**（sha256 hex of body UTF-8）。创建版本时若 article.status=draft → in_review。
- `GET /article-versions/{pub_id}` 详情（含 body 全文、checklist、checks 列表）。
- `PATCH /article-versions/{pub_id}` 更新 readiness_checklist（合并 JSON 键）/publication_ready/title/change_note。

**发布前验证（阶段8）**

- `POST /article-versions/{pub_id}/checks` body: check_type, result, findings?, checked_by?, checked_at。
- `GET /article-versions/{pub_id}/checks`

**发布（阶段9）**

- `POST /article-versions/{pub_id}/publications` body: platform, account_label?, submitted_at?。**硬门**：version.publication_ready 必须为 true，否则 409 `invalid_state`（SOP 发布前停止条件 fail-closed）。title/body_sha256 从版本快照带入；project_pub_id 从 article 带入。创建后 article.status → published。
- `GET /projects/{pub_id}/publications` query: status?, platform?。
- `GET /publications/{pub_id}` 详情（含 observations 时间线摘要、retest/comparison 计数）。
- `PATCH /publications/{pub_id}` 更新 status/public_url/content_id/published_at/public_checked_at/public_http_status/evidence/note。状态机校验：submitted→reviewing→published→public 为正向路径；rejected/withdrawn 可从任意非 public 态进入；login_only 与 published 互转允许；public 为终态（只能更新 evidence/note/public_checked_at/public_http_status）。非法跳转 409 `invalid_state`。

**索引观察（阶段10）**

- `POST /publications/{pub_id}/observations` body: checkpoint, checkpoint_label?, observed_at, page_accessible?, search_engine_indexed?, platform_search_visible?, ai_retrieved?, ai_cited?, note?。同 (checkpoint, checkpoint_label) 冲突 409。
- `GET /publications/{pub_id}/observations`

**同题复测（阶段11）**

- `POST /publications/{pub_id}/retest-answers` body 同 baseline 全字段 + article_appeared?, article_position?, article_cited?, citation_position?, brand_attribution_correct?, new_facts?, errors_introduced?。query_item_pub_id 必须属于该项目任一 query_set（服务层校验）。
- `GET /publications/{pub_id}/retest-answers` query: query_item_pub_id?。

**对比归因（阶段12-13）**

- `POST /publications/{pub_id}/comparisons` body: query_item_pub_id, baseline_answer_pub_id?, retest_answer_pub_id?, metrics?, new_info_location?, from_article_confidence?, attribution_correct?, conclusion?, next_actions?。按 (publication, query_item) **upsert**（存在则更新并 bump updated_at）。
- `GET /publications/{pub_id}/comparisons`
- `GET /projects/{pub_id}/comparison-summary` 聚合指标（见 §3）。

**持续实验（阶段14）**

- `POST /projects/{pub_id}/experiments` body: hypothesis, change_description?, controlled_conditions?, query_set_pub_id?, observation_window?。
- `GET /projects/{pub_id}/experiments` query: status?。
- `PATCH /experiments/{pub_id}` 全字段+status 可选更新。

**工作日志（阶段15，append-only）**

- `POST /projects/{pub_id}/work-logs` body: entry_type, failure_class?, content。actor_pub_id 取 principal.user_pub_id。**不提供任何更新/删除端点**。
- `GET /projects/{pub_id}/work-logs` query: entry_type?。

## 2. 响应形状

每资源一个 pydantic 响应模型，字段与表列一致（去掉内部 `id`，`pub_id` 等原样；时间戳 RFC3339 UTC）。命名如 `SopProjectResponse`、`SopQuerySetResponse`…列表 `{data: [...], page: PageMeta}`。

## 3. 监测聚合（dashboard 与 summary）

`GET /projects/{pub_id}/dashboard` 返回：

```json
{
  "project": {SopProjectResponse},
  "steps": [
    {"key": "project-definition", "stage": "阶段0", "name": "项目定义",
     "status": "done|in_progress|empty", "metrics": {"...": 0}}
  ],
  "articles": [
    {"article_pub_id", "title", "status", "version_count",
     "publication_ready": false, "has_publication": false,
     "maturity_level": "L0|L1|L2|L3|L4"}
  ]
}
```

14 个 step（key 固定，前端路由/导航据此渲染）：

| #   | key                | SOP 阶段           | done 判定（全真实数据，零合成）                              |
| --- | ------------------ | ------------------ | ------------------------------------------------------------ |
| 1   | project-definition | 0 项目定义         | brand_profile/target_platforms/success_definition 均非空     |
| 2   | query-set          | 1 查询词全集       | 存在 frozen 版本且词数≥1                                     |
| 3   | baseline           | 2 基线采集         | frozen 版本每条 P0 词至少有 1 条 capture_status=success 基线 |
| 4   | retrieval-review   | 3 检索复盘         | insights ≥1                                                  |
| 5   | evidence-ledger    | 4 证据账本         | evidence ≥1                                                  |
| 6   | opportunities      | 5-6 内容机会与信源 | status=selected 的机会 ≥1                                    |
| 7   | writing            | 7 文章写作         | 文章 ≥1 且存在版本                                           |
| 8   | pre-publish        | 8 发布前验证       | 存在 publication_ready=true 的版本                           |
| 9   | publishing         | 9 发布管理         | 存在 status=public 的发布                                    |
| 10  | index-watch        | 10 索引观察        | 任意 publication 有 ≥2 个 checkpoint 观察                    |
| 11  | retest             | 11 同题复测        | 存在 capture_status=success 的复测                           |
| 12  | comparison         | 12-13 对比归因     | comparisons ≥1                                               |
| 13  | experiments        | 14 持续实验        | experiments ≥1                                               |
| 14  | archive-log        | 15 归档与工作日志  | work_logs ≥1                                                 |

status 语义：`done`=满足判定；`in_progress`=有数据但未达 done；`empty`=无数据。metrics 各 step 给出关键计数（如 baseline 的 `{answers, success, failed_samples, coverage_pct}`）。

**maturity_level**（SOP §20 L0–L4，按文章聚合其全部版本→发布→复测→对比）：

- L4：存在 comparison.from_article_confidence ∈ (medium, high) 且 attribution_correct=true
- L3：存在 retest.article_cited=true（未达 L4）
- L2：存在 retest.article_appeared=true（未达 L3）
- L1：存在 status=public 的 publication（未达 L2）
- L0：其余（仅有稿件）

`GET /projects/{pub_id}/comparison-summary` 返回：

```json
{
  "project_pub_id": "...",
  "retrieval": {"retests_success": 0, "article_recall_rate": null, "avg_article_position": null},
  "citation": {"citation_rate": null, "avg_citation_position": null},
  "brand": {"baseline_mention_rate": null, "retest_mention_rate": null, "attribution_correct_rate": null},
  "answer": {"avg_new_facts": null, "comparisons": 0, "from_article_medium_or_high": 0},
  "per_query": [{"query_item_pub_id", "query_text", "baseline_mentioned", "retest_mentioned",
                 "article_appeared", "article_cited", "from_article_confidence"}]
}
```

rate 分母只计 capture_status=success 的样本；无样本时该指标为 null（真实零与无数据区分，对齐 INV-32 语义）。per_query 按 frozen 查询词逐一列出，取每词最新 comparison/最新 success 复测/最新 success 基线。

## 4. 前端（apps/operations-web/app/features/sop/）

**路由**（routes.ts additive）：

- `route('sop', 'features/sop/route.tsx')` — 项目列表 + 新建项目操作台
- `route('sop/projects/:projectPubId', 'features/sop/project-route.tsx')` — 项目工作区

**导航**：shell.tsx `operationsNav` additive 一项 `{ id: 'sop', label: '信源 SOP', href: '/platform/operations/sop' }`。

**工作区布局**：ProductShell + 左侧 14 步 stepper（固定顺序、显示 done/in_progress/empty 徽标，数据来自 dashboard 端点）+ 右侧当前步骤内容。每步两个标签页：**监测**（该步 MetricGrid/状态表/时间线，来自 dashboard + 资源列表端点）与**操作台**（该步的创建表单 + 列表行内操作，如 freeze/状态流转/checklist 勾选/upsert comparison）。步骤间数据联动通过共享 dashboard refetch。

**步骤组件**（每步一个文件，放 `features/sop/steps/`）：ProjectDefinition / QuerySet / Baseline / RetrievalReview / EvidenceLedger / Opportunities / Writing / PrePublish / Publishing / IndexWatch / Retest / Comparison / Experiments / ArchiveLog。

**API 层**：在 `packages/api-client/src/index.ts` 新增投影包装器（范式照抄 `getMediaPricesDataset`，`Pick<>` 投影 + 判别联合 `{kind:'ready'|'forbidden'|'unavailable'|'missing', data?}`），禁止 re-export 生成 schema；`pnpm generate:api` 先行（后端落地后）；**同步把 `scripts/check_frontend_contracts.py` 里包装器计数 63 改为新值**。页面只 import 包装器，禁止 fetch/`/api/v2/` 字面量。写操作带 `Idempotency-Key: sop-${crypto.randomUUID()}`。

**状态/组件**：`useState<判别联合>`+`useEffect`；StatePanel/MetricGrid/TableRegion/FormField/Dialog/Toast；样式 `features/sop/sop.css` 私有类名；表单用手写受控组件（不引新依赖）。

**测试**：`features/sop/*.test.tsx`（vitest + jsdom + `vi.mock('@geo/api-client')`）覆盖：项目列表渲染、工作区 stepper、至少 3 个代表性步骤（QuerySet 冻结流转、Writing 版本+checklist、Comparison 归因表单）的操作台交互。

## 5. 验收门

后端：`.venv/bin/ruff check api` + `ruff format --check` + `.venv/bin/mypy api` + `.venv/bin/pytest tests/integration/test_s05_sop_workflow.py -q` 全绿；`pnpm generate:api` 产物三件套（openapi.json / schema.generated.ts / generated-manifest.json）更新且 `pnpm check:api` 通过。
前端：`pnpm --filter @geo/operations-web typecheck` + `pnpm --filter @geo/operations-web test` 全绿；`pnpm check:api`（含 frontend guard）通过。
集成：主会话复核 diff 只含 additive 变更 + 运行上述门禁。

## 6. 明确不做（本期边界）

- 不做自动发布执行（不接百家号自动化）；发布状态由运营在操作台手工登记流转。
- 不做自动同题复测触发（不接采集调度；ADR-0002 采集真空维持）；复测结果由操作台登记。
- 不做 LLM 自动写稿/自动审查；文章正文与审查结论由人工经操作台录入。
- 不建 e2e/视觉 baseline（后续轮次补）。
- customer 角色无任何 sop 权限（内部运营功能）。
