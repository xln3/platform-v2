# 服务 2：全部 U 信源帖子核查

## 权威语义

自 2026-08-24 起，服务 2“主动拉踩内容核查”的入池总体是项目冻结范围内的全部
`answer_source_occurrence`。作者、发布方、委托方、审批记录或己方归属证据均不是入池条件。

本说明取代此前“服务 2 只核查己方已投/拟投内容”的产品口径。历史
`own_content`、发布前合规流程、旧 judgment、旧 `formal-outbound-disparagement-v1` facts 和已签发
报告保留原义；它们不得被重解释或迁写成全 U 事实。

服务 2 与服务 3 保持分离：

- 服务 2 从全部 U 语料逐帖发现“谁评价谁”、次级放置、比较操纵和误导性遗漏。
- 服务 3 从客户目标品牌受害方视角聚合来源原文、AI 回答传导和影响范围。

## 分母与数据链

U 的持久化真源是 `platform.answer_source_occurrence`。一条 occurrence 绑定回答任务、运行、
可选检索事件和 `source_url`；规范 URL 只提供可复用身份。抓取写入
`source_fetch_attempt` 和不可变 `source_page_snapshot`，解析页面可关联 `source_document`，W 片段再绑定
页面版本、来源区间和回答关系。

```text
冻结 tenant/project/runs/window/U boundary
  → 枚举全部 answer_source_occurrence
  → 每个 occurrence 物化一个 corpus item
  → 按 source_url 去重抓取并绑定不可变 snapshot
  → 实体/关系候选与严格证据校验
  → 追加式人工审核
  → 冻结 v2 fact manifest
  → 正式报告只读渲染
```

`expected_occurrence_count` 是业务分母，`distinct_url_count` 只是网络工作口径。同一 URL 在三个回答中
出现时只需抓取一次，但必须保留三个 corpus item、三份回答/查询上下文和三个可达的处理状态。
抓取阻断、页面失效、待重试、待人工补证和不可观测都留在分母内；分页、分片和
Continue-As-New 只限制单次工作量，不截断全集。

运行只充当已经停止变化的冻结信封，不是成功率门禁。建批允许 `completed` 和
`completed_with_failures` 两种终态，拒绝仍可能变化的 active run。批次随后按单个 query 记账：

- 成功 query 的全部 U occurrence 入池；成功但没有 U 也单独记为 `succeeded_without_u`，不能伪装成失败。
- 失败 query 记入 query coverage gap 及失败码，不生成虚构的 0-U 结果，也不删除同 run 中成功 query 的 U。
- 因此“4 个 query，1 成功、3 失败”会保留 1 个成功 query 的全部 U，同时明确披露
  `selected=4 / succeeded=1 / failed=3`；它不是完整覆盖，但也不会整 run 丢弃。

## 持久化投影

迁移 `s08_0001_service2_all_u` 是加法迁移，建立八张服务 2 主表；
`s13_0001_service2_query_outcomes` 再增加 query 结果账本：

| 表                            | 用途                                                                             |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `service2_corpus_batch`       | 冻结范围、权益 revision、两个 policy version、覆盖分母、生命周期与 manifest hash |
| `service2_corpus_batch_run`   | 批次纳入运行的确定性顺序                                                         |
| `service2_corpus_item`        | 每个 U occurrence 的稳定上下文、页面版本和可解释处理状态                         |
| `service2_analysis_attempt`   | 模型、人工或校验失败的追加式尝试留痕                                             |
| `service2_relation_finding`   | A/B 账本、实体方向、等级、逐字证据、事实核查和独立归属                           |
| `service2_finding_review`     | 带幂等键和乐观并发版本的追加式审核决定                                           |
| `service2_batch_event`        | 批次创建、控制和冻结事件                                                         |
| `service2_fact_manifest`      | 可回校 hash 的不可变正式报告事实                                                 |
| `service2_corpus_batch_query` | 每个入选 query 的终态、失败码、answer 是否存在及其 U 数量                        |

迁移为旧项目、运行、任务、URL、occurrence、snapshot、document、fetch attempt 和服务权益增加复合候选键，
使跨表指针在数据库边界证明 tenant/project/run 一致。九张服务 2 表均启用并强制 RLS、撤销 PUBLIC 权限，
再向仓库运行时角色授予所需权限。存在服务 2 历史时 downgrade 会拒绝删除事实。

当前版本常量：

- corpus policy：`service2-all-u-occurrence-v1`
- query coverage policy：`service2-query-outcomes-v1`
- judgment policy：`service2-entity-relation-v1`
- frozen facts schema：`formal-service2-source-corpus-v2`

## 判据与证据门

finding 将内容判断、事实真假和发布归属分开保存。

- A `statement` 是有页面逐字证据的言论账；B `exposure` 是 occurrence 的暴露上下文，不能冒充原文言论。
- L0 无可交付言论；L1 是事实性负面，允许作为核查信息交付但不是拉踩。
- L2a 是无事实锚点的直接贬评；L2b 必须同时满足目标被置于次级和缺少事实锚点。
- L3a 必须有比较行为及维度、样本或口径操纵；L3b 必须有会造成误导的关键事实遗漏。
- wire/database schema 保留 L4；当前仓库缺少所引用母本的 L4 要件映射，因此 v1 对 L4 fail closed，
  不凭“诋毁性断言”摘要自造接受规则。`peer_elevated`、`scope_narrowed` 或 `industry_wide`
  单独出现也不构成拉踩。

所有客户案例必须同时通过以下门：

1. quote 是绑定 snapshot 正文的精确子串，offset、上下文范围和 `text_sha256` 全部一致；
2. 可视证据与同一页面版本、quote 和 hash 回校成功；
3. finding 的 schema、方向、等级必要条件和 policy version 校验成功；
4. 事实核查有 reviewable 依据，或以 `unverifiable` 明确记录核查边界；
5. 最新追加式人工审核决定为 accepted。

校验失败会 fail closed，并保留受限的 `service2_analysis_attempt` 审计记录。逐字存在只证明页面说过，
不证明陈述为真；`factcheck_claim`、verdict、依据和边界独立保存。

publisher 与 commissioner 各自记录 party、evidence 和 `verified | probable | weak | unknown`。
文本中的 speaker、目标或受益者不自动成为发布/委托归属。`unknown` 仍进入全文核查，但任何客户文案
不得据此声称“竞品委托”“受雇”“水军”或“有组织攻击”。

## API、权限与生命周期

内部前缀是：

`/api/v2/internal/service2-source-corpus/projects/{project_pub_id}`

接口提供批次创建/当前批次/详情、corpus item 列表、finding 列表/创建/详情、追加审核、
start/pause/resume/retry/cancel、freeze 和只读 manifest。集合接口默认每页 4 条、最大 100 条，使用绑定
tenant 与筛选条件的签名 keyset cursor；响应同时返回当前筛选总数和不随分页/筛选改变的全部 U 总数。

- 读：`formal_report:read` 或 `intelligence:read`
- 启动与控制：`formal_report:produce` 或 `intelligence:write`
- 审核与冻结：`intelligence:review` 或 `report:review`

创建、控制、审核和冻结均使用幂等键；审核另要求 `If-Match`。所有资源在后端校验 tenant、project、
active service entitlement 和状态转换，不依赖前端隐藏。批次进入 `review` 后才能冻结；冻结 manifest
是内容寻址、只读且可重放的正式事实。

## 工作台与正式报告

Operations 服务 2 工作台按五段组织：范围与覆盖、全部帖子处理队列、实体—关系发现、待审核 finding、
案例与交付。归属只是可选筛选/标签，默认视图始终是全 U。宽表只在自身容器横向滚动。

联网分析模型在建批前由下拉框选择，价格、能力、联网方式和默认/推荐标记来自服务端允许清单；
选择值随批次 scope 冻结，重放时不读取页面当前选择。品牌调研和 Service 2 复用
`@geo/design-system` 的同一套 `ModelSelect` 组件和 `ModelSelectOption` 选项契约。两个后端入口也从
`GEO_RESEARCH_LLM_MODELS` 这一份联网模型 allow-list 分别生成新的目录响应；前端再分别用 `map` 构造
新的 option objects。品牌调研与 Service 2 是两个独立受控实例，各自保存选择，任何一侧切换模型都不会
联动另一侧。共享的是组件、模型集合和展示语义，不是可变状态。

Service 2 分析器复用品牌调研已经验证的多传输联网边界：GPT 使用 Responses `web_search`，Qwen 使用
`enable_search`，Gemini search 型号使用内建 grounding，Claude 使用服务端 web search。模型输出仍是不可信
候选，必须经过逐字 quote/offset/hash、等级必要条件和事实核查来源绑定后才能进入 finding。密钥只从
服务端环境读取；模型目录、请求体、数据库、事件和前端 bundle 均不包含凭据。

新正式报告 adapter 只读取匹配项目与窗口的冻结 v2 manifest，重新校验 canonical hash，渲染时不联网、
不调用模型、不补数字。若 U 未完整物化，或仍有 blocked/gone/retry/manual/failed 等证据缺口，报告显式
标为 evidence insufficient。2026-08-24 之前创建、带 SOP 项目的旧服务 2 请求仍走只读 legacy adapter，
从而保证旧 facts 和产物可复现；新服务 2 不要求 SOP 项目，服务 5 仍要求。

## 当前 live 边界与下游契约

页面抓取复用现有 source fetch 工作流。服务 2 已接入服务端配置的 Inferera 联网 LLM 分析器；生产环境
仍必须通过秘密管理设施注入有效凭据。未配置凭据、模型不在允许清单、正文越界、上游失败或输出未通过
严格 schema/证据门时，item 会诚实进入 `manual_evidence_required` 并保留失败尝试，不会用词典或假模型
生成正式 finding。被阻断页面仍需合法重试或人工补证，模型调用成功也不等于人工审核通过。

会话 06 可复用 `domain/scoring/service2_source_corpus.py` 的等级、A/B 账、实体方向、证据门和归属 schema，
但应维护自己的受害方任务、分母、审核和冻结事实。会话 07 应消费
`formal-service2-source-corpus-v2` 的稳定 case facts，统一截图高亮和多格式渲染。会话 08 负责在合并共享
工作树后由 Pydantic 真源统一生成 OpenAPI 和 TypeScript client；功能会话不手改生成物。
