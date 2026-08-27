# ADR-0017：证据驱动知识演进中间件的第一阶段部署边界

- 状态：Accepted
- 日期：2026-08-27
- 决策者：GEO Platform 工程与知识治理责任人

## 背景

GEO 请求、AI 回答抽取、人工纠错和外部知识源会持续产生新的实体、关系与分类。一次请求中的模型判断可以帮助当前业务，但它不能未经证据和审核就成为跨项目永久事实。此前的静态品牌规则同时承担解析、审核和发布，因而无法清楚表达不确定性、血缘、版本或冲突。

这一问题不是品牌专用问题。稳定的通用过程是：观察、候选聚合、提案、证据、裁决、变更集、不可变发布、分发和运行反馈。

## 决策

第一阶段在 `platform-v2` 仓库内实现模块化中间件，并随现有 FastAPI 进程部署。中间件使用隔离的 `knowledge` PostgreSQL schema、独立的 `/api/v2/knowledge/v1` 契约、独立的 `domain/knowledge_evolution` 核心包和独立的不可变 artifact 目录。

这个决定是物理上的同仓起步，不是逻辑上的 GEO 私有实现。通用核心不能导入 GEO API、品牌包或 SiliconIndex。GEO、品牌领域包和 SiliconIndex adapter 都是核心的消费者或插件。

系统分成四个平面：

1. 运行时推理平面组合指定 release、确定性解析、模型和受控工具。
2. 观察与治理平面保存候选、证据、裁决、变更集和审计历史。
3. 发布与同步平面生成内容寻址 release，并完成激活、回滚和 connector 对账。
4. 领域策略平面定义本体、解析、提示词、证据政策、质量门和投影。

请求热路径只读取本机激活的知识 release。SiliconIndex 只能由同步任务访问，不属于请求关键路径。中间件不可用时，品牌消费者继续读取经过校验的 last-known-good 投影，并披露 degraded 状态。

## 请求时模型边界

系统不禁止请求时模型。策略为 `deterministic_only`、`llm_assisted`、`llm_required` 和 `exploratory`。

模型结果与确定性结果分开返回。调用方只有同时启用外部模型、选择允许模型的策略并设置 `adopt_model_inferred=true` 时，模型结果才能影响当次请求。采用后的状态仍是 `model_inferred`，作用域仍是 `request`。它不会修改主数据、变更集或 release。

`llm_assisted` 只允许模型处理确定性结果中的未决项，不能用模型覆盖已发布判断。`confidential/restricted` 请求即使误传 `allow_external_model=true` 也会被外部 gateway 拒绝。gateway 对 408、429、5xx 和传输错误做有界重试并支持备用端点；输出、工具和异常都经过结构与泄露边界校验。费用预算存在但 provider 未返回可核验费用时，结果标为 `cost_budget_unverifiable`，不能被采用。

缓存键包含租户、领域、任务、输入、必要上下文、release hash、policy、prompt、model 和 tool 版本。缓存命中保留模型血缘，但新增 token、费用和 provider latency 记为零。

cache、observation 和 inference trace 写入属于可降级的运行反馈边界。数据库实现用 savepoint 隔离这些写入；失败时当前有效判断仍返回，并披露稳定 degradation code。候选聚合、证据和发布仍由持久治理事务完成，不能因为反馈降级而伪造已持久化回执。

## 治理不变量

- 正式知识必须有 observation 到 release 的可追溯链。
- observation、evidence、adjudication、release activation 和 audit event 是 append-only 历史。
- 提案者不能裁决自己的提案。
- 变更集创建者不能批准自己的变更集。
- 发布者不能是变更集创建者或批准者。
- 公开且已审核的品牌对象必须通过领域质量门并带公开证据。
- change set 必须精确列出与已批准 proposal 绑定的全部 evidence public ID；反对证据不能从发布血缘中省略。
- authoritative/primary 反对证据未解决时不能批准；终态 proposal 不能被后续 evidence 静默重开或覆盖裁决。
- 三方合并出现同字段双写时必须产生显式冲突，不能 last-write-wins。
- 租户数据由 RLS 隔离；公共导出拒绝客户、项目、回答、上下文和凭证字段。

## 备选方案

### 立即拆成独立仓库和独立服务

该方案有最清楚的部署边界，但会在第一条纵切尚未稳定时增加服务发现、鉴权、网络容错、独立 CI 和双仓发布成本。当前选择保留可抽取边界，等第二个生产领域或第二个外部调用方出现后再拆分。

### 继续维护品牌专用 CRUD 和 JSON

该方案短期简单，但会复制状态机、审计、模型治理、发布和同步逻辑。它也不能证明非品牌复用，因此被否决。

### 让 SiliconIndex 成为请求时远端事实源

该方案会把 Render 可用性和网络时延引入每个项目请求，并使本地审核无法自治，因此被否决。

## 后果

优点是请求策略、知识状态和发布权限被明确分离。品牌是完整生产纵切，`source/type-fixture` 通过同一核心和发布流程证明通用性。现有 API 服务仍可复用认证、租户和部署设施。

代价是第一阶段仍与平台共享进程和数据库实例。独立扩缩容、跨系统服务级鉴权和 broker push event 留到提取阶段。当前提供稳定 HTTP SDK、OpenAPI 和 pull-based `knowledge-event-v1`，避免消费者直接依赖内部表。

## 提取触发条件

出现以下任一情况时重新评审物理拆分：第二个生产领域需要独立发布节奏；第二个系统需要独立 SLO；模型负载需要独立扩缩容；平台数据库维护窗口无法满足知识服务可用性；或跨组织权限要求独立安全域。
