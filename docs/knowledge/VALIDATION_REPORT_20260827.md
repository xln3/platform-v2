# 证据驱动知识演进中间件生产交付与验证报告

- 验证日期：2026-08-27（Asia/Shanghai）
- 平台最终提交：`014bf22`
- 生产迁移版本：`s17_0002_knowledge_trace_details`
- 本机 active release：`knowledge-2026-08-27.3`
- SiliconIndex 公网 release：`2026-08-27.2`
- 总体状态：已编码、已测试、已提交、已推送、已部署并完成 live 验证

## 1. 背景、两次纠正与根因

原问题表面上是品牌别名、公司/产品混淆和竞品榜重复计权，实际问题是业务运行持续产生不确定知识，却没有统一的观察、推理、证据、审核、版本和分发闭环。

第一次纠正否决了“项目请求不调用 LLM”。请求时 LLM 可以完成上下文消歧、关系判断和未知实体发现，也可以在调用方明确政策下影响本次结果。禁止的是把临时模型推理未经证据、审核和 release 发布就伪装成永久全局事实。

第二次纠正否决了“只做品牌/SiliconIndex 字典桥”。稳定模式是 `observation → candidate → proposal → evidence → adjudication → change set → release → connector`。品牌实体归并只是首个生产领域包，SiliconIndex 只是首个 source/sink adapter。

根因是旧设计把三组正交问题绑定在一起：运行时是否调用模型、推理是否影响当前请求、推理是否升级为全局知识；同时又把通用治理状态机写成品牌专用同步逻辑。最终实现把运行时决策、认识状态和知识发布权限拆开，并把领域逻辑放入 domain pack。

## 2. 现有代码和数据审计

### 保留

- 保留并通用化静态快照的 manifest/hash/引用/重复 ID 校验、不可变目录、原子 `CURRENT/PREVIOUS` 和 last-known-good 回退。
- 保留 SiliconIndex 的确定性生成流水线、Schema、search index、graph、quality report、bundle 和 release 历史。
- 保留平台已有 FastAPI、PostgreSQL、租户 ID、RBAC、audit 和 OpenAPI 生成基础设施。
- 保留用户工作树中所有与本任务无关的修改；所有提交均只选择性暂存本任务文件。

### 重构或替换

- 将品牌专用闭环重构为 `domain/knowledge_evolution` 通用核心、注册表和策略接口。
- 将 SiliconIndex 降为 adapter；运行时优先读取本机已验证 knowledge release，远端站点不在请求关键路径。
- 将人工可漂移的网安投影改为由指定 SiliconIndex snapshot 确定性生成。
- 将“上游全局 hash 必须不变”的 lineage-only 判断替换为“完整 reviewed 对象集合及属性必须逐字段不变”。全局 hash 可以因其他行业数据变化而前进，旧/新上游 release/hash 会写入 connector audit。
- 删除了架构上的在线 LLM 禁令，改为四种显式 reasoning policy。

### SiliconIndex 拆分仓库审计

Render 实际跟踪独立仓库 `suzakuzhang/siliconindex-consumer`，而不是 monorepo 子目录。独立仓库的 TypeScript/source 代码比 monorepo 新，因此合并时保留 split 仓库代码和已有同 ID 记录，只补入 monorepo 新 ID 与网安域。两条重复 mention 被分配新稳定 ID；213 条旧格式关系显式迁移到 schema 1.1；9 条 `context_required` mention 补齐行业上下文。没有用覆盖目录的方式丢弃 split-only 证据。

## 3. 通用模式、ADR 和物理部署

ADR-0017 决定第一阶段在 `platform-v2` 内以模块化中间件落地，理由是可以复用现有鉴权、租户、数据库、审计和生产部署，同时用独立边界保持以后可提取性：

- 独立 PostgreSQL `knowledge` schema；
- 独立 `/api/v2/knowledge/v1` versioned API；
- 独立 `domain/knowledge_evolution` 核心包；
- 独立 `/var/lib/geo-platform-v2/knowledge` 不可变 artifact；
- 独立治理、备份和 SiliconIndex 同步 systemd service/timer。

生产 API 仍随现有 FastAPI 进程运行，但公共契约、存储命名和 domain registry 不依赖 GEO 项目对象。第二领域 `source/type-fixture` 通过同一核心 contract，证明核心没有品牌、网安或 SiliconIndex 前提。

## 4. 四个平面、数据模型、状态机、插件与 API

### 四个平面

1. Runtime Reasoning Plane 组合确定性 read model、domain resolver、模型网关、缓存和降级契约。
2. Observation & Governance Plane 处理幂等观察、候选聚合、证据、提案、裁决、变更集和审计。
3. Release & Synchronization Plane 发布内容寻址的不可变 release，并通过 adapter import/export/reconcile。
4. Domain Pack & Policy Plane 注册 ontology、resolver、prompt、evidence/review policy、quality gate 和 projector。

### 通用数据与状态

迁移创建 15 张强制 RLS 表：observation、candidate、candidate_observation、knowledge_object、assertion、proposal、evidence、adjudication、change_set、knowledge_release、release_activation、connector_run、inference_trace、semantic_cache 和 audit_event。

状态覆盖 `observed → aggregated → proposed → evidence_pending/review_ready → approved/rejected/deferred → local_published → exported/externally_published → reconciled/superseded`。拒绝项只有在新证据、政策升级或显式 reopen 后才能重新进入审核。

### 契约

- 生产 OpenAPI 共 298 个 path，其中 knowledge API 为 19 个 path、23 个 operation。
- API 覆盖 runtime resolve、observation ingest、candidate/proposal/evidence/adjudication、change set、release activate/rollback、connector run、audit/events、health/readiness/metrics。
- 生成的 TypeScript client 独立于 GEO 业务 service。
- 插件契约覆盖 validator、normalizer/resolver、inference strategy、evidence collector、review policy、quality gate、projector 和 connector。

## 5. 请求时 LLM、认识状态、缓存、降级和评测

同一 runtime contract 支持：

- `deterministic_only`：只使用固定 release 和确定性规则；
- `llm_assisted`：只对歧义/未知项调用模型；
- `llm_required`：模型必须参与，失败行为由契约决定；
- `exploratory`：允许扩展候选和关系，事实与假设分开返回。

结果披露 `knowledge_status`、`decision_scope`、confidence/reason、adoption、knowledge/policy/prompt/model 版本、token/cost/cache、latency 和 degradation。`model_inferred` 可以按显式 policy 被本次请求采用，但只能异步进入 observation/candidate，不能变成 `reviewed_local/published`。

模型网关使用 OpenAI-compatible 协议但不绑定供应商。缓存隔离键包含 tenant/domain/task、规范化输入与安全上下文摘要、knowledge release、policy、prompt、model 和工具版本。实现和测试覆盖超时、重试、非法结构、provider 不可用、隐私拒绝、预算、缓存命中以及 fail-closed/fail-open 契约。

生产真实 smoke 使用已配置 provider 和 `gpt-5.6-luna`：已知“腾讯云”保持确定性 reviewed 结果且没有模型元数据；未知“云盾X”在 `llm_assisted` 下被标为 `model_inferred` 并按本次策略采用。首次调用耗时 5.354 秒、input/output token 为 4125/389；第二次 26 ms 命中缓存，增量 token、成本和模型 latency 均为 0。

最终 `.3` 金标集共 12 个 case、4 种 policy。identity、relation、eligibility 和 dedupe accuracy 均为 1.0，error 为 0。未授权外发时，assisted 有 3 次、required/exploratory 各 12 次 `model_denied_by_data_policy`，说明数据政策实际生效而非静默调用。

## 6. 品牌治理、SiliconIndex adapter 和数据审核

品牌包独立建模 identity、relation、roll-up、comparison eligibility 和 epistemic status。对象类型区分法人、集团、company、brand family、business unit、product、tool 和 institution；关系区分同一法人、简称、英文名、曾用名、商号、产品、业务线、子公司和家族成员。WIPO/GS1 分类只作证据，不被当作竞争关系真值。

网安域数据结论：

- 68 个实体、186 条 mention；
- 40 个实体具备公开证据并发布为 reviewed；
- 28 个实体保留为 pending candidate；
- 40 个已发布实体 `reviewed_without_evidence=0`；
- 腾讯云/腾讯、华为云/华为、绿盟/NSFOCUS、BJCA/数字认证、ZoomEye/知道创宇等按明确 relation 归并；
- 新大陆在通用网安 scope 中 fail-closed，只在 CTID/数字身份等有证据 scope 下具备资格。

SiliconIndex `2026-08-27.2` 公网数据为 185 个品牌、575 条 mention、20 个品类、605 条关系、711 条 GEO 信源记录，孤儿引用为 0。schema 为 1.1.0，content hash 为 `sha256:118980eaff29451ff8f280de44a206acc17c0d1906df6c272ab26b1836d715a7`。

平台最终 `knowledge-2026-08-27.3` 的父版本是 `.2`，content hash 为 `sha256:93a04f23f5585efa6e569a973953f65acd8ee4897108982cb73f412b3ec21261`。它记录 SiliconIndex `.1 → .2` 及 `a36b… → 1189…` 的上游血缘转换；40 个 reviewed 对象逐字段相等。

## 7. 本机自治、周期治理、同步、冲突和隐私

GEO 请求只读取本机 knowledge release；同步器异步获取 SiliconIndex 并在完整验证后原子切换。远端失败不改变 `CURRENT`。运行时还包含 bundled generated projection 作为最后一级只读后备，并明确返回 degraded 来源。

生产曾把同步 URL 指向 `127.0.0.1:9` 模拟远端中断。同步任务按预期失败，API readiness 连续 10 次均为 HTTP 200，耗时 4.6–5.9 ms，本机 release 未变化。恢复公网后同步成功，`/var/lib/geo-platform-v2/siliconindex/CURRENT=2026-08-27.2`，本地重新计算的 hash 与 Render 一致。

base/upstream/local 三方合并覆盖 local-ahead、upstream-ahead 和同字段冲突；同字段冲突进入 conflict queue，不使用 last-write-wins。只有 approved、public、已脱敏且有 evidence 的 change 可导出。

观察只持久化允许的数据字段、安全摘要和不可逆引用；公共 adapter 拒绝 tenant、项目名、prompt、完整回答和 safe context 等键。tenant/namespace/domain/visibility/data classification 从表、API、缓存到审计全链路隔离。普通请求无 release 发布权限。

## 8. 数据迁移、生产影子回放与通用性 fixture

生产数据库在发布前备份后从 `s14` 顺序迁移到 `s15 → s16 → s17_0001 → s17_0002_knowledge_trace_details`。初始 `.1` 导入 40 个 reviewed 对象，`.2` 和 `.3` 均使用 lineage-only 验证对象不变，没有复制 proposal/object。

最终生产计数：71 observation、69 candidate、40 proposal、51 evidence、40 knowledge object、3 release、3 activation、4 connector run。最新治理批次 backlog 为 29、conflict 为 0；多出的候选来自运行时发现，不会自动进入正式对象。

生产只读影子回放使用 1309 个真实历史答案：

- raw/legacy mention：11336；
- governed canonical mention：11328；
- governed eligible mention：6677；
- reviewed local：7076；unresolved：4252；
- alias collapse：8；受新资格投影影响的答案：1158；
- 目标品牌 mention：legacy 741、governed 741；
- 数据库 mutation：0。

回放没有重写历史 extraction。正式计算统一消费中间件 read model。非品牌 `source/type-fixture` 使用相同状态机、release、client 和 API contract，完成通用性验收。

## 9. 测试、故障注入、性能、安全和恢复证据

### 自动化和静态检查

- 平台任务隔离树：2643 passed、18 skipped；Ruff check/format、strict mypy、migration head 和生成物校验通过。
- `.3` 跟进：64 个聚焦单元/契约测试通过；新增测试证明“上游全局 hash 改变但 governed object 不变”可建立 lineage，并证明对象内容改变会被拒绝。
- PostgreSQL 实库集成：1 passed；开发库 `.3` 导入及幂等重跑通过，proposal/object 均保持 40。
- 平台 Turbo test/build/lint/typecheck 通过；OpenAPI client TypeScript 编译通过。
- SiliconIndex split 和 monorepo：各 3 个 test file、31 tests 通过；validate:data、build、lint、`git diff --check` 通过。lint 仅有 3 条既有 unused-variable warning。
- 组合 `pnpm check:api` 的唯一失败来自未提交用户文件 `tests/e2e/customer-product-live.spec.ts` 删除了截图 guard literal；本任务 OpenAPI 生成、client 生成和编译均独立通过，未篡改该无关修改。

### 性能

500 次/策略的 synthetic-provider benchmark：

| policy | p50 ms | p95 ms | p99 ms | model calls | cache hit | synthetic cost | errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| deterministic | 0.243 | 0.430 | 0.465 | 0 | 0% | 0 | 0% |
| assisted | 0.810 | 1.301 | 1.505 | 25/500 | 95% | $0.05 | 0% |
| required | 0.829 | 1.326 | 1.566 | 25/500 | 95% | $0.05 | 0% |
| exploratory | 0.850 | 1.215 | 1.431 | 25/500 | 95% | $0.05 | 0% |

该 benchmark 测量编排、校验、缓存和观察开销，不代表公网模型网络延迟；真实 provider latency 由前述生产 smoke 单列。

### 安全与可恢复性

- `knowledge` 15/15 张表全部启用并强制 RLS；PUBLIC table grant 为 0。
- observation、evidence、adjudication、release、activation、inference trace 和 audit event 有 UPDATE/DELETE append-only trigger。
- 未认证 runtime resolve 和 releases 查询均返回 401；health/readiness 公开，治理 metrics API 需要 session。
- Prometheus `/metrics` live 返回 200，已记录 knowledge health/readiness 和鉴权结果。
- 发布前全量备份为 `.production-backups/20260827T051856Z`；独立 PostgreSQL custom dump 为 53,946,120 bytes，SHA-256 `d6935b140cdecd9a2e2aea55df6ab27fe1fb119857e79605ea637bc143f1e4d3`。
- 最终 artifact 备份 manifest 为 `.production-backups/knowledge/20260827T055637Z/manifest.json`，9 个文件，archive hash `sha256:2bb3dca98844cf61ffbc87f7331e246b0df5a49bd2183c3ffb0e12480bdbd653`。
- 空目录恢复 `/tmp/geo-knowledge-production-restore-20260827.l36TVs` 通过；恢复后 `.3/.2` content hash 均重新验证成功。

## 10. 提交、推送、部署与 live 状态

### Git 与静态发布

| 仓库 | 提交/PR | 状态 |
| --- | --- | --- |
| `xln3/platform-v2` | `322029a` 通用中间件；`014bf22` 最终上游血缘 | 已推送 `master` |
| `Fuyujia799/GEO-auto-analysis` | `22e2724` 初始网安发布；`3064b64` 镜像 split `.2` | 已推送 `master` |
| `suzakuzhang/siliconindex-consumer` | `ad7a615`，PR #1，merge `17cc88c` | 已合并 `main`，Render 已发布 |

Render 公网 manifest 已回读为 release `2026-08-27.2`、schema `1.1.0` 和 hash `sha256:118980…715a7`，与本地 release 完全一致。

### 平台部署

- 生产代码来自不可变快照 `/home/xln/geo-system/.deploy-backups/platform-v2-master-014bf22-20260827T1353CST`，不是脏工作树。
- API systemd `WorkingDirectory` 和所有知识任务 drop-in 均指向该快照；`systemd-analyze verify` 通过。本机显示的 warning 均来自无关 vendor/host unit。
- API 于 13:54:03 CST 重启并保持 active；OpenAPI 为 298 paths。
- `/api/v2/knowledge/v1/health` 和 readiness 均返回 `status=ok`、active `.3`、previous `.2`、database reachable、model gateway configured、release verified。
- 已知“腾讯云”live 解析为 `CYB-BR-TENCENT/腾讯`、`business_unit_of`、`reviewed_local`。
- SiliconIndex sync 和治理批次实际手工运行成功；治理 report hash 为 `sha256:05b67e898aaf7cb1499bf33eacd506c0e8687919888a4ff89a947ef322f1d00c`。

### 调度

三个 timer 均为 enabled/active：

- SiliconIndex sync：下一次 2026-08-27 19:22:39 CST；
- knowledge backup：下一次 2026-08-28 03:54:06 CST；
- weekly governance：下一次 2026-08-31 04:02:48 CST。

## 11. 风险、技术债与外部阻塞

- 没有阻塞本次上线的外部权限或生产故障。
- 第一阶段仍是仓库内模块化部署，而非独立进程/仓库。ADR 已固定可提取边界；真正出现第二个生产调用方或独立扩缩容需求时再拆服务。
- 当前 governance backlog 为 29，需要按审核 SOP 持续补证和裁决；它们不会自动进入榜单。
- “远端连续故障 7 天”由时间推进/故障注入测试验证状态机，并做过一次真实 live 阻断；本次交付没有等待七个自然日。
- 模型生产 smoke 证明真实调用、采用、审计和缓存，但不是公网 provider 容量测试。生产 SLO 需继续用真实流量监控校准。
- SiliconIndex 仍有 3 条既有 lint warning；平台组合 `check:api` 仍受无关脏文件 guard 影响。两项均已隔离记录，不影响本任务生成物或生产发布。
- 用户原有未提交工作树修改均保留，未被 reset、stash、覆盖或夹带进本任务提交。
