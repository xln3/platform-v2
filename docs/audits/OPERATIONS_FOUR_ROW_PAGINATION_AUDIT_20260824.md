# Operations 四行分页与 AI 评测只读工作区审计

审计日期：2026-08-24

本文记录会话 02 对 Operations Web 中不定长的运行、任务、队列、采样、周期和事件集合的有界审计。数据行统一使用 `PAGE_SIZE = 4`；表头、汇总、说明和分页器不计入四行。

## 已分页模块

| Operations 模块                | 数据真源                                         | 分页策略                                                    | 全量口径                                                             | 结论                                                                          |
| ------------------------------ | ------------------------------------------------ | ----------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| 项目与商务总览的项目组合       | `GET /api/v2/operations/business-overview`       | 服务端复合键游标，绑定搜索/状态/关注过滤，每页 4 项         | 响应 `summary` 和 `filtered_total`                                   | 已分页；该先行会话的游标是无内部 ID 的旧 envelope，不是本会话的共享 HMAC 格式 |
| AI 评测“本次评测配置”问题明细  | 当前/Run 绑定的冻结快照                          | 完整冻结快照前端独立页码，每页 4 题                         | 问题组数、问题总数和任务规模仍来自完整快照                           | 已分页；项目切换/配置版本切换回首页；摘要不再展示调度频率                     |
| AI 评测采样进度问题×采样位矩阵 | `GET /api/v2/analytics/sampling-progress`        | 服务端 `page/page_size=4`，响应返回当前 4 行和完整页元数据  | `observed_cells` / `total_cells` / `answer_count` 保持全量           | 已分页；数字窗口、总条数、总页数和跳页齐全；横向采样位仍可滚动                |
| AI 评测采样记录                | `GET /api/v2/collection/runs?project_pub_id=...` | 服务端 `page/limit=4`；旧调用方继续兼容 HMAC keyset cursor  | `X-Total-Count` / `X-Page-Count` + `/collection/runs/summary`        | 已分页；数字窗口、总条数、总页数和跳页齐全；不再从租户前 N 条浏览器过滤       |
| AI 回答浏览器                  | `GET /api/v2/analytics/answers`                  | 租户/项目/run/平台/地域/模式绑定的签名游标，每页 4 行       | 不用当前页冒充全量指标                                               | 已分页；回答、证据和关系详情使用对话框                                        |
| 执行控制面“项目与冻结计划”     | `GET /api/v2/projects` + current config 投影     | 服务端签名游标，每页 4 个项目；只为当前页读生效/待生效计划  | 项目卡的配置语义来自 current/effective API                           | 已分页                                                                        |
| 执行控制面“运行与任务矩阵”     | `GET /api/v2/collection/runs`                    | 租户级签名服务端游标，每页 4 行                             | run summary 提供全量运行/任务/活动运行数                             | 已分页；暂停/恢复/取消/重试仍只作用于所点运行                                 |
| 周期监测任务                   | `GET /api/v2/schedules`                          | 签名服务端游标，每页 4 行                                   | 不从当前页推导租户全量                                               | 已分页                                                                        |
| 平台账号目录                   | `GET /api/v2/platform-accounts`                  | 签名服务端游标，每页 4 张卡                                 | `X-Total-Count` / `X-Active-Count`                                   | 已分页                                                                        |
| Break-glass 审批队列           | `GET /api/v2/break-glass`                        | 状态过滤绑定的签名服务端游标，每页 4 行                     | 当前页不承担全量 KPI                                                 | 已分页                                                                        |
| 人工接管队列                   | `GET /api/v2/interventions`                      | 签名服务端游标，每页 4 行                                   | `X-Open-Count`                                                       | 已分页                                                                        |
| 工作流与会话时间线             | `GET /api/v2/platform-events`                    | `(occurred_at DESC, pub_id DESC)` 签名服务端游标，每页 4 条 | 当前页不承担全量 KPI                                                 | 已分页                                                                        |
| 采集手机账号目录               | `GET /api/v2/collection-accounts`                | 签名服务端游标，每页 4 行                                   | 服务端总数头                                                         | 已分页                                                                        |
| 采集浏览器实例                 | `GET /api/v2/collection-browsers`                | 签名服务端游标，每页 4 行；30 秒轮询只刷新当前锚点          | 服务端总数头                                                         | 已分页                                                                        |
| 单个采集账号事件               | `GET /api/v2/collection-accounts/{id}/events`    | 账号过滤绑定的签名游标，每页 4 条                           | 不用当前页推导全量                                                   | 已分页                                                                        |
| 服务 2 run 选择器              | `GET /api/v2/collection/runs?project_pub_id=...` | 项目绑定的签名游标，每页 4 个 run                           | 不用当前页推导全量                                                   | 已分页                                                                        |
| 服务 2 全 U 帖子表 / 关系发现  | Service 2 corpus item/finding API                | 批次、项目及筛选条件绑定的签名游标，每页 4 行               | `filtered_count` / `all_u_total` / `all_findings_total` 保留全量分母 | 已分页                                                                        |
| 效果对比 run 选择器与对比历史  | runs API + `GET /analytics/comparisons`          | 两个 run 选择器和对比列表分别维护签名游标栈，每页 4 条      | 结果详情不计作列表行                                                 | 已分页，三个页面状态互不干扰                                                  |
| 正式报告生产队列               | `GET /api/v2/reports/formal-production`          | 项目/状态绑定的签名服务端游标，每页 4 条                    | 队列详情/产物在当前实体内展示                                        | 已分页                                                                        |
| 帖子分析任务与任务条目         | `/api/v2/post-analysis/tasks` 及 task items      | 任务、条目各有独立的服务端签名游标，每页 4 条               | 任务状态不由当前四条反推                                             | 已分页                                                                        |
| 发帖批次队列                   | `GET /api/v2/posting/batches`                    | 租户/状态绑定的签名 `(created_at, pub_id)` 游标，每页 4 批  | `X-Total-Count`                                                      | 已分页；轮询保持当前页                                                        |
| 当前发帖批次的目标条目         | 单个批次详情                                     | 有上限的完整批次响应本地页码，每页 4 条                     | 批次级完成/失败汇总不受当前页影响                                    | 已分页                                                                        |
| SOP 项目组合                   | `GET /api/v2/sop/projects?page=1&page_size=4`    | 服务端页码，每页 4 项；响应返回总条数与总页数               | `total_count` / `total_pages`                                        | 已分页；旧 `cursor/limit` 契约已删除，传入旧参数返回 422                      |
| SOP 文章成熟度与各阶段监测记录 | dashboard / 各 stage 列表端点                    | 各集合独立服务端页码，每页 4 条；前端按页请求               | 阶段页响应返回总条数/总页数；步骤进度来自 dashboard                  | 已根治；无本地切片、无 `limit=100`、无丢弃游标，可直接跳到第 101 条以后       |

## 明确例外

| 集合                                               | 不套用四行的理由                                                                         |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------- | -------------------------- |
| 五平台 SLA 维度和五平台图标列表                    | 固定且需完整对照的 5 个平台维度，不是任务队列                                            |
| 发帖供应商账号卡                                   | 由有限 provider catalog 生成的配置维度，不是无界运营明细                                 |
| KPI/说明/空状态/当前实体详情                       | 不是数据行；详情中的回答、证据和报告产物不按列表行计数                                   |
| 品牌/竞品/地域分析、风险案例、官网审计、媒体价格表 | 业务分析结果，不是运行/任务/采样/队列型集合；保留它们自身的业务分页口径                  |
| 报价行、开户步骤、用户选择项                       | 用户编辑或固定流程维度，不是运行队列                                                     |
| SOP 14 个步骤导航和步骤指标                        | 固定工作流维度；其中真正不定长的阶段记录已独立分页                                       |
| 旧 `/platform/operations?section=...` 生命周期快照 | 仅保留为旧直链兼容快照；正常导航已指向 `/platform/operations/execution#platform-accounts | interventions | events` 的真实独立游标集合 |

## SOP 断代契约

- 2026-08-25 根治了 `loadSopStage` 硬编码 `limit=100` 并丢弃 cursor 的问题：SOP 所有不定长 GET 列表统一只接受 `page/page_size`，响应统一返回 `page/page_size/total_count/total_pages`。
- 详情响应不再内嵌无界的 versions/checks/observations 数组；对应资源改由独立页码端点读取。旧 `cursor` 和 `limit` 不保留兼容分支，请求会显式返回 `legacy_pagination_removed` 422。
- 数据库集成验收在同一查询集写入 105 条后，直接请求第 26 页返回第 101–104 条，第 27 页返回第 105 条。

## 盛邦真实验收快照

- 项目：`prj_68ER9J6QBX054EAX52G7BEF7PH`（盛邦安全-GEO验证），租户 `tnt_0H7G8QYWPP43J5BXXWCDZD1C2Y`。
- 2026-08-24 只读生产快照：136 问、34 个问题页、6 个采样位、816 格、555 个已观测格、1143 条合格回答。
- 同一只读快照共有 474 个 run、119 页；验收覆盖第一页和直接跳转第 119 页，保留真实项目/run/回答公开 ID，不使用虚构项目。
- 冻结快照位置：`tests/e2e/fixtures/sbaq-readonly-pagination-20260824.json`；三视口浏览器用例：`tests/e2e/operations-readonly-pagination.spec.ts`。
