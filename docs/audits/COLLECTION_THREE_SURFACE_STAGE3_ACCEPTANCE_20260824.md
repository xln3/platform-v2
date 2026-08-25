# 三表面采集 Stage 3 验收证据草稿

日期：2026-08-24

契约集：`collection-three-surface-v2-20260824`

状态：`DRAFT / IMPLEMENTATION VERIFIED / REMOTE MIGRATION CHAIN BLOCKED`

范围：外部提交最多一次协议、无 I/O coordinator、提交事务 schema、真实 PostgreSQL repository，以及 capture/fact/outbox 的恢复边界

## 当前结论

Stage 3 的 domain/coordinator/repository 纵切已经完成、复验并推送。最终 `s10` migration、GC/object-intent 边界和 runtime-role ACL 也已在 disposable PostgreSQL 上通过，但暂时不能进入远端 migration chain：其直接父 revisions `s08/s09` 仍是其他工作流拥有的未提交文件，且尚未进入 `origin/master`。

因此，当前结论是“实现与本地数据库证据通过，远端 migration 链阻塞”，不是 Stage 3 最终接受，也不是整个会话 03 完成。不得为了提交 `s10` 而抢占、复制或改写 `s08/s09`。

本草稿不授权部署、迁移共享或生产数据库、启动 Temporal worker、连接真实 provider/Web/App gateway，或发送任何真实采集请求。本阶段没有部署，也没有发生真实外部发送。

## 目标与边界

Stage 3 的目标是把 Stage 2 已冻结的 binding、grant、resource lease/fence 和 quota truth 接到一个可持久恢复的提交事务协议，同时保持以下硬边界：

- workflow/coordinator 只携带 operation、reservation、grant、lease、partition/cursor 等常量大小引用；
- prepare/reserve 与 resource/grant resolve 是两个有序阶段，reservation identity 只能来自数据库真实预占结果；
- preflight 不拥有 submit capability；只有唯一 owner CAS 的新鲜获胜者能够构造一次性 submit command；
- `SENDING`、`CONFIRMED_SENT` 和 `SEND_UNKNOWN` 恢复路径不得重新提交；活跃 owner 的 CAS loser 不得抢占 reconciliation；
- SubmitGateway、CaptureGateway 和后续 AnalysisGateway 严格分离；capture/analysis 重试不允许进入 submit 路径；
- terminal send truth、quota settlement/release、capture truth、slot fact、governance effect 和 outbox 必须具备可验证的原子或幂等恢复边界；
- datetime 进入 canonical hash 前递归归一为 UTC；capture provenance 必须保存 channel、protocol/product/adapter revision、数据分类、DLP revision 和 retention；
- surface/product mismatch 必须 quarantine，不能通过后续 capture retry 洗白。

Stage 3 不包含完整 Stage 4 Temporal execution partition、Continue-As-New、scheduler、pause/resume/cancel 和 worker 隔离，也不包含 Stage 5 的完整 analysis/reporting 执行链。

## 已推送提交

当前 `origin/master` 包含下列提交：

| Commit    | 已推送主题                                                     | 证据范围                                                                                                           |
| --------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `e3a546c` | `feat(collection): add resumable three-surface identity plane` | Stage 1 可恢复 campaign identity/materialization 基线                                                              |
| `ae4cb20` | `feat(collection): add execution governance and quota plane`   | Stage 2 binding/grant/lease/fence/quota 基线                                                                       |
| `7a72bee` | `feat(collection): add at-most-once submission protocol`       | send/capture/analysis truth 与最多一次提交协议                                                                     |
| `04e8e2a` | `feat(collection): harden submission crash recovery`           | 无 I/O coordinator、durable recovery 与 crash harness 加固                                                         |
| `a6e18e2` | `fix(collection): derive quota reference after reservation`    | reservation identity 只能在原子预占成功后产生                                                                      |
| `389c6c1` | `feat(collection): bind durable capture provenance`            | UTC canonical hash 与三表面 capture provenance                                                                     |
| `bd9879c` | `fix(collection): bind terminal transitions to owner fence`    | terminal transition 与 owner dispatch/fence 的持久绑定                                                             |
| `b856037` | `feat(collection): add durable submission repository`          | restricted-entry PostgreSQL repository、durable capture admission/object intent、terminal/base fact 和真实 PG 纵切 |
| `d2ea2c4` | `fix(collection): align submission freeze gate`                | 将 repository 的物化终态门与 Stage 1/database 的唯一真值 `complete` 对齐，并固定回归断言                           |

这些提交证明协议、纯编排边界和 fail-closed repository 已进入远端历史；repository 尚未接入生产 router/worker，且远端仍没有 `s10` schema。它们不证明 Temporal 或生产链路已经验收。

## 当前已验证事实

### Stage 1/2 回归

当前工作树执行了不连接数据库的 Stage 1 + Stage 2 聚焦回归，结果为 `188 passed`，另有两条既有 Alembic `path_separator` deprecation warning。

该集合覆盖：

- 279,000 slot 流式生成、不同 chunk size 的 identity/digest 一致性；
- chunk 短事务、lost commit acknowledgement、失败回滚和断点恢复；
- incomplete campaign 不能 frozen，scheduler 只能读取 persisted frozen campaign；
- V2 不导入 V1 `run_service`，也不存在固定 10,000 逻辑总量限制；
- capability、binding、typed grant、multi-scope quota、lease generation/heartbeat、owner fencing 和跨 tenant/project fail-closed；
- `s07_0001_surface_identity` 与 `s07_0002_execution_governance` 的离线 migration 契约。

相关源和测试的 Ruff format/check 已通过。除干净基线文件 `s07_0001_collection_surface_identity.py` 的既有 SQLAlchemy typing 问题外，Stage 1/2 相关核心源的 strict Mypy 已通过。该基线 typing 问题不等同于 Stage 3 repository 验收。

### Stage 3 已提交 repository 边界

已提交代码表达并由单元/crash harness 与真实 PostgreSQL 纵切覆盖以下协议事实：

- preflight 与 submit capability 分离；
- 唯一新鲜 owner claim 才能进入 submit-once；
- owner 死亡后的 send reconciliation 依赖 durable WAL、exact lease/fence truth 和独占 reconciliation claim；旧 fence 永不复活；
- direct owner 只有完整 authority 仍 live 时才能执行 capture；reconciled owner 只能证明旧资源已终止，不能自动获得 capture authority；后续独立、无 submit 权限 capture authority 仍留给后续阶段；
- quota fake 使用多 scope effects、确定性 effect-set hash 和 append-only unique ledger；
- capture command、provenance、normalization 和 terminal transition 进行精确回放校验；
- API timeout、Web 单一 submit action 和 App crash 均遵守 no-resend 边界；
- terminal send truth 会先形成 base fact/outbox，capture 只能追加更高 fact version，既有 capture/analysis fact 重放不得降级；
- deterministic object intent 在 upload 前由 `operation + attempt` 生成，upload-before-manifest 崩溃重放复用同一 staging/object identity；
- surface/product mismatch 固定写入 `invalid_surface_or_product`，quarantine 到期转 orphan 后仍保持同一正式事实语义。

Stage 4 开发期间的复核发现 repository 曾把 Campaign 物化终态拼为不存在的 `completed`，而 Stage 1 领域和 `s07` schema 的唯一合法值是 `complete`。该问题已由 `d2ea2c4` 修正；仓储单测固定验证 prepare 与 exact-replay 两个冻结门只接受 `frozen + complete`。修正后仓储单测为 `29 passed`，Ruff 与 strict Mypy 均通过。

根代理此前的 Stage 3 聚焦回归结果为：`138 passed, 1 warning`；最终隔离 PostgreSQL `geo_verify_final4` 的 repository 纵切为 `6 passed`。上述终态错字不在原真实 PG 纵切覆盖路径内，因此不能引用那 6 个用例声称其已提前发现该问题。修正后的 Stage 1–4 非数据库聚焦回归为 `248 passed`；最终链进入远端后仍须重新执行真实 PostgreSQL prepare 纵切。测试未连接任何 provider、浏览器、App、object storage 或真实 event bus。

## Migration、repository 与提交链状态

Stage 3 migration、PG 审计和 repository 已冻结。`s10` migration SHA-256 为 `218f9b071a98a98fdfe4b45230379de09ac6e3346a97e3f6322bd8e7379f53bb`，静态 migration 测试 SHA-256 为 `173ff2d8df7e9ad1dd0311a7f18dd7ad1b2980b35f85ac96cd93d9ccd1719d2f`。

当前父任务拥有的 `s08_0001_service2_all_u_corpus.py` 与 `s09_0001_operational_keyset_indexes.py` 尚未进入 `origin/master`。`s10_0001_submission_transactions` 直接以 `s09_0001_ops_keysets` 为 down revision，因此不能绕过或代替这两个父 revision 独立提交。依赖 `s10` 新对象和 ACL 的 runtime-role 修改也暂时不能单独提交。为避免抢占父任务文件、制造断裂 Alembic 链或把未验收 ACL 混入远端，本草稿仅将 `s10` 和 runtime-role 保持为未提交；repository 已通过独立 fail-closed capability gate 安全推送。

### Disposable PostgreSQL 边界与异常记录

最终根代理验证只使用专用容器 `geo-s10-verify-20260824-root` 的 loopback 端口 `32776`，在从 `template0` 创建的 `geo_verify_final4` 上完成 empty-to-head、runtime-role ACL 和 6 个 repository integration cases。另一轮独立验证完成 empty-to-head、downgrade-to-s09、re-upgrade-to-head，以及 quarantine/orphan、staging intent 和 current-truth GC guard 的真实函数场景。

早期曾有两次 Alembic 命令错误使用 `DATABASE_URL`；本项目 Alembic 实际读取 `GEO_POSTGRES_DSN`，因此命令误连默认 `127.0.0.1:55433`。两次均在创建首个父约束时立即失败，PostgreSQL transactional DDL 回滚，且当时确认 Alembic version 未推进；此后没有再次连接该实例。最终证据全部在 `32776` 重建，`55434` 生产实例从未连接。

## 待最终验收项目

下表区分本地证据与远端 migration-chain 状态；`LOCAL VERIFIED` 不等于生产可用或 Stage 3 最终接受：

| ID           | 验收项目                                            | 状态                            | 最终通过所需证据                                                                                                                                   |
| ------------ | --------------------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `S3-PG-01`   | `s10` migration 完整 upgrade/downgrade/re-upgrade   | LOCAL VERIFIED / REMOTE BLOCKED | disposable PG16 已通过完整循环并记录最终 SHA，最终容器已清理；仍需父 revisions 进入 origin 后重验远端最终链                                        |
| `S3-PG-02`   | RLS、runtime-role ACL 与 restricted functions       | LOCAL VERIFIED / REMOTE BLOCKED | 10 张表均 RLS + FORCE RLS；29 个函数对 PUBLIC/API 拒绝；worker 仅 12 个 public entrypoints；真实 role 的跨租户、直写和 public API EXECUTE 负例通过 |
| `S3-REPO-01` | prepare + multi-scope reserve 外层事务              | PARTIAL VERIFIED                | exact replay/payload drift 已真实 PG；blocker 全回滚与 resolve-after-reserve 已单元覆盖，最终链落地后仍应补真实 PG blocker case                    |
| `S3-REPO-02` | owner claim、活 owner gate 与 reconciliation        | PARTIAL VERIFIED                | CAS 并发唯一 winner、活 owner gate、preflight not-sent 和 owner-loss capture recovery 已真实 PG；全 terminal matrix 留给最终链复验                 |
| `S3-REPO-03` | terminal + quota + base fact/transition/outbox 收敛 | PARTIAL VERIFIED                | preflight terminal 原子释放、capture/fact/outbox 与版本提升已真实 PG；无 capture 的完整 `SEND_UNKNOWN` corruption matrix 仍需最终链纵切            |
| `S3-REPO-04` | capture truth/manifest/link/fact/outbox round-trip  | LOCAL VERIFIED                  | active command、attempt identity、三表面 provenance、mismatch quarantine、immutable link、fact/outbox 和稳定 reason code 已真实 PG round-trip      |
| `S3-GC-01`   | quarantine/orphan GC lifecycle                      | LOCAL VERIFIED                  | retention/legal-hold/observation、current truth pointer、非 current staging 和 quarantine orphan 的真实 PG 正负例通过；manifest/hash/reason 保留   |
| `S3-OBJ-01`  | object intent 与数据库外 staging 崩溃窗口           | LOCAL VERIFIED                  | fake object store 已证明 upload-before-manifest 后同一 blob/command/primary；真实 begin function 重算 intent；未连接真实 object service            |
| `S3-OBJ-02`  | deterministic object intent exact replay            | LOCAL VERIFIED                  | `operation + attempt` 的完整 64 位 key、stored command、drift 拒绝和不产生第二 primary 已通过                                                      |
| `S3-IT-01`   | 最终 repository 单元与真实 PostgreSQL 纵切          | LOCAL VERIFIED / REMOTE BLOCKED | 根代理 `138 passed` + 最终 schema `6 passed`；父 revisions 入 origin 后需从远端 checkout 重跑同一集合                                              |

## 明确未完成或未授权

- 最终 `s10` migration 和 runtime-role 尚未进入远端；因此还不能从干净的 `origin/master` checkout 复现完整 Stage 3 验收包。
- 尚未完成真实 Temporal history 的 Stage 3 回放；旧 V1 history replay 仍是已知未验证项。
- 尚未实现或验收生产 router/scheduler/outbox/worker/gateway 的完整 wiring。
- 尚未启动真实 V2 Temporal workflow、task queue 或 Continue-As-New 执行链。
- 尚未执行 live API/Web/App collection、shadow、canary 或生产采集。
- 尚未部署、重启服务、应用共享/生产 migration、执行历史 backfill 或发送真实外部请求。

## 草稿转为最终验收的条件

只有在以下条件全部满足后，才可把本文状态改为最终通过：

1. `s08/s09` 父 revisions 已由其 owner 审核并进入 origin，`s10` 基于最终链可独立复现；
2. 上述所有 `PARTIAL VERIFIED`/`REMOTE BLOCKED` 项补齐可复现命令、精确测试结果和 disposable PostgreSQL 证据；
3. repository 对 domain truth、SQL 枚举、hash/key、timestamp、nullable shape、RLS scope 和 replay 条件逐字段 round-trip；
4. GC/object intent 覆盖数据库外 object write 的崩溃窗口；
5. runtime-role ACL 在最终 schema 后重验且没有权限回扩；
6. 最终回归、Ruff、strict Mypy 和 diff-check 通过；
7. 若进入 Temporal 或 live 阶段，必须另行取得明确授权，并单独记录无重发和外部副作用证据。
