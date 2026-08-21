# 独立信源页面体检

## 这项能力解决什么

页面体检回答的是三个相互独立的问题：

1. 页面本身写了什么，属于 A/B 言论账还是 C 暴露账；
2. 这页被答案怎样使用，危害是否在传导层出现；
3. 能确认的发布主体、同文传播、受益方和更正入口分别是什么。

它不是答案采集的组成步骤。答案、分享链接、截图、HAR、SSE 和引用 URL 完成持久化后，采集就结束；公开网页抓取和页面体检通过 durable outbox 交给独立 worker。页面体检失败、关闭或重算，都不会反向改变 `collection_task` 的完成状态。

## 数据流

```text
登录态采集 worker
  └─ 回答与现场证据落库
      └─ analysis_job + workflow_start_command（同一事务）

公开信源 worker
  └─ 引用 URL 去重、抓取正文、正文哈希和页面元数据入库/CAS

分析 worker
  ├─ 读取被冻结的对象画像、模型、提示词和策略版本
  ├─ 页面层：A0–A5 / B1–B3 / C1–C4
  ├─ 传导层：T1 / T2 / T4；T3 未验证时明确为空
  ├─ 归属层：域名 + 账号证据、项目历史同文快照、受益方、更正入口
  └─ 原子写入 inspection、finding 和 exact evidence span
```

采集完成事务会冻结当时的 active profile。之后修改画像不会悄悄改变已排队任务。采集时没有 active profile，`page_inspection` 状态为 `not_requested`；创建画像后可对已经抓取过信源的历史 run 手动排一个新版本。

## 第一步：创建对象画像

```http
PUT /api/v2/source-analysis/projects/{project_pub_id}/profile
```

示例：

```json
{
  "object_name": "盛邦安全",
  "object_kind": "brand",
  "categories": ["WAF", "网络空间测绘"],
  "aliases": [{ "value": "盛邦", "evidence_url": "https://example.com/observed-alias" }],
  "own_domains": ["webray.com.cn"],
  "peers": ["绿盟科技", "阿里云"],
  "anchor_sources": [
    {
      "name": "IDC",
      "publisher": "IDC",
      "url": "https://www.idc.com/",
      "categories": ["WAF"]
    }
  ],
  "linked_entities": [{ "name": "某关联主体", "relation": "parent" }],
  "hard_anchor_available": true,
  "decision_mode": "selection"
}
```

硬约束：

- 每个别名必须带合法 HTTP(S) `evidence_url` 或 `capture_pub_id`，不允许按构词规则生成；引用采集记录时，该记录必须属于本项目、已完成且回答原文逐字包含别名；
- 声明 `hard_anchor_available=true` 时必须列出权威锚；
- I/II/III/IV 型由“硬锚是否可得 × 选型/口碑”两轴计算，客户端不能手填；
- 相同内容 PUT 是幂等读取；内容变化创建新 revision，并把旧 revision 标为 `retired`。

读取当前画像和历史版本：

```http
GET /api/v2/source-analysis/projects/{project_pub_id}/profile
GET /api/v2/source-analysis/projects/{project_pub_id}/profiles
```

## 第二步：读取或重算页面体检

查询列表和证据详情：

```http
GET /api/v2/source-analysis/projects/{project_pub_id}/inspections?run_pub_id={run_pub_id}
GET /api/v2/source-analysis/projects/{project_pub_id}/inspections/{inspection_pub_id}
```

对历史 run 按指定画像版本重算：

```http
POST /api/v2/source-analysis/projects/{project_pub_id}/runs/{run_pub_id}/inspect
Content-Type: application/json

{"profile_pub_id": "sap_..."}
```

省略 `profile_pub_id` 时使用当前 active profile。接口只接受已经成功抓取并具有 CAS 正文和 SHA-256 的 source document；尚未就绪返回 `409 source_documents_not_ready`。任务、workflow start command 和冻结输入在同一数据库事务写入。相同输入返回原任务；画像 revision、模型或提示词任一变化都会生成新的 policy version 和 workflow ID，旧结论不被覆盖。

## 分型和账本

| 账本     | 分型  | 含义                                                           |
| -------- | ----- | -------------------------------------------------------------- |
| 言论账 A | A0–A5 | 事实性负面、无据贬评、相对贬抑、维度操纵、关键遗漏、可证伪指控 |
| 言论账 B | B1–B3 | 无锚抬升、跨品类挪用、榜单结构存疑                             |
| 暴露账 C | C1–C4 | 名单缺席、尾部位次、形态弱势、素材源不署名                     |

`statement_count` 与 `exposure_count` 分开返回，禁止相加后统一称作“拉踩”。C 组所有字段禁止出现“拉踩、抹黑、诋毁、打压”；全部分型禁止无证据的动机词。A0、A5、C4 先记为 `needs_review`，外部事实核验完成前不升级成确认指控。

## 证据链为什么可回查

每个 finding 的 `evidence_chain` 只能使用四类事实：

- `source_quote`：正文逐字子串；
- `authority_fact`：必须命中对象画像声明的权威锚，并带 URL、发布方和时间；
- `recomputable`：必须带算法、输入和结果；
- `absence`：必须带检索范围、检索词、any/all 算子和程序复算命中数。

LLM 只生成候选，程序决定能否交付。窗口里的逐字引文先被换算为全文 occurrence，再落成：

```json
{
  "quote": "盛邦安全能力很差",
  "text_start": 17,
  "text_end": 25,
  "quote_hash": "...",
  "verification": "exact"
}
```

持久化前必须满足 `source_text[text_start:text_end] == quote`。重复句必须定位到产生候选的那个窗口；改写、拼接、省略号、越界 occurrence 或哈希不一致会让整条 finding 作废。详情接口返回 span，前端可以直接据此高亮原文。

质量字段同时报告候选数、接纳数、逐字引文候选数、验证通过数、命中率、窗口错误和截断字符数。没有可用 LLM 时页面记录为 `unverifiable`，不会伪造“无风险”。

## 传导与归属的边界

- T1 统计引用该页的回答数、问题数和模型数；
- T2 只在页面含对象时计算对象在引用回答中的存续率；
- T4 统计页面引用句在回答中的逐字沿用；
- T3 依赖经过验证的榜首结构，当前不具备时返回 `null` 和原因。

当页面完整扫描、页面层没有成立 finding、T1 至少三条回答且 T2 为零时，程序生成 `C1 transmission`。它只说明“页面中的对象没有在答案里存续”，不反推页面作者有过错。

归属层把域名、站点元数据、作者/账号逐字证据分别保存。相同正文 SHA-256 会在当前项目历史快照中按 canonical URL 去重并形成同文簇；“最早发布时间候选”明确不等于原发证明。受益方与行为主体始终分开，无法落到账号就只保留域名和元数据，不猜发布者。

## 当前边界

这一版分析的是 `cited_pool_snapshot`：答案已经引用且系统已经抓取的页面。它还不代表全网潜在页面召回，也不回答“模型还能被诱导写出什么”。下列能力应继续使用独立任务和结果表：

- `discovered_pool_snapshot`：主动检索池外页面；
- `adversarial_query_research`：N/P1/P2 配对差分、同位对手基线和不存在对象基线；
- 经结构化榜单验证后的 T3；
- A0/A5/C4 的外部事实核验和结论升级。

这些扩展只消费不可变采集引用、网页快照或研究批次，不得重新成为采集完成条件。

## 生产配置

分析 worker：

```dotenv
GEO_PAGE_INSPECTION_ENABLED=1
GEO_PAGE_INSPECTION_MAX_DOCUMENTS=500
GEO_PAGE_INSPECTION_MAX_CHARS=120000
GEO_AUDIT_LLM_API_KEY=...
GEO_AUDIT_LLM_BASE_URL=...
GEO_AUDIT_LLM_MODEL=...
```

凭据或模型为空时功能如实降为 `unverifiable`。`MAX_DOCUMENTS` 超出的文档和 `MAX_CHARS` 超出的正文尾部都会进入截断计数，任务终态为 `partial`，不能显示成完整通过。
