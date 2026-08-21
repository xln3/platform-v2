# UVW 全景信源事实与三端交付

## 权威口径

- U：一次 AI 问答中平台检索实际返回且系统可观察的全部 URL 候选 occurrence。
- V：U 中平台明确打开、抓取或精读的页面。平台不暴露时为 `unobserved`，不是 0。
- W：绑定来源页面版本、来源字符区间和答案关系的内容片段。最终引用 URL 本身不是 W。

站点和规范 URL 是可复用身份；`answer_source_occurrence` 是不可丢失的发生事实。同一 URL 在三个答案出现会得到一个规范身份和三条 occurrence；同一答案两个检索事件返回同一 URL，也保留两条 occurrence。

## 数据与任务链

| 层          | 权威事实                                                                                  | 关键状态/版本                                                                          |
| ----------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 现场采集    | `collection_task`、`answer_retrieval_event`、`answer_source_occurrence`、evidence、outbox | U/V/final 各自 observed/partial/unobserved                                             |
| URL 身份    | `source_site`、`source_url`                                                               | 原始 URL、规范 URL、规范化版本                                                         |
| 公开页      | `source_fetch_attempt`、`source_page_snapshot`、CAS                                       | queued/fetching/succeeded/partial/blocked/gone/retry_wait/failed                       |
| W           | `content_contribution_analysis`、`weighted_content_chunk`                                 | 正/负分析版本、页面/答案 span、哈希、分数、置信度、模型/提示词/策略/算法版本、复核状态 |
| 服务 5 策略 | `content_strategy_analysis`                                                               | V 对 U−V、高 W 对低 W、输入哈希和因果边界                                              |
| 服务授权    | `project_service_entitlement`                                                             | 五个独立 service code、catalog version 和授权状态                                      |

采集事务先原子提交回答、现场证据、检索事件、全部 occurrence、`answer.capture.completed` 和只引用该事实的分析任务。公开页抓取及所有分析异步运行；失败只改变自己的任务或抓取状态，不能污染已完成采集。W 的 `confirmed` 与 `no_evidence` 都有绑定 occurrence、页面快照、输入哈希和策略版本的不可变分析行；没有分析行仍是未知/待处理，不能用“没有 chunk”反推为零。

所有 U 都进入处理分母。URL 身份可以复用快照，发生记录不能合并。旧引用只能证明 final reference，迁移将其 U/V/W 标为 `unobserved`。工程上的分页、分批、背压、响应尺寸和单请求超时不改变全集。

## 平台可观察边界

当前适配器只保存平台真实暴露的阶段：

- DeepSeek：结构化保存实际暴露的 U、V 和 final reference；
- 豆包：保存实际暴露的 U；未能从现场证据确定的 V/W 保持不可观察；
- 通义、文心一言、腾讯元宝：现有采集未提供可证明的候选/打开阶段时，U/V 保持不可观察，只保留其真实 final reference。

这些边界必须用真实平台样本继续校验。DOM、网络协议或产品界面变化时只能升级适配器版本，不能从最终引用倒推出历史 U/V。

## API 与界面边界

管理信源接口位于 `/api/v2/internal/source-intelligence/projects/{project_pub_id}`，要求内部 intelligence 权限。固定钻取为项目→站点→URL→全部 occurrence→内部回答，并支持回答反向查看完整 UVW；URL 详情继续展示抓取尝试、页面版本、版本化体检、finding、逐字 span 和证据链。站点默认按 distinct URL、U occurrence、最近时间、host 排序；URL 默认按 U occurrence 排序；列表全部分页。

客户接口位于客户安全投影，只返回已授权的五项服务状态和可交付 DTO。未购买服务不返回结果。客户身份不能读取内部站点/URL 目录、完整第三方 U、未复核 finding、提示词或跨项目经验。

Operations 导航按项目与商务、采集、分析、五项服务生产、内容生产与发布、报告与交付组织。账号、浏览器、会话、队列、告警、接管、事件和审计属于采集；服务 2 和 3 是独立工作台。Customer 以项目首页、我的五项服务、报告与交付物、项目资料与授权组织。

## 服务消费规则

1. 服务 1 以真实回答、品牌提及、推荐和排名为主，UVW 只提供内部解释。
2. 服务 2 只消费带作者、委托或审批归属证据的己方已投/拟投内容。
3. 服务 3 的候选集合是全部 U，不缩成 V、final 或 W。
4. 服务 4 只按客户确认官网域名区分未进 U、U 未进 V、V 未进 W、进入 W。
5. 服务 5 同时消费 V/U−V 与高 W/低 W 观察差异，再由服务 1 同口径前后测验证。建议只是假设，不承诺因果或排名提升。

## 迁移、回滚与验证边界

迁移 `s06_0037_uvw` 创建新事实表、RLS、索引、角色授权并兼容回填旧引用/旧 source document。升级顺序是数据库→source/analysis worker→API→前端。回滚前先停止新采集并导出新表；downgrade 会移除 UVW 新表，但不改写旧回答与旧报告。

自动测试覆盖重复 URL occurrence、同 URL 多检索事件、重复 V/final 发生、750 个候选、历史小 limit 下完整抓取规划、V 不可观察、final 不自动成为 W、逐字 span 篡改拒绝、W/服务 5 历史重算版本、客户权限拒绝、管理双向钻取、三端导航和服务 5 两组对照。真实生产迁移、真实平台 U/V 网络证据、大规模抓取积压和客户人工复核仍需在部署阶段执行，不能由单测冒充已验证。
