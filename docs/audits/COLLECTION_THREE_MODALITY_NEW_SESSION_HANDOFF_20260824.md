# 采集三模态重构：新会话无损交接入口

> 本文不是旧实施提示词的续写，也不是已经批准的技术方案。
> 它只负责把旧会话的有效事实、冻结文档和新增需求交给一个全新的会话。
> 新会话第一阶段应重新建模并产出新版开发提示词；在用户审阅新版提示词前，不开始产品代码、数据库 migration 或生产操作。

## 1. 冻结点与完整性校验

冻结时间：`2026-08-24T11:29:11+08:00`（Asia/Shanghai）。

旧实施提示词：

```text
/home/xln/geo-system/platform-v2/docs/audits/COLLECTION_EXECUTION_GRANT_IMPLEMENTATION_PROMPT_20260821.md
```

冻结指纹：

```text
SHA-256: 6ea3bdec14175dd3b58a122840754feaeba0cba4777eadc9520bbced3001de1b
Git blob: 3fb6142fc28c331837128c2815fb0826c83e4415
Lines:    5847
Bytes:    705881
Git state: untracked
mtime:    2026-08-22 02:04:17.970413001 +0800
```

仓库冻结现场：

```text
Repository: /home/xln/geo-system/platform-v2
HEAD:       0d06c3b8808aee26c6395b33d1db4f3cf579f95d
Dirty summary: 17 modified/other, 11 untracked, 28 porcelain entries
```

新会话必须先重新计算旧提示词的 SHA-256、行数和字节数。任一值不一致时停止引用并向用户报告，不得把不同内容冒充本冻结版本。旧提示词包含中断前的增量编辑，尚未经过最终收敛或用户批准；它是审计材料和设计候选集合，不是实现真源。

## 2. 新增且会改变总体模型的需求

同一采集系统以后需要支持三种采集模态：

1. `API`；
2. `WEB_APPLICATION`，即网页端应用；
3. `MOBILE_APP`，即移动端 App。

每种模态的采集结果可能需要独立统计和横向比较，以展示同一平台、问题、模式在不同交互入口上的差异。

这不是在现有 `mode` 字段后再塞一个字符串。新会话必须首先判断“模态”在产品语义上究竟是：

- 正式采样维度，会扩展目标 cell、完成分母和主批次成员；
- 执行路由维度，只描述如何取得同一个逻辑样本；
- 或者按平台/研究任务配置的混合模型。

在产品语义和现有数据得到证据前，不得预选其中一个答案。尤其不能把 `normal/deep_think` 等模型交互模式与 `API/WEB_APPLICATION/MOBILE_APP` 采集模态混为一个枚举。

## 3. 新版提示词必须重新回答的决策

新版提示词至少要对以下问题给出明确、可验收且有迁移路径的答案：

### 3.1 业务身份与采样合同

- 逻辑工作唯一键、formal leg、sampling cell、run item、primary slot 和 campaign target 是否包含 modality；
- 同一题是否要求三模态全部采集，还是允许按平台、地区、项目和时间段选择子集；
- 三模态结果何时算三个独立观察值，何时只是同一观察值的三个执行候选；
- 缺失、不可用、未配置、不支持和失败必须作为不同状态统计，不能以 `0` 或静默缺行代替；
- 主批次身份必须来自冻结前的权威意图/slot，不能由先创建、先完成、样本最多或锁赢家推断。

### 3.2 资源、身份和授权

- API 需要密钥、租户或调用额度；网页端需要正式账号、会话和浏览器所有权；移动端需要正式账号、设备/模拟器、App 安装与会话所有权；
- 三类资源应共享一个带类型的 execution-grant 协议，还是使用不同 grant subtype，并如何保持统一审计和 fail-closed；
- quota 是按外部账号共享，按 modality 独立，还是两者同时存在；所有适用 scope 必须原子预占，不能只读余额后再发送；
- 浏览器 fencing 只适用于网页资源。移动端需要定义等价的 device/session fencing；API 需要定义 request credential/tenant 的并发与幂等边界；
- 豆包及其他平台缺少已验证正式绑定时，scheduler 和 Activity 必须直接阻断，不得回退到 env、历史 CDP、隐式设备或其他旧路径。

### 3.3 外部副作用与结果证据

- 为 API request、网页 submit、移动端 tap/send 分别定义“尚未发送、发送中、已确认发送、结果未知、已确认未发送”的证据；
- 任何发送结果未知都不得由 Temporal Activity retry 自动重发；
- 每条结果必须冻结 platform、interaction mode、collection modality、外部账号/租户、资源实例、App/Web/API 版本、submission operation、capture/provenance 和内容 hash；
- 比较三模态时必须记录会影响可比性的 prompt、模型、区域、登录态、功能开关、客户端版本、时间窗口和采集策略。

### 3.4 调度、Temporal 与历史兼容

- 快速/专家等 execution mode 与三模态组合后，schedule lineage、partition、run-origin intent、primary slot 和 top-up 如何分段；
- 新字段进入 Workflow/Activity payload 时，必须通过版本分支、typed default、patch marker 或新 workflow type 保持既有 Temporal history 可确定回放；
- 不得原地扩大已经 active 的 chain generation 的成员集合；需要明确 cutover、continue-as-new 和旧历史 replay corpus；
- 同地域可以让不同平台、账号和独立资源并行。region/relay 行只表达健康投影和单调 probe generation，不得成为地域级全局互斥锁。

### 3.5 统计、查询和界面

- 原始事实表应保留 modality，不允许只在 UI 临时推断；
- 分母同时展示“配置目标数、可采目标数、已尝试数、已确认观察数”，避免不可用模态让完成率失真；
- 支持同平台跨模态、同模态跨平台以及同题配对比较，并明确配对样本和非配对样本；
- 历史无 modality 的记录必须有显式 `legacy/unknown` 语义或可证明的 backfill，不能默认伪装成网页端；
- API、报表、导出、缓存键和权限过滤都必须覆盖该维度。

## 4. 从旧会话保留、但必须重新验证的核心问题

以下问题仍需保留，不代表旧提示词中的每个解法已获批准：

- 账号额度必须在外部发送前原子预占，并对所有日/周/年及 mode/modality scope 守恒；
- 浏览器及未来移动设备必须有 fencing，失去所有权后旧 holder 不能继续产生副作用；
- relay/region probe 需要 observation generation、stale no-op 和可审计 override，不能让乱序旧结果覆盖新事实；
- Temporal retry 与外部副作用之间必须采用保守的 at-most-once 状态机；
- 任务结果落库、额度结算、采样 observation 和审计 receipt 不能依赖跨事务 best-effort 回调；
- 快速/专家混合批次必须按模式保持可解释的身份和统计，同时保持旧 Temporal history replay；
- 正式账号/credential/session binding 缺失时 fail-closed；
- 全局暂停、管理端释放、换绑和异常回收需要线性化语义与一致锁顺序；
- 当前工作树有其他会话改动，必须逐文件、逐块合并，禁止覆盖或回退无关修改。

## 5. 新会话的工作边界

1. 不继承本旧聊天的完整对话上下文；只读取本文作为入口。
2. 第一阶段只读核查当前代码、schema、Temporal payload/history fixture、统计口径和 UI/API；随后产出“现状证据 + 决策矩阵 + 新版开发提示词”。
3. 旧提示词不能整篇注入模型上下文。先读取目录和相关章节；每次引用都记录章节与冻结 SHA-256。
4. 如使用子智能体，使用无历史或最小历史 fork，只传具体问题、精确文件路径、冻结 hash、允许读取的章节和固定输出格式；禁止嵌套 full-history fork。
5. 子智能体只提交短结论和证据索引，不回传大段日志、完整 diff 或全文；主会话用文件作为稳定真源。
6. 不启动、停止或恢复真实 worker、scheduler、cron、浏览器、移动设备或真实平台发送。旧提示词要求采集保持停止；新会话先只读核验实际状态，未经用户明确授权不得改变。
7. 在用户审阅并接受新版提示词前，不实施 schema、业务代码或 migration。

## 6. 建议的新会话首轮产出

新会话先交付以下四项，等待用户审阅：

1. 当前系统中 `mode/platform/account/browser/region/run/cell` 的真实数据流图和证据路径；
2. “modality 是采样维度、执行路由或混合配置”的决策矩阵及推荐结论；
3. 三模态对 quota、typed resource fencing、主批次、Temporal replay 和统计分母的影响清单；
4. 新版开发提示词的章节目录、预计篇幅和拆分策略。

新版提示词应拆成一个短主入口和若干按主题读取的附件，避免再次形成一个必须整篇进入上下文的超大单文档。

## 7. 可直接复制到新会话的启动提示词

```text
请先完整读取：
/home/xln/geo-system/platform-v2/docs/audits/COLLECTION_THREE_MODALITY_NEW_SESSION_HANDOFF_20260824.md

这是一次新的采集架构建模会话。不要继承或复述旧聊天，不要立即修改产品代码。先按交接文档校验冻结旧提示词的 SHA-256/行数/字节数；若不匹配立即停止并报告。

目标是重写采集端开发提示词，使系统正式支持 API、网页端应用、移动端 App 三种采集模态，并可按模态独立统计和横向比较。先用只读证据判断 modality 应是正式采样维度、执行路由维度还是按配置混合；同时重新审视额度预占、typed resource fencing、外部副作用幂等、主批次身份、豆包正式绑定、region/relay 并发、Temporal 历史回放和统计分母。

首轮只交付：现状证据、决策矩阵、影响清单、新版提示词目录与拆分策略，等待我确认。不得启动/恢复真实采集，不得写 migration 或业务代码，不得覆盖工作树现有改动。

如果分派子智能体，使用 fork_turns=none 或最小历史，只传本交接文件、冻结 hash、具体只读问题与输出上限；禁止嵌套 full-history fork，禁止把整个旧提示词或完整日志复制进消息。
```
