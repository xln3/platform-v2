# Codex `Invalid prompt` 与长会话/多智能体失控事件取证

- 取证日期：2026-08-24（Asia/Shanghai）
- 取证边界：只读分析本机 Codex 元数据、脱敏 rollout 结构、token 计数和官方公开文档；不公开 system/developer 指令、私密 reasoning 或加密的智能体消息正文。
- 结论状态：客户端行为已确认；具体 classifier 命中规则外部不可见，因此“发生了哪一种实时 safeguard 介入”仍是高可信推断，不伪装成已证实的模型内部根因。

## 1. 结论先行

本事件不是“那句 `当前会话已恢复，我继续完成原任务。` 本身被判违规”，也不是“单次请求塞入 1459 万 token”，更没有证据表明 remote compaction 失败。

已确认的真实链路是：

1. `当前会话已恢复，我继续完成原任务。` 位于已返回给用户的 assistant 输出末尾；它不是随后新增的一条 user、system 或 developer prompt。
2. assistant 输出之后，同一个 turn 因仍在继续工作而发起下一次 sampling；rollout 中没有夹入一条新的可编辑自然语言 prompt。
3. 服务端已经流出 reasoning item，之后才返回 `Invalid prompt`。OpenAI 官方说明 GPT-5.6 的实时 cyber/biology misuse classifier 会在生成过程中运行，并可能在中途暂停或阻断合法的双用途防御工作。这与本地时序一致，但本地日志无法看到具体 classifier 类别。
4. Codex 0.149.0/0.149.1 把该错误降格保存为 `codexErrorInfo="other"`、`additionalDetails=null`，UI 只显示泛化的 `Invalid prompt`，丢失了“中途 safeguard 介入”与“请求格式/输入本身无效”之间的重要区别。
5. 失败子智能体的错误被作为 459 字符的 model-visible plaintext `agent_message` 两次注入父智能体；父智能体在第一次失败后 26 秒又向同一子线程发出 follow-up，得到同样错误。
6. 根 turn 已于 02:04:27 失败，但后代仍继续运行：02:06:19 重试旧子线程，02:08:41 又启动新的二级子智能体，父智能体直到 02:30:26 才结束。根失败没有形成后代取消/隔离边界。

因此需要拆成三条修复线：

- 模型/平台：降低合法防御性系统设计的实时 safeguard 误介入，并提供可提交给支持团队的服务端 trace；
- Codex 客户端：保留结构化 safeguard 错误、partial-stream 状态和 request/response ID，不再误称为普通“prompt 无效”；
- 多智能体编排：同一错误上下文禁止盲重试，根 turn 失败时取消或隔离后代，错误通过控制面传播而不是作为普通对话内容反复回灌。

## 2. 取证对象

### 2.1 版本与环境

```text
Failure-time child CLI: 0.149.0
Current/latest npm CLI: 0.149.1
Current model:          gpt-5.6-sol
Reasoning effort:       max
OS:                     Linux 5.15.0-125-generic x86_64
Node.js:                v24.6.0
```

2026-08-24 两次根线程现象来自同一 Codex 进程 UUID；该进程后续自身记录 `client_version="0.149.1"`。因此升级到 0.149.1 不能被当作本问题已经消失的证明。

### 2.2 线程树

```text
root
01a0231e-0252-7be3-865c-f8f3039df74c
└── integration_review
    01a0235c-2357-7c22-9cf0-db406da980bb
    ├── temporal_migration_audit
    │   01a02433-ebb0-74c3-842a-7e1356a7c88d
    └── ddl_integrity_audit
        01a02582-c0b4-7c11-bbf9-2c0dd23daac1
```

目标子线程是深度 2 的 `temporal_migration_audit`。其持久化 rollout 为 25,865,952 字节，投影包含 488 个 reasoning item、279 个命令执行、65 个 subagent activity、54 个 agent wait tool call、43 个 assistant message 和 6 个可见 context compaction item。

该子线程还接收了 31 条 model-visible agent message，其中 27 条来自直接父线程、4 条来自根线程。消息正文使用加密内容保存，不能从本地记录可靠恢复；任何声称知道这些 follow-up 的逐字正文都属于编造。

## 3. Prompt 来源澄清

### 3.1 用户看到的那句话不是隐藏 prompt

2026-08-24 11:05:32 开始的根 turn 中：

1. turn 起始只有一条 204 字符的用户消息；
2. assistant 先输出说明和官方文档核查结果；
3. 第二条 assistant 输出的末尾是 `当前会话已恢复，我继续完成原任务。`；
4. 随后只有内部 reasoning continuation；
5. 11:07:15 返回 `Invalid prompt`。

第 3 项是 assistant 已显示输出，第 4 项是同一 Responses turn 的继续生成。两者之间没有新增 user/system/developer message。因此不存在一条包含该句、可由用户编辑的“秘密续跑提示词”。下一次请求实际重放的是 Codex 组装的当前 conversation state、compaction item、工具结果和 phase 信息。

### 3.2 子智能体 follow-up 的实际可见度

目标子线程的触发消息在 rollout 中由两块组成：

- 125/126 字符的路由 envelope；
- opaque encrypted content。

与两次失败最接近的父→子 follow-up 分别是 460 和 312 字符的密文。它们没有可用的本地解密材料，不能给出逐字正文。可以确定的是：第二条 follow-up 在第一次错误后 26 秒发送，使用同一旧线程和同一历史状态，9.6 秒后再次得到同样错误。

## 4. Token 与 compaction 证据

### 4.1 `14,594,218 input_tokens` 是整 turn 累计量

失败子 turn `01a02564-e570-7870-a57f-31fe0a280a1a` 的 thread cumulative input 从 `33,970,631` 增至 `48,564,849`：

```text
48,564,849 - 33,970,631 = 14,594,218
```

这与 turn 结束 telemetry 完全相等，证明 1459 万是 105 次成功 sampling 加最后失败请求在该 turn 内的累计 input，并非任一单次模型请求的上下文长度。缓存输入累计为 14,118,656，也不等于一次请求大小。

### 4.2 两次 compaction 均成功

失败 turn 的单次输入分段如下：

| 分段                        | 成功 sampling 数 | 单次 input 范围 | 结束状态                |
| --------------------------- | ---------------: | --------------: | ----------------------- |
| turn 开始→第一次 compaction |               41 |  71,182–219,559 | 生成 window 11          |
| 第一次→第二次 compaction    |               54 |  21,325–231,622 | 生成 window 12          |
| 第二次 compaction→错误      |               10 |   21,174–68,209 | 之后中途 safeguard 错误 |

两次压缩的直接变化：

```text
219,559 input -> 21,325 input
231,622 input -> 21,174 input
```

在完整故障时间窗内，本地日志中 `Error running remote compact task`、`failed to compact` 和 `remote compact task` 的命中数均为 0。`incremental request failed, incompatible request length` 在多个无关线程也出现，并且目标线程紧接着生成了新 compaction window；它是 WebSocket 增量复用不兼容/回退信号，不是本事件的压缩失败证据。

### 4.3 上下文长度不是充分原因

对照如下：

| 场景                                   | 最近成功单次 input | 结果                                |
| -------------------------------------- | -----------------: | ----------------------------------- |
| 2026-08-22 根线程                      |            117,611 | 下一 sampling 中途 `Invalid prompt` |
| 2026-08-22 深度 2 子线程，第二次压缩后 |             68,209 | 下一 sampling 中途 `Invalid prompt` |
| 2026-08-24 根线程第一次恢复            |            119,774 | 中途 `Invalid prompt`               |
| 2026-08-24 根线程解释报错              |            125,486 | 中途 `Invalid prompt`               |
| 2026-08-24 同一根线程下一 turn         |            164,727 | 正常完成                            |

同一根线程在更大的 164,727-token 输入下成功，排除了“达到固定 context limit 就报 Invalid prompt”这一解释。长度仍会增加上下文污染和误介入风险，但不能单独解释该错误。

## 5. 中途阻断证据

四个可定位的失败 turn 都先持久化了新的 reasoning item，再收到 Responses `error` event；它们不是在任何输出生成前就被格式校验拒绝。服务端错误经本地链路变成：

```text
unhandled responses event: "error"
Turn error: Invalid prompt: ... potentially violating our usage policy ...
```

最终 SQLite 只保存：

```json
{
  "codexErrorInfo": "other",
  "additionalDetails": null,
  "message": "Invalid prompt: ..."
}
```

OpenAI 官方 GPT-5.6 文档明确说明实时 misuse classifier 在模型输出生成过程中运行，可能中途暂停，并偶尔介入合法双用途工作：

- https://developers.openai.com/api/docs/guides/latest-model#safeguards

这与本事件强一致，但服务端没有把具体 classifier 结果或可关联 request ID暴露给本地记录，所以仍不能断言命中了 cyber 还是其他内部类别。

## 6. 多智能体传播与生命周期缺陷

### 6.1 错误作为普通模型输入传播

第一次子线程失败后，父 rollout 在 02:05:59 收到一条 459 字符 plaintext agent message，包含 `Invalid prompt`；第二次失败后 02:06:35 又收到完全相同 hash 的 459 字符消息。

这意味着运行时错误不仅是控制面状态，也会进入父模型上下文。它会：

- 污染后续分类和推理；
- 诱发模型把错误当作需要“换种说法继续”的普通任务反馈；
- 在父/根继续传播时重复占用上下文；
- 隐藏“相同 context hash 已经失败”的重试事实。

### 6.2 根 turn 失败后后代没有终止

关键时序：

```text
02:04:27  root turn failed: Invalid prompt
02:05:53  depth-2 child first failure
02:06:19  parent sent follow-up to the same failed child
02:06:28  child failed identically again
02:08:41  parent started another depth-2 child
02:15:09  parent logged its own Invalid prompt
02:30:26  parent turn finally reached failed terminal state
```

根 turn 失败后至少 25 分 59 秒仍有后代活动，并且还产生了新的 child。该行为既浪费 token，也可能让用户误以为工作已经停止，同时后台仍在读写工作区。

## 7. 最小对照实验

### 7.1 状态句单独测试

在 Codex 0.149.1、`gpt-5.6-sol`、全新 ephemeral session、无旧历史的条件下，仅把该状态句作为普通待核验文本，请模型返回 `EXACT_OK`：

```text
Result: EXACT_OK
Input tokens: 14,216
Exit code: 0
```

因此该句自身不是稳定触发器。

### 7.2 新会话交接入口测试

全新 ephemeral/read-only session 只读取三模态短交接文件并校验冻结旧文档，不读取旧文档正文：

```text
Result: HANDOFF_OK
Old prompt SHA-256: 6ea3bdec14175dd3b58a122840754feaeba0cba4777eadc9520bbced3001de1b
Lines: 5847
Bytes: 705881
Modalities: API, WEB_APPLICATION, MOBILE_APP
Exit code: 0
```

这证明新交接入口可以在无旧聊天历史的情况下恢复必要事实。它不证明后续完整架构设计一定不会遇到 safeguard，但已经把“必须继承膨胀对话”从依赖中移除。

## 8. 根因分级

### 已确认

- `Invalid prompt` 在 partial reasoning stream 之后发生；
- 单次输入未达到 258,400 context window；
- 两次 remote compaction 成功；
- 1459 万是 turn 累计输入；
- 状态句在干净会话中通过；
- Codex 将错误归为 `other` 且丢失 additional details；
- 相同错误被明文回灌父上下文并立即重试；
- 根 turn 失败没有取消/隔离后代。

### 高可信推断

- 这是 GPT-5.6 实时 safeguard 对双用途防御性工程上下文的偶发介入，而不是 prompt 语法错误；
- 超长、多轮、包含大量失败路径和安全边界的上下文提高了 classifier 看到脱离产品语境片段的概率；
- compacted opaque state 可能保留了任务相关的高密度安全语义，但本地无法检查其内容，因此不能把 compaction summary 本身定为根因。

### 尚未确认

- 具体 classifier、命中特征和阈值；
- 服务端原始 error type/code/param/request ID；
- 加密 follow-up 的逐字正文；
- 0.149.1 相对 0.149.0 是否修改过部分错误处理，但已确认它没有完全解决现象；
- classifier 是否对同一完整 context 可 100% 稳定复现。

## 9. 当前本地协作规约

在上游修复前，本项目采用以下 fail-safe 协作方式；它们用于减少上下文污染和重复故障，不用于绕过安全检查：

1. 旧采集会话只做取证，不再继续设计正文。
2. 新采集会话只以带 SHA-256 的短交接文件启动；旧 705,881-byte 提示词只按章节选择性读取。
3. 子智能体使用无历史或最小历史 fork，输入只含具体问题、精确路径、冻结 hash、只读边界和输出上限。
4. 禁止嵌套 full-history fork；默认深度不超过 1，确需深度 2 时先由根会话批准。
5. 子智能体返回短结论与证据索引，不回传完整命令输出、diff 或日志。
6. `Invalid prompt`、policy/safeguard 类错误第一次出现即停止同 context 自动 retry；先冻结 thread/turn/time/version/token/compaction 元数据，再由用户决定新会话或脱敏上报。
7. 根 turn 失败或被中断后显式枚举并停止所有仍活动的后代；不得继续 spawn。
8. 设计事实落盘到内容寻址文档，聊天只保存决策和下一步，不把聊天历史当唯一状态库。
9. 对读多写少的独立审计使用窄子智能体；并行写同一文件必须由单一主会话合并。

OpenAI 官方 Codex 文档也建议把 noisy intermediate output 移出主线程，并让子智能体返回摘要而非原始中间输出：

- https://learn.chatgpt.com/docs/agent-configuration/subagents

## 10. 相关交接文件

```text
New-session handoff:
/home/xln/geo-system/platform-v2/docs/audits/COLLECTION_THREE_MODALITY_NEW_SESSION_HANDOFF_20260824.md
SHA-256: 85a8691eac6d0be86608c20d46a800815b3afd0e1562544f2346bd3b4249b1b6

Frozen old prompt:
legacy-prompt.md
SHA-256: 6ea3bdec14175dd3b58a122840754feaeba0cba4777eadc9520bbced3001de1b
```
