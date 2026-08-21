# 采集与分析解耦边界

## 结论

一条答案在以下数据成功写入后就算“采集完成”：

- 原始回答及规范化版本；
- 平台分享链接、截图、HAR、SSE 等当场才能取得的证据；
- 检索词、每次检索事件、全部可观察 U occurrence、可观察 V 打开顺序和最终引用；
- U、V 和最终引用三个阶段各自的可观察状态与原始证据引用；
- `answer.capture.completed` 事件；
- 一条只引用该采集记录、不携带回答原文的版本化分析任务。

语义分析、信源网页抓取、信源审计、页面危害体检、官网诊断、拉踩识别和事实核查均不再是采集完成条件。它们失败、超时或重跑时，只能修改自己的 `platform.analysis_job`，不能把已经完成的 `platform.collection_task` 改成失败。

## 运行结构

```text
登录态采集队列
  └─ 每题：回答 + 分享链接/截图/HAR/SSE + 检索事件 + U/V occurrence
       + capture event + answer_basic job（同一事务）
       最后一题：再把轮次级 source/risk jobs 放进同一事务
       └─ 立即开始下一题，并释放上一题的分析等待

分析队列
  ├─ 每题 answer_basic：按 capture_ref 回读并校验 response_hash
  └─ 每轮 post_collection_analysis
       ├─ 全部 U 的页面抓取 → 信源审计 → 官网建议
       ├─ 全部已取得快照的 U：页面体检 A/B/C + 传导 T + 归属层
       ├─ V 页面 W 逐字贡献 → V/U−V 与高 W/低 W 对照
       └─ 拉踩识别 → 事实核查

公开信源队列
  ├─ 全部可观察 U 网页正文抓取
  └─ 官网公开页快照
```

公开信源队列与登录态采集队列物理分开。公开网页故障不会占用平台账号租约；语义或 LLM 故障不会阻塞下一题采集。轮次分析中的多个分支也相互隔离：信源抓取失败时，基于已采集回答的风险识别仍可继续。

## 状态语义

`platform.analysis_job.state` 使用独立词表：

- `not_requested`：缺少执行所需的冻结上下文，未提交；
- `queued`：任务与工作流启动命令已经持久化；
- `running`：分析 worker 已经开始执行；
- `completed`：该版本完整完成；
- `partial`：有明确截断或部分信源失败；
- `failed`：该分析器失败，但采集结果仍有效；
- `skipped`：功能关闭、依赖失败或没有适用数据。

任务唯一键包含分析器和 `policy_version`。旧结论是不可变的；规则、模型或研究方法升级时创建新版本任务，不覆盖旧判断。

答案查询以 `platform.collection_task` 为真源，因此答案在基础分析产物进入 `analytics.answer` 之前就可见。接口分别返回 `capture_state`、`answer_analysis_state`、`source_analysis_state` 和 `risk_analysis_state`，不会把“待分析”伪装成采集失败或零分。

## 与全景信源分析的关系

`page_inspection` 在独立分析队列读取本 run 全部已成功抓取的 U 页面快照；没有正文的 U 仍保留抓取状态和重试记录，不会从处理分母消失。它提供：

- 对象别名强制带实采记录或证据网址，并把同位对手、品类和权威锚纳入版本化画像，按修订保留历史；
- A0–A5、B1–B3 言论账与 C1–C4 暴露账，两个账本分别计数，禁止相加；
- 原文、权威事实、可复算数、缺席事实四种证据环节；原文必须回定位到精确字符区间，任一硬校验失败则整条结论作废；
- T1 引用广度、T2 对象存续率、T4 逐字沿用；T3 在页面/回答榜首结构尚未被程序验证前明确返回空值；
- 域名、站点元数据、作者/发布账号证据、受益方和项目历史同文快照；“最早快照”只叫候选，不冒充原发证明；
- 对历史 run 的独立重算入口。画像、策略、模型、提示词和正文哈希均被冻结，升级生成新版本，不覆盖旧结论。

以下两类输入仍是独立研究流水线，不属于普通 U：

1. `discovered_pool_snapshot`：主动搜索尚未被答案引用、但可能进入模型取材池的页面；
2. `adversarial_query_research`：N 中性、P1 弱诱导、P2 强诱导的配对差分，以及同位对手和不存在对象的基线实验。

因此，“本次 AI 实际检索返回的全部页面有没有危害、危害有没有传导”可以独立重算；“U 之外还潜伏着什么页面”和“模型还能被诱导写出什么”仍需走另外两条研究任务队列，不能拿 U 冒充全网召回。详细接口和判据见 [SOURCE_PAGE_INSPECTION.md](SOURCE_PAGE_INSPECTION.md)。

## 生产升级顺序

1. 先执行 Alembic 迁移，创建 UVW 身份与 occurrence、抓取尝试、页面版本、W、内容策略事实，以及版本化页面体检结论和精确证据区间；
2. 安装并启动 `geo-platform-v2-source-worker.service` 与 `geo-platform-v2-analysis-worker.service`；
3. 更新 API、workflow-start outbox 和采集 worker；
4. 确认三个队列分别有 poller，再放入新采集任务；
5. 若升级时仍有旧版采集 workflow history，在一个采集 worker 临时设置 `GEO_LEGACY_ANALYSIS_ON_COLLECTION_WORKER=1` 直至旧 history 排空，然后关闭。

不要先上线新采集代码再补分析 worker。这样不会丢采集数据，但分析命令会停留在 `queued`，造成不必要的积压。
