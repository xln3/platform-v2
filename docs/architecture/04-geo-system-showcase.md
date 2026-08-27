# GEO 系统全景展示

> GEO 系统持续测量品牌在生成式 AI 中的可见性、信源引用和内容传播效果。系统把客户授权范围转化为可执行任务，从模型 API、消费级 AI 真实网页和真实 App 三种表面采集回答，再生成可追溯证据、五项专业分析结果和正式交付物。

> **版本快照**
>
> - 记录时间：2026-08-25T13:07:27+08:00（Asia/Shanghai）。
> - 主实现仓库：<code>platform-v2</code>，分支 <code>master</code>，提交 <code>c6f53153f4a9ac44490850c74ef7124b05ac2916</code>。
> - 对应提交时间：2026-08-25T12:13:37+08:00。
> - 开发态工作树：包含 94 个 Git 状态项。本文对应提交锚点及其尚未提交的开发态内容。
> - 已跟踪改动指纹：SHA-256 <code>0570ace0e9bdd84977fc85abfc00e43659b26f7a1e5ea36beecbb69c4d57deba</code>。

## 展示口径

- **规模数据**采用 2026-08-24 生产审计快照。
- **当前开发结构**采用 2026-08-25 静态盘点。
- **状态标记**只描述实现成熟度，主要使用【已上线】、【已实跑】、【部分完成】、【分阶段建设】、【待接入】和【待实现】。
- **颜色含义**：绿色表示客户，橙色表示运营/分析/审核人员，深紫表示 Agent Skill，浅紫表示产品内运行时 AI，蓝色表示确定性软件，灰色表示外部系统，青色表示事实、证据和交付物，藏蓝表示架构分层标题。
- **模块口径**：生产审计识别 24 个业务功能模块，当前开发工作树盘点到 25 个 API 模块目录；下文按展示关系归并为 16 个能力模块，并保留全部实现范围。

## 系统规模速览

| 规模维度                          |               审计快照 |
| --------------------------------- | ---------------------: |
| 角色化 Web 工作台                 |                   5 个 |
| 业务模块                          |                  24 个 |
| API 路径 / 操作 / Schema          |        262 / 320 / 395 |
| 生产业务表 / 数据域               |                173 / 9 |
| Workflow / Activity / Worker 入口 |             8 / 33 / 6 |
| 一方源文件 / 源代码               |       994 / 356,550 行 |
| 测试文件 / 测试代码               |       318 / 109,113 行 |
| 采集任务 / 回答与分析             |          3,104 / 1,492 |
| 引用事实 / 证据资产               |        13,007 / 21,781 |
| 规范 URL / Occurrence / 页面快照  | 4,853 / 11,010 / 3,141 |

---

## 核心图一｜开发者视角：部署、安全域与运行组件（对应 02 #2）

这组图展示系统的运行边界。分层总览适合快速定位系统结构；关系展开版把工作台、边缘入口、授权步骤、业务域、事件、Worker、采集资源、外部表面和存储分别画成节点，并用带语义的连线展示组件内关系与跨层关系。

### 分层总览

```mermaid
block-beta
  columns 12

  H1["访问角色"]:2
  block:roles:10
    columns 3
    C["客户 / 受邀填报人"] O["运营 / 管理员"] A["分析 / 审核人员"]
  end

  H2["入口与安全域"]:2
  block:access:10
    columns 3
    CU["客户安全域<br/>customer-web・intake-form<br/>customer edge・intake edge"] MG["管理安全域<br/>operations-web・intelligence-web<br/>report-studio・management edge"] AU["辅助入口<br/>OTP・同会话接管・客户终端扩展<br/>auxiliary edge"]
  end

  H3["边缘安全"]:2
  EDGE["TLS・路由・限流・身份头清洗"]:10

  H4["模块化 API"]:2
  API["FastAPI 业务后端｜身份/租户/项目・报价/配置/准入・采集/证据/分析・五项服务・报告/导出/SOP"]:10

  H5["耐久执行"]:2
  block:exec:10
    columns 3
    ORCH["Temporal・Scheduler・Outbox<br/>状态机・重试・补偿・可靠事件"] WORK["独立 Worker<br/>Collection・Source・Analysis<br/>S02/Report・Scheduler・Outbox"] CONTROL["三表面采集控制<br/>任务身份・grant・quota<br/>lease・fencing"]
  end

  H6["外部 AI 测量"]:2
  block:surface:10
    columns 3
    block:sapi
      columns 1
      PA["provider_api Adapter<br/>【待接入】"]
      PW["外部模型 API"]
    end
    block:sweb
      columns 1
      WB["consumer_web Adapter<br/>真实浏览器【五平台已实跑】"]
      WW["五平台消费级 AI 真实网页"]
    end
    block:sapp
      columns 1
      MA["consumer_app Adapter<br/>真实设备 / App 会话【待实现】"]
      MW["消费级 AI 真实 App"]
    end
  end

  H7["数据与证据"]:2
  block:data:10
    columns 4
    PG["PostgreSQL<br/>权威业务事实"] OBJ["MinIO / S3 / CAS<br/>不可变证据"] CH["ClickHouse<br/>可重建分析投影"] STATE["Redis・Temporal PostgreSQL・Vault<br/>临时态・Workflow 历史・密钥"]
  end

  H8["运行保障"]:2
  OBS["OTel・Prometheus・Loki・Grafana｜告警・备份・发布与回滚"]:10

  NOTE["层间连接｜角色按职责进入安全域；API 提交耐久任务；Worker 调度采集资源；采集结果固化为事实、证据、运行状态与遥测。"]:12
  ORCH --> WORK
  WORK --> CONTROL
  PA --> PW
  WB --> WW
  MA --> MW

  classDef header fill:#0f3d5e,stroke:#0f3d5e,color:#ffffff,stroke-width:2px;
  classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
  classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
  classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
  classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
  classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;

  class H1,H2,H3,H4,H5,H6,H7,H8 header
  class C customer
  class O,A human
  class CU,MG,AU,EDGE,API,ORCH,WORK,CONTROL,PA,WB,MA,OBS software
  class PW,WW,MW external
  class PG,OBJ,CH,STATE fact

  style roles fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
  style access fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
  style exec fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
  style surface fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
  style data fill:#ffffff,stroke:#cbd5e1,stroke-width:1px
  style NOTE fill:#ffffff,stroke:#94a3b8,color:#334155,stroke-width:1px
```

### 关系展开版｜组件、调用、事件与数据流

关系展开版由 1A—1E 五个分面和一个可展开的全连接索引组成。五个分面依次放大入口与安全、业务域、任务执行、AI 与人工批准、数据与运行保障；同名节点始终保持同一含义，1F 再把关键跨层关系合并到一个画布。流程图中的实线表示调用、控制或任务路由，虚线表示依赖、异步事件、状态约束或人工接管，粗线表示需要持久化的事实、证据、命令或投影；时序图中的实线和虚线分别表示请求与返回。每个节点只表达一个组件或一项明确职责，节点内换行只补充实现名、责任或当前状态。

#### 1A｜角色如何穿过安全域到达 FastAPI

```mermaid
flowchart TB
    subgraph ACTORS["访问角色"]
        direction TB
        CUSTOMER["客户"]
        INVITEE["受邀填报人"]
        OPERATOR["运营人员"]
        ANALYST["信源分析人员"]
        REVIEWER["报告审核人员"]
    end

    subgraph CLIENTS["产品入口"]
        direction TB
        CUSTOMER_WEB["customer-web"]
        INTAKE_FORM["intake-form"]
        OPERATIONS_WEB["operations-web"]
        INTELLIGENCE_WEB["intelligence-web"]
        REPORT_STUDIO["report-studio"]
        TERMINAL_EXT["customer-terminal-extension<br/>【部分完成】"]
    end

    subgraph EDGES["相互隔离的边缘入口"]
        direction TB
        CUSTOMER_EDGE["customer edge"]
        INTAKE_EDGE["intake edge"]
        MANAGEMENT_EDGE["management edge"]
        AUXILIARY_EDGE["auxiliary edge"]
    end

    subgraph SECURITY["每类入口都执行的安全步骤"]
        direction TB
        TLS["TLS 终止"]
        ROUTING["域名与路径路由"]
        RATE_LIMIT["请求限流"]
        HEADER_CLEAN["身份头清洗"]
        SESSION_AUTH["会话鉴权"]
        OTP_TICKET["一次性 OTP 票据校验"]
        SIGNED_TASK["终端签名任务校验"]
        RBAC["角色授权"]
        CAPABILITY["能力授权"]
        TENANT_SCOPE["租户范围校验"]
        PROJECT_SCOPE["项目范围校验"]
    end

    API_APP["FastAPI 应用边界"]

    CUSTOMER -->|查看并确认| CUSTOMER_WEB
    CUSTOMER -->|完成本机挑战| TERMINAL_EXT
    INVITEE -->|填写项目资料| INTAKE_FORM
    OPERATOR -->|配置并控制| OPERATIONS_WEB
    ANALYST -->|研究并复核| INTELLIGENCE_WEB
    REVIEWER -->|审核并发布| REPORT_STUDIO

    CUSTOMER_WEB --> CUSTOMER_EDGE
    INTAKE_FORM --> INTAKE_EDGE
    OPERATIONS_WEB --> MANAGEMENT_EDGE
    INTELLIGENCE_WEB --> MANAGEMENT_EDGE
    REPORT_STUDIO --> MANAGEMENT_EDGE
    TERMINAL_EXT --> AUXILIARY_EDGE

    CUSTOMER_EDGE -->|HTTPS| TLS
    INTAKE_EDGE -->|HTTPS| TLS
    MANAGEMENT_EDGE -->|HTTPS| TLS
    AUXILIARY_EDGE -->|HTTPS| TLS
    TLS --> ROUTING --> RATE_LIMIT --> HEADER_CLEAN
    HEADER_CLEAN -->|工作台请求| SESSION_AUTH
    HEADER_CLEAN -->|OTP 接管请求| OTP_TICKET
    HEADER_CLEAN -->|终端扩展请求| SIGNED_TASK
    SESSION_AUTH --> RBAC --> CAPABILITY
    OTP_TICKET --> CAPABILITY
    SIGNED_TASK --> CAPABILITY
    CAPABILITY --> TENANT_SCOPE
    TENANT_SCOPE --> PROJECT_SCOPE
    PROJECT_SCOPE -->|注入已验证上下文| API_APP

    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    class CUSTOMER,INVITEE customer;
    class OPERATOR,ANALYST,REVIEWER human;
    class CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO,TERMINAL_EXT,CUSTOMER_EDGE,INTAKE_EDGE,MANAGEMENT_EDGE,AUXILIARY_EDGE,TLS,ROUTING,RATE_LIMIT,HEADER_CLEAN,SESSION_AUTH,OTP_TICKET,SIGNED_TASK,RBAC,CAPABILITY,TENANT_SCOPE,PROJECT_SCOPE,API_APP software;
```

##### 七个共享前端包的真实直接依赖

箭头表示当前 <code>package.json</code> 中的直接 workspace 依赖。<code>domain-types</code> 目录存在，静态盘点未发现当前应用或其他共享包直接引用它。

```mermaid
flowchart LR
    API_CLIENT["@geo/api-client"]
    AUTH["@geo/auth"]
    CHARTS["@geo/charts"]
    DESIGN["@geo/design-system"]
    DOMAIN_TYPES["@geo/domain-types<br/>【存在/未见直接消费】"]
    EVIDENCE_VIEWER["@geo/evidence-viewer"]
    WORKFLOW_UI["@geo/workflow-ui"]

    CUSTOMER_WEB["customer-web"]
    INTAKE_FORM["intake-form"]
    OPERATIONS_WEB["operations-web"]
    INTELLIGENCE_WEB["intelligence-web"]
    REPORT_STUDIO["report-studio"]

    API_CLIENT --> DESIGN
    API_CLIENT --> AUTH
    DESIGN --> AUTH

    API_CLIENT --> CUSTOMER_WEB
    AUTH --> CUSTOMER_WEB
    CHARTS --> CUSTOMER_WEB
    DESIGN --> CUSTOMER_WEB
    EVIDENCE_VIEWER --> CUSTOMER_WEB

    API_CLIENT --> INTAKE_FORM
    DESIGN --> INTAKE_FORM

    API_CLIENT --> OPERATIONS_WEB
    AUTH --> OPERATIONS_WEB
    DESIGN --> OPERATIONS_WEB
    EVIDENCE_VIEWER --> OPERATIONS_WEB

    API_CLIENT --> INTELLIGENCE_WEB
    AUTH --> INTELLIGENCE_WEB
    DESIGN --> INTELLIGENCE_WEB

    API_CLIENT --> REPORT_STUDIO
    AUTH --> REPORT_STUDIO
    DESIGN --> REPORT_STUDIO
    WORKFLOW_UI --> REPORT_STUDIO

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef target fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px,stroke-dasharray:6 4;
    class API_CLIENT,AUTH,CHARTS,DESIGN,EVIDENCE_VIEWER,WORKFLOW_UI,CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO software;
    class DOMAIN_TYPES target;
```

#### 1B｜后端业务域如何传递事实

图中的 25 个具名节点对应当前后端顶层模块目录，另有一个跨模块准入职责。路由模块由 FastAPI 挂载；<code>tenancy</code> 提供共享租户能力；<code>notifications</code> 同时包含独立部署的通知 Bot。它们通过同一后端代码库、数据库事实和事件协作。

```mermaid
flowchart TB
    subgraph PROJECT_CONTROL["项目与商业控制"]
        direction TB
        IDENTITY["identity<br/>身份"]
        TENANCY["tenancy<br/>租户"]
        INTAKE_FORM_API["intake_form<br/>邀请表单"]
        INTAKE["intake<br/>资料接收"]
        PROJECTS["projects<br/>项目事实"]
        QUOTATIONS["quotations<br/>报价"]
        VARIANTS["variants<br/>问题变体"]
        ADMISSION["配置冻结与执行准入"]
    end

    subgraph COLLECTION_EVIDENCE["采集与证据"]
        direction TB
        COLLECTION["collection<br/>采集控制"]
        DATASETS["datasets<br/>回答数据集"]
        EVIDENCE["evidence<br/>证据"]
    end

    subgraph ANALYSIS_SERVICES["分析与五项服务生产"]
        direction TB
        SOURCE_INTEL["source_intelligence<br/>信源情报"]
        INTELLIGENCE["intelligence<br/>智能任务"]
        SOURCE_ANALYSIS["source_analysis<br/>信源分析"]
        ANALYTICS["analytics<br/>指标分析"]
        BRANDRANK["brandrank<br/>推荐排名"]
        SERVICE2_CORPUS["service2_corpus<br/>服务二语料【重建中】"]
        POSTING["posting<br/>内容发布"]
        POST_ANALYSIS["post_analysis<br/>发布复测"]
    end

    subgraph DELIVERY_COLLAB["交付与协作"]
        direction TB
        REPORTS["reports<br/>服务事实与报告"]
        EXPORTS["exports<br/>交付导出"]
        CUSTOMER_SERVICES["customer_services<br/>客户服务投影"]
        CUSTOMER_DASHBOARD["customer_dashboard<br/>客户总览投影"]
        NOTIFICATIONS["notifications<br/>独立通知 Bot 与协作逻辑"]
        OTP["otp<br/>OTP 协作"]
        SOP["sop<br/>运行规程"]
    end

    IDENTITY -->|建立主体| TENANCY
    TENANCY -->|限定归属| PROJECTS
    INTAKE_FORM_API -->|提交邀请表单| INTAKE
    INTAKE -->|形成客户确认事实| PROJECTS
    PROJECTS -->|定义服务范围| QUOTATIONS
    PROJECTS -->|提供问题基线| VARIANTS
    QUOTATIONS -->|提供权益与价格约束| ADMISSION
    VARIANTS -->|提供冻结问题集合| ADMISSION
    PROJECTS -->|提供授权范围| ADMISSION
    ADMISSION -->|准入通过| COLLECTION
    COLLECTION -->|组织运行与回答| DATASETS
    DATASETS -->|关联原始回答| EVIDENCE

    EVIDENCE -->|提供引用事实| SOURCE_INTEL
    SOURCE_INTEL -->|形成研究对象| INTELLIGENCE
    INTELLIGENCE -->|提交分析版本| SOURCE_ANALYSIS
    EVIDENCE -->|提供测量事实| ANALYTICS
    EVIDENCE -->|提供回答样本| BRANDRANK
    EVIDENCE -->|提供全 U 重建输入| SERVICE2_CORPUS
    POSTING -->|提供发布事实| POST_ANALYSIS

    ANALYTICS -->|服务指标事实| REPORTS
    BRANDRANK -->|服务一事实| REPORTS
    SERVICE2_CORPUS -->|服务二迁移与生产事实| REPORTS
    SOURCE_ANALYSIS -->|服务三与服务四事实| REPORTS
    POST_ANALYSIS -->|服务五事实| REPORTS
    REPORTS -->|冻结交付版本| EXPORTS
    REPORTS -->|生成安全服务视图| CUSTOMER_SERVICES
    ANALYTICS -->|生成趋势投影| CUSTOMER_DASHBOARD
    PROJECTS -->|生成项目投影| CUSTOMER_DASHBOARD
    COLLECTION -.->|异常与状态事件| NOTIFICATIONS
    REPORTS -.->|审核与发布事件| NOTIFICATIONS
    NOTIFICATIONS -.->|发起人工接管| OTP
    SOP -.->|约束暂停与恢复| COLLECTION
    SOP -.->|约束审核与发布| REPORTS

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    class IDENTITY,TENANCY,INTAKE_FORM_API,INTAKE,PROJECTS,QUOTATIONS,VARIANTS,ADMISSION,COLLECTION,DATASETS,EVIDENCE,SOURCE_INTEL,INTELLIGENCE,SOURCE_ANALYSIS,ANALYTICS,BRANDRANK,SERVICE2_CORPUS,POSTING,POST_ANALYSIS,REPORTS,EXPORTS,CUSTOMER_SERVICES,CUSTOMER_DASHBOARD,NOTIFICATIONS,OTP,SOP software;
```

#### 1C｜业务命令如何变成三表面真实执行

##### 命令派发与表面调用

```mermaid
sequenceDiagram
    autonumber
    actor OPERATOR as 运营入口
    participant SCHEDULER as Scheduler【已实现】
    participant API as collection API
    participant COMMAND_DB as PostgreSQL 命令事务
    participant OUTBOX as Outbox Worker
    participant TEMPORAL as Temporal Server
    participant COLLECTION as Collection Worker【已实现并实跑】
    participant ADMISSION as 最终准入
    participant GRANT as Typed Grant【V2 分阶段接入】
    participant QUOTA as 原子配额
    participant LEASE as 资源 Lease
    participant FENCE as Fencing Token
    participant ADAPTER as 选定的 Surface Adapter
    participant EXTERNAL as 外部 AI 测量表面

    alt 人工运行
        OPERATOR->>API: 提交执行意图
        API->>COMMAND_DB: 同一事务写业务事实与 workflow_start_command
    else 周期运行
        SCHEDULER->>COMMAND_DB: 到期物化运行并写 workflow_start_command
    end
    COMMAND_DB-->>OUTBOX: 暴露待派发命令
    OUTBOX->>TEMPORAL: start_workflow 或 signal_workflow
    TEMPORAL->>COLLECTION: 路由登录态采集任务
    alt 现行 v1 资源路径
        COLLECTION->>ADMISSION: 校验运行、账号与浏览器上下文
        ADMISSION->>QUOTA: 检查并记录平台额度
        ADMISSION->>LEASE: 获取会话与浏览器租约
        LEASE->>FENCE: 生成单调 fencing token
    else V2 Typed Grant 路径【分阶段接入】
        COLLECTION->>ADMISSION: 校验任务与资源身份
        ADMISSION->>GRANT: 签发受限执行授权
        GRANT->>QUOTA: 原子预留调用额度
        GRANT->>LEASE: 取得独占资源租约
        LEASE->>FENCE: 生成单调 fencing token
    end
    FENCE-->>COLLECTION: 返回可审计资源权限

    alt provider_api【待接入】
        COLLECTION->>ADAPTER: 传入模型 API 凭据与结构化请求
        ADAPTER->>EXTERNAL: 调用模型提供方 API
        EXTERNAL-->>ADAPTER: 返回结构化响应与原生状态
    else consumer_web【五平台已实跑】
        COLLECTION->>ADAPTER: 传入账号、常驻浏览器与地域代理租约
        ADAPTER->>EXTERNAL: 在消费级 AI 真实网页提交问答
        EXTERNAL-->>ADAPTER: 返回页面回答与原生页面状态
        opt 登录、OTP 或 CAPTCHA
            ADAPTER-->>OPERATOR: 发起人工同会话接管
            OPERATOR-->>ADAPTER: 在同一上下文完成挑战
        end
    else consumer_app【待实现】
        COLLECTION->>ADAPTER: 传入真实设备与 App 会话
        ADAPTER->>EXTERNAL: 在消费级 AI 真实 App 提交问答
        EXTERNAL-->>ADAPTER: 返回 App 回答与原生界面状态
    end

    ADAPTER-->>COLLECTION: 返回捕获结果与证据清单
    COLLECTION->>COMMAND_DB: 同一事务保存捕获结果并提交派生事件
```

##### 捕获完成后的并行事实生产

```mermaid
sequenceDiagram
    autonumber
    participant COLLECTION as Collection Worker
    participant FACTS as PostgreSQL 权威事实
    participant OBJECTS as 对象存储证据
    participant COMMAND_DB as PostgreSQL 命令与事件表
    participant OUTBOX as Outbox Worker
    participant TEMPORAL as Temporal Server
    participant SOURCE as Source Worker
    participant ANALYSIS as Analysis Worker
    participant PUBLIC_WEB as 引用页面与客户官网
    participant REPORT_API as reports API
    participant REPORT as S02 Worker
    participant CLICKHOUSE as ClickHouse 投影

    COLLECTION->>FACTS: 同一事务写回答事实与完成状态
    COLLECTION->>OBJECTS: 写表面原生证据
    COLLECTION->>COMMAND_DB: 同一事务写 outbox_event 与派生分析命令
    COMMAND_DB-->>OUTBOX: 暴露待处理命令与事件

    par 公开信源处理
        OUTBOX->>TEMPORAL: 启动 post_collection_analysis
        TEMPORAL->>ANALYSIS: 路由分析 Workflow
        ANALYSIS->>TEMPORAL: 向 source_task_queue 调度公开页面 Activity
        TEMPORAL->>SOURCE: 路由公开页面 Activity
        SOURCE->>PUBLIC_WEB: 获取引用页面与客户官网
        SOURCE->>FACTS: 写 URL、Occurrence 与页面版本
        SOURCE->>OBJECTS: 写页面正文、截图与网络材料
    and 语义与风险分析
        OUTBOX->>TEMPORAL: 启动回答或运行级分析 Workflow
        TEMPORAL->>ANALYSIS: 路由语义分析任务
        ANALYSIS->>FACTS: 写候选事实与分析版本
    and 分析投影
        OUTBOX->>CLICKHOUSE: 按 event_id 幂等写投影
    end

    REPORT_API->>COMMAND_DB: 审核后提交证据或报告命令
    COMMAND_DB-->>OUTBOX: 暴露待派发报告命令
    OUTBOX->>TEMPORAL: 启动证据或报告 Workflow
    TEMPORAL->>REPORT: 路由证据与报告任务
    REPORT->>FACTS: 写冻结服务事实与报告版本
    REPORT->>OBJECTS: 写正式报告与证据包
```

##### consumer_web 五个平台适配器展开

五个平台适配器拥有独立的 DOM、成功判定、模式识别和错误语义；资源治理先绑定账号、常驻浏览器、地域代理和 fencing 权限，再把同一个受控执行上下文交给选中的平台适配器。

```mermaid
flowchart LR
    OPERATOR["运营人员"] -->|明确确认付费| PROXY_PURCHASE["Proxy Purchase Service"]
    PROXY_PURCHASE ==>|取得并记录| UPSTREAM_PROXY["付费地域代理"]
    UPSTREAM_PROXY -.->|配置确认后供给| PROXY_RELAY["proxy-relay@地域"]

    COLLECTION_WORKER["Collection Worker"] -->|读取任务平台与地域| BROWSER_ROUTER["Browser Router"]
    ACCOUNT["已验证消费平台账号"] -->|提供绑定版本| BROWSER_ROUTER
    BROWSER_ROUTER -->|选择平台 × 地域 × 账号实例| BROWSER["browser@实例"]
    PROXY_RELAY -->|固定本机代理入口| BROWSER
    BROWSER -->|CDP attach| CONTEXT["受控浏览器执行上下文"]
    LEASE["有效资源 Lease"] -->|限定持有者| CONTEXT
    FENCE["有效 Fencing Token"] -->|限定执行代际| CONTEXT

    CONTEXT --> DOU_ADAPTER["doubao_adapter.py"]
    CONTEXT --> DEEPSEEK_ADAPTER["deepseek_adapter.py"]
    CONTEXT --> YIYAN_ADAPTER["yiyan_adapter.py"]
    CONTEXT --> TONGYI_ADAPTER["tongyi_adapter.py"]
    CONTEXT --> YUANBAO_ADAPTER["yuanbao_adapter.py"]

    DOU_ADAPTER -->|真实网页问答| DOUBAO["豆包"]
    DEEPSEEK_ADAPTER -->|真实网页问答| DEEPSEEK["DeepSeek"]
    YIYAN_ADAPTER -->|真实网页问答| YIYAN["文心一言"]
    TONGYI_ADAPTER -->|真实网页问答| TONGYI["通义千问"]
    YUANBAO_ADAPTER -->|真实网页问答| YUANBAO["腾讯元宝"]

    DOU_ADAPTER -.->|登录或 CAPTCHA| TAKEOVER["人工同会话接管"]
    DEEPSEEK_ADAPTER -.->|登录或 CAPTCHA| TAKEOVER
    YIYAN_ADAPTER -.->|登录或 CAPTCHA| TAKEOVER
    TONGYI_ADAPTER -.->|登录或 CAPTCHA| TAKEOVER
    YUANBAO_ADAPTER -.->|登录或 CAPTCHA| TAKEOVER
    TAKEOVER -.->|恢复原浏览器上下文| CONTEXT

    STATUS["实现成熟度<br/>五平台均有真实网页实跑证据<br/>采集、证据入库与分析投递闭环已验证<br/>各平台成熟度不同"]
    STATUS -.-> COLLECTION_WORKER

    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    class OPERATOR,TAKEOVER human;
    class UPSTREAM_PROXY,DOUBAO,DEEPSEEK,YIYAN,TONGYI,YUANBAO external;
    class ACCOUNT,LEASE,FENCE,STATUS fact;
    class PROXY_PURCHASE,PROXY_RELAY,COLLECTION_WORKER,BROWSER_ROUTER,BROWSER,CONTEXT,DOU_ADAPTER,DEEPSEEK_ADAPTER,YIYAN_ADAPTER,TONGYI_ADAPTER,YUANBAO_ADAPTER software;
```

<details>
<summary>展开组件拓扑图</summary>

```mermaid
flowchart TB
    subgraph PRODUCERS["耐久任务生产者"]
        direction TB
        COLLECTION_API["collection API"]
        SOURCE_API["source_analysis API"]
        POST_API["post_analysis API"]
        REPORT_API["reports API"]
        SOP_API["sop API"]
        SCHEDULER["Scheduler<br/>【已实现】"]
    end

    subgraph COMMANDS["PostgreSQL 中的耐久命令与事件"]
        direction TB
        START_COMMAND[("workflow_start_command")]
        SIGNAL_COMMAND[("workflow_signal_command")]
        EVENT_OUTBOX[("outbox_event")]
        OUTBOX_WORKER["Outbox Worker"]
    end

    subgraph ORCHESTRATION["Temporal 与独立 Worker"]
        direction TB
        TEMPORAL["Temporal Server"]
        COLLECTION_WORKER["Collection Worker<br/>【已实现并实跑】"]
        SOURCE_WORKER["Source Worker"]
        ANALYSIS_WORKER["Analysis Worker"]
        S02_WORKER["S02 Worker"]
    end

    subgraph GOVERNANCE["发送前资源治理"]
        direction TB
        FINAL_ADMISSION["最终准入<br/>现行 v1 与 V2 强化边界"]
        TYPED_GRANT["Typed Grant<br/>【V2 分阶段接入】"]
        QUOTA["原子配额"]
        LEASE["资源 Lease"]
        FENCING["Fencing Token"]
    end

    subgraph SURFACES["资源、Adapter 与外部测量表面"]
        direction TB
        API_CREDENTIAL["模型 API 凭据"]
        PROVIDER_ADAPTER["provider_api Adapter<br/>【待接入】"]
        MODEL_API["模型提供方 API"]

        PLATFORM_ACCOUNT["消费平台账号"]
        RESIDENT_BROWSER["常驻浏览器"]
        REGION_PROXY["地域代理租约"]
        WEB_ADAPTER["consumer_web Adapter<br/>【五平台已实跑】"]
        CONSUMER_WEB["五个消费级 AI 真实网页"]

        APP_DEVICE["真实移动设备<br/>【待实现】"]
        APP_SESSION["App 会话<br/>【待实现】"]
        APP_ADAPTER["consumer_app Adapter<br/>【待实现】"]
        CONSUMER_APP["消费级 AI 真实 App"]

        PUBLIC_PAGE["引用页面与客户官网"]
        HUMAN_TAKEOVER["人工同会话接管"]
    end

    subgraph CAPTURE_OUTPUT["捕获结果"]
        direction TB
        BUSINESS_FACTS[("PostgreSQL<br/>回答与运行事实")]
        NATIVE_EVIDENCE[("对象存储<br/>表面原生证据")]
        ANALYTICS_PROJECTION[("ClickHouse<br/>分析投影")]
    end

    COLLECTION_API ==>|同一事务提交| START_COMMAND
    COLLECTION_API ==>|同一事务提交| SIGNAL_COMMAND
    SOURCE_API ==>|同一事务提交| START_COMMAND
    POST_API ==>|同一事务提交| START_COMMAND
    REPORT_API ==>|同一事务提交| START_COMMAND
    SOP_API ==>|同一事务提交| START_COMMAND
    SCHEDULER ==>|到期物化运行| START_COMMAND
    START_COMMAND -->|领取| OUTBOX_WORKER
    SIGNAL_COMMAND -->|领取| OUTBOX_WORKER
    EVENT_OUTBOX -->|领取| OUTBOX_WORKER
    OUTBOX_WORKER -.->|启动 Workflow| TEMPORAL
    OUTBOX_WORKER -.->|发送 Signal| TEMPORAL
    OUTBOX_WORKER ==>|幂等写入| ANALYTICS_PROJECTION

    TEMPORAL -->|登录态采集队列| COLLECTION_WORKER
    TEMPORAL -->|公开页面队列| SOURCE_WORKER
    TEMPORAL -->|语义分析队列| ANALYSIS_WORKER
    TEMPORAL -->|证据与报告队列| S02_WORKER
    COLLECTION_WORKER ==>|捕获完成事件| EVENT_OUTBOX
    ANALYSIS_WORKER ==>|分析完成事件| EVENT_OUTBOX
    COLLECTION_WORKER ==>|派生分析命令| START_COMMAND

    COLLECTION_WORKER -->|申请执行授权| FINAL_ADMISSION
    FINAL_ADMISSION -->|校验任务与资源身份| TYPED_GRANT
    TYPED_GRANT -->|预留调用额度| QUOTA
    TYPED_GRANT -->|取得独占资源| LEASE
    LEASE -->|隔离旧持有者| FENCING

    API_CREDENTIAL --> PROVIDER_ADAPTER
    PLATFORM_ACCOUNT --> WEB_ADAPTER
    RESIDENT_BROWSER --> WEB_ADAPTER
    REGION_PROXY --> WEB_ADAPTER
    APP_DEVICE --> APP_ADAPTER
    APP_SESSION --> APP_ADAPTER
    QUOTA --> PROVIDER_ADAPTER
    QUOTA --> WEB_ADAPTER
    QUOTA --> APP_ADAPTER
    FENCING --> PROVIDER_ADAPTER
    FENCING --> WEB_ADAPTER
    FENCING --> APP_ADAPTER
    PROVIDER_ADAPTER -->|结构化请求与响应| MODEL_API
    WEB_ADAPTER -->|真实浏览器问答| CONSUMER_WEB
    APP_ADAPTER -->|真实 App 问答| CONSUMER_APP
    WEB_ADAPTER -.->|登录或 CAPTCHA| HUMAN_TAKEOVER
    APP_ADAPTER -.->|登录或原生挑战| HUMAN_TAKEOVER
    HUMAN_TAKEOVER -.->|恢复同一上下文| WEB_ADAPTER
    HUMAN_TAKEOVER -.->|恢复同一上下文| APP_ADAPTER

    COLLECTION_WORKER ==>|保存回答事实| BUSINESS_FACTS
    COLLECTION_WORKER ==>|保存原生证据| NATIVE_EVIDENCE
    SOURCE_WORKER -->|抓取公开页面| PUBLIC_PAGE
    SOURCE_WORKER ==>|保存页面事实| BUSINESS_FACTS
    SOURCE_WORKER ==>|保存页面证据| NATIVE_EVIDENCE
    ANALYSIS_WORKER ==>|保存候选与版本| BUSINESS_FACTS
    S02_WORKER ==>|保存冻结事实| BUSINESS_FACTS
    S02_WORKER ==>|保存报告与证据包| NATIVE_EVIDENCE

    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    class HUMAN_TAKEOVER human;
    class MODEL_API,CONSUMER_WEB,CONSUMER_APP,PUBLIC_PAGE external;
    class START_COMMAND,SIGNAL_COMMAND,EVENT_OUTBOX,BUSINESS_FACTS,NATIVE_EVIDENCE,ANALYTICS_PROJECTION fact;
    class COLLECTION_API,SOURCE_API,POST_API,REPORT_API,SOP_API,SCHEDULER,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,FINAL_ADMISSION,TYPED_GRANT,QUOTA,LEASE,FENCING,API_CREDENTIAL,PROVIDER_ADAPTER,PLATFORM_ACCOUNT,RESIDENT_BROWSER,REGION_PROXY,WEB_ADAPTER,APP_DEVICE,APP_SESSION,APP_ADAPTER software;
```

</details>

#### 1D｜运营侧 Agent Skill、产品内运行时 AI 与人工批准

```mermaid
flowchart TB
    subgraph OPERATIONS_SKILLS["运营侧独立 Agent Skill"]
        direction TB
        OPERATOR["运营人员"]
        QUOTATION_SKILL["geo-quotation<br/>Agent Skill"]
        DIAGRAM_SKILL["workflow-diagram<br/>Agent Skill"]
        PRICING_RULES["确定性定价规则"]
        TEMPLATE_VALIDATOR["确定性模板校验"]
        DOCUMENT_RENDERER["确定性文档渲染器"]
        TOPOLOGY_CHECKER["确定性拓扑检查"]
        DIAGRAM_RENDERER["确定性图形渲染器"]
        QUOTATION_DOC["报价单"]
        EXPLANATION_DOC["报价说明书"]
        FLOW_DOC["流程图"]
        OPS_APPROVAL["运营批准"]
    end

    subgraph PRODUCT_AI["产品内运行时 AI"]
        direction TB
        INTAKE_FORM_API["intake_form API"]
        INTAKE_API["intake API"]
        VARIANTS_API["variants API"]
        ANALYSIS_WORKER["Analysis Worker"]
        REPORTS_API["reports API"]
        RESEARCH_AI["品牌公开调研"]
        QUERY_AI["Query 变体生成"]
        EXTRACTION_AI["品牌与实体抽取"]
        RISK_AI["风险与事实候选"]
        REPORT_AI["报告叙事草稿"]
        MODEL_GATEWAY["受控模型调用"]
        RUNTIME_MODEL["外部运行时模型端点"]

        PROJECT_CANDIDATES["项目与 Query 候选"]
        PROJECT_RULES["项目 Schema 与词表校验"]
        CUSTOMER_CONFIRM["客户或受邀填报人确认"]
        OPS_CONFIRM["运营人员确认"]
        PROJECT_FACTS["已确认项目事实"]

        ANALYSIS_CANDIDATES["实体、风险与关系候选"]
        EVIDENCE_RULES["证据定位、阈值与版本规则"]
        ANALYSIS_REVIEW["分析人员复核"]
        SERVICE_FACTS["冻结服务事实"]

        NARRATIVE_DRAFT["报告叙事草稿"]
        GROUNDING_RULES["冻结事实引用与字段校验"]
        REPORT_REVIEW["报告审核人员批准"]
        APPROVED_NARRATIVE["获批报告措辞"]
    end

    OPERATOR -->|独立调用| QUOTATION_SKILL
    OPERATOR -->|独立调用| DIAGRAM_SKILL
    QUOTATION_SKILL -->|编排价格计算| PRICING_RULES
    QUOTATION_SKILL -->|编排模板字段| TEMPLATE_VALIDATOR
    PRICING_RULES --> DOCUMENT_RENDERER
    TEMPLATE_VALIDATOR --> DOCUMENT_RENDERER
    DOCUMENT_RENDERER --> QUOTATION_DOC
    DOCUMENT_RENDERER --> EXPLANATION_DOC
    DIAGRAM_SKILL -->|编排业务结构| TOPOLOGY_CHECKER
    TOPOLOGY_CHECKER --> DIAGRAM_RENDERER
    DIAGRAM_RENDERER --> FLOW_DOC
    QUOTATION_DOC --> OPS_APPROVAL
    EXPLANATION_DOC --> OPS_APPROVAL
    FLOW_DOC --> OPS_APPROVAL

    INTAKE_FORM_API -->|受邀表单调研| RESEARCH_AI
    INTAKE_FORM_API -->|建议监测问题| QUERY_AI
    INTAKE_API -->|运营端调研| RESEARCH_AI
    VARIANTS_API --> QUERY_AI
    ANALYSIS_WORKER --> EXTRACTION_AI
    ANALYSIS_WORKER --> RISK_AI
    SERVICE_FACTS -->|限定输入| REPORTS_API
    REPORTS_API --> REPORT_AI

    RESEARCH_AI --> MODEL_GATEWAY
    QUERY_AI --> MODEL_GATEWAY
    EXTRACTION_AI --> MODEL_GATEWAY
    RISK_AI --> MODEL_GATEWAY
    REPORT_AI --> MODEL_GATEWAY
    MODEL_GATEWAY --> RUNTIME_MODEL

    RESEARCH_AI ==>|生成候选| PROJECT_CANDIDATES
    QUERY_AI ==>|生成候选| PROJECT_CANDIDATES
    PROJECT_CANDIDATES --> PROJECT_RULES
    PROJECT_RULES -->|客户填报路径| CUSTOMER_CONFIRM
    PROJECT_RULES -->|运营维护路径| OPS_CONFIRM
    CUSTOMER_CONFIRM ==>|确认| PROJECT_FACTS
    OPS_CONFIRM ==>|确认| PROJECT_FACTS

    EXTRACTION_AI ==>|生成候选| ANALYSIS_CANDIDATES
    RISK_AI ==>|生成候选| ANALYSIS_CANDIDATES
    ANALYSIS_CANDIDATES --> EVIDENCE_RULES
    EVIDENCE_RULES --> ANALYSIS_REVIEW
    ANALYSIS_REVIEW ==>|冻结| SERVICE_FACTS

    REPORT_AI ==>|生成| NARRATIVE_DRAFT
    NARRATIVE_DRAFT --> GROUNDING_RULES
    GROUNDING_RULES --> REPORT_REVIEW
    REPORT_REVIEW ==>|批准| APPROVED_NARRATIVE

    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef skill fill:#f4ebff,stroke:#7e22ce,color:#581c87,stroke-width:2px;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    class CUSTOMER_CONFIRM customer;
    class OPERATOR,OPS_APPROVAL,OPS_CONFIRM,ANALYSIS_REVIEW,REPORT_REVIEW human;
    class QUOTATION_SKILL,DIAGRAM_SKILL skill;
    class RESEARCH_AI,QUERY_AI,EXTRACTION_AI,RISK_AI,REPORT_AI runtime;
    class RUNTIME_MODEL external;
    class QUOTATION_DOC,EXPLANATION_DOC,FLOW_DOC,PROJECT_CANDIDATES,PROJECT_FACTS,ANALYSIS_CANDIDATES,SERVICE_FACTS,NARRATIVE_DRAFT,APPROVED_NARRATIVE fact;
    class PRICING_RULES,TEMPLATE_VALIDATOR,DOCUMENT_RENDERER,TOPOLOGY_CHECKER,DIAGRAM_RENDERER,INTAKE_FORM_API,INTAKE_API,VARIANTS_API,ANALYSIS_WORKER,REPORTS_API,MODEL_GATEWAY,PROJECT_RULES,EVIDENCE_RULES,GROUNDING_RULES software;
```

#### 1E｜运行组件如何读写数据并形成监控、备份与发布闭环

##### 数据职责

```mermaid
flowchart LR
    API_APP["FastAPI"] -->|带租户上下文访问| RLS["PostgreSQL RLS"]
    RLS --> POSTGRES[("PostgreSQL<br/>权威业务事实")]
    API_APP -->|缓存与限流| REDIS[("Redis<br/>缓存与短期协调")]
    API_APP -->|读取应用密钥| VAULT["Vault Transit"]
    API_APP ==>|记录操作| AUDIT["审计事件"]
    AUDIT ==>|持久化| POSTGRES

    COLLECTION["Collection Worker"] ==>|回答与运行事实| POSTGRES
    COLLECTION ==>|表面原生证据| OBJECTS[("MinIO 或 S3 CAS<br/>不可变证据")]
    COLLECTION -->|解封采集凭据| VAULT

    SOURCE["Source Worker"] ==>|URL 与页面版本| POSTGRES
    SOURCE ==>|页面与截图证据| OBJECTS
    ANALYSIS["Analysis Worker"] ==>|分析候选与版本| POSTGRES
    REPORT["S02 Worker"] ==>|冻结事实与报告版本| POSTGRES
    REPORT ==>|报告与证据包| OBJECTS

    OUTBOX["Outbox Worker"] ==>|幂等分析投影| CLICKHOUSE[("ClickHouse<br/>可重建投影")]
    TEMPORAL["Temporal Server"] ==>|耐久历史| TEMPORAL_PG[("Temporal PostgreSQL")]
    POSTGRES ==>|按允许字段生成| SAFE["安全客户投影"]
    SAFE -->|通过 FastAPI 客户端点返回| FRONTENDS["五个 Web 工作台"]

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    class POSTGRES,OBJECTS,CLICKHOUSE,SAFE,AUDIT fact;
    class API_APP,RLS,REDIS,VAULT,COLLECTION,SOURCE,ANALYSIS,REPORT,OUTBOX,TEMPORAL,TEMPORAL_PG,FRONTENDS software;
```

##### 监控、备份与发布闭环

```mermaid
flowchart TB
    FASTAPI["FastAPI"] -.->|OTLP 链路| OTEL["OpenTelemetry Collector"]
    COLLECTION_WORKER["Collection Worker"] -.->|OTLP 链路| OTEL
    SOURCE_WORKER["Source Worker"] -.->|OTLP 链路| OTEL
    ANALYSIS_WORKER["Analysis Worker"] -.->|OTLP 链路| OTEL
    S02_WORKER["S02 Worker"] -.->|OTLP 链路| OTEL
    OUTBOX_WORKER["Outbox Worker"] -.->|OTLP 链路| OTEL
    OTEL ==>|落盘| TRACE_FILE[("trace file")]

    FASTAPI -.->|抓取 API 指标端点| PROMETHEUS["Prometheus"]
    BUSINESS_METRICS["Business Metrics Exporter"] -.->|抓取业务指标端点| PROMETHEUS
    OTEL -.->|抓取 Collector 自身指标| PROMETHEUS
    NODE_EXPORTER["Node Exporter"] -.->|抓取主机指标端点| PROMETHEUS

    FASTAPI -.->|systemd 日志| JOURNAL["systemd journal"]
    COLLECTION_WORKER -.->|systemd 日志| JOURNAL
    SOURCE_WORKER -.->|systemd 日志| JOURNAL
    ANALYSIS_WORKER -.->|systemd 日志| JOURNAL
    S02_WORKER -.->|systemd 日志| JOURNAL
    OUTBOX_WORKER -.->|systemd 日志| JOURNAL
    JOURNAL --> ALLOY["Grafana Alloy"]
    ALLOY --> LOKI["Loki"]
    PROMETHEUS -->|触发规则| ALERTMANAGER["Alertmanager"]
    PROMETHEUS -->|指标查询| GRAFANA["Grafana"]
    LOKI -->|日志查询| GRAFANA
    ALERTMANAGER -->|本地 webhook| ALERT_RECEIVER["Alert Receiver"]
    ALERT_RECEIVER ==>|持久化| NOTIFICATION_OUTBOX[("PostgreSQL 通知状态与 Outbox")]
    NOTIFICATION_OUTBOX -->|轮询领取发送命令| FEISHU_BOT["Feishu Bot<br/>发送器与卡片回调"]
    FEISHU_BOT -->|HTTPS 发送或更新卡片| FEISHU_API["飞书 OpenAPI"]

    FASTAPI -.->|挂载| DATASETS_API["datasets API"]
    DATASETS_API ==>|POST 创建耐久请求| MEDIA_REQUEST[("媒体价格刷新请求文件")]
    MEDIA_REQUEST -->|systemd path 触发| MEDIA_REFRESH["Media Price Refresh Worker"]
    MEDIA_REFRESH ==>|原子替换快照| MEDIA_DATASET[("媒体价格数据集")]
    MEDIA_DATASET -->|GET 返回只读快照| DATASET_CLIENT["工作台或匿名客户端"]

    ALERT_RECEIVER -.->|systemd 日志| JOURNAL
    FEISHU_BOT -.->|systemd 日志| JOURNAL
    MEDIA_REFRESH -.->|systemd 日志| JOURNAL

    POSTGRES[("PostgreSQL")] -->|备份源| BACKUP["全量与增量备份"]
    OBJECTS[("对象存储")] -->|备份源| BACKUP
    CLICKHOUSE[("ClickHouse")] -->|备份源| BACKUP
    BACKUP -->|验证可恢复性| RESTORE["恢复演练"]

    TESTS["自动化测试"] --> GATE["迁移与发布预检"]
    RESTORE --> GATE
    GATE -->|通过后允许| RELEASE["原子发布"]
    RELEASE -->|保留前一版本| ROLLBACK["版本回滚"]
    RELEASE -.->|部署| FRONTEND_RELEASE["五前端版本集"]
    RELEASE -.->|部署| FASTAPI
    RELEASE -.->|部署| WORKER_RELEASE["Worker 版本集"]

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    class TRACE_FILE,NOTIFICATION_OUTBOX,MEDIA_REQUEST,MEDIA_DATASET fact;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    class FEISHU_API external;
    class FASTAPI,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,OUTBOX_WORKER,OTEL,BUSINESS_METRICS,NODE_EXPORTER,PROMETHEUS,JOURNAL,ALLOY,LOKI,ALERTMANAGER,ALERT_RECEIVER,FEISHU_BOT,GRAFANA,DATASETS_API,MEDIA_REFRESH,DATASET_CLIENT,POSTGRES,OBJECTS,CLICKHOUSE,BACKUP,RESTORE,TESTS,GATE,RELEASE,ROLLBACK,FRONTEND_RELEASE,WORKER_RELEASE software;
```

<details>
<summary>展开逐运行组件的完整连线图</summary>

```mermaid
flowchart LR
    subgraph RUNTIMES["运行组件"]
        direction TB
        FRONTENDS["五个 Web 工作台"]
        API_APP["FastAPI"]
        OUTBOX_WORKER["Outbox Worker"]
        TEMPORAL["Temporal Server"]
        COLLECTION_WORKER["Collection Worker"]
        SOURCE_WORKER["Source Worker"]
        ANALYSIS_WORKER["Analysis Worker"]
        S02_WORKER["S02 Worker"]
    end

    subgraph DATA_SECURITY["数据与安全组件"]
        direction TB
        RLS["PostgreSQL RLS"]
        POSTGRES[("PostgreSQL<br/>权威业务事实")]
        OBJECT_STORE[("MinIO 或 S3 CAS<br/>不可变证据")]
        CLICKHOUSE[("ClickHouse<br/>可重建投影")]
        REDIS[("Redis<br/>缓存与短期协调")]
        TEMPORAL_PG[("Temporal PostgreSQL<br/>Workflow 历史")]
        VAULT["Vault Transit<br/>密钥与敏感状态"]
        SAFE_PROJECTION["安全客户投影"]
        AUDIT_LOG["审计事件"]
    end

    subgraph OBSERVABILITY["可观测性"]
        direction TB
        OTEL["OpenTelemetry Collector"]
        ALLOY["Grafana Alloy"]
        PROMETHEUS["Prometheus"]
        LOKI["Loki"]
        GRAFANA["Grafana"]
        ALERTMANAGER["Alertmanager"]
    end

    subgraph OPERATIONS["恢复与发布"]
        direction TB
        BACKUP["全量与增量备份"]
        RESTORE_DRILL["恢复演练"]
        MIGRATION_CHECK["迁移预检"]
        RELEASE["原子发布"]
        ROLLBACK["版本回滚"]
    end

    API_APP -->|带租户上下文访问| RLS
    RLS --> POSTGRES
    COLLECTION_WORKER ==>|写回答与运行事实| POSTGRES
    SOURCE_WORKER ==>|写 URL 与页面版本| POSTGRES
    ANALYSIS_WORKER ==>|写分析候选与版本| POSTGRES
    S02_WORKER ==>|写冻结事实与报告版本| POSTGRES
    COLLECTION_WORKER ==>|写表面原生证据| OBJECT_STORE
    SOURCE_WORKER ==>|写页面与截图证据| OBJECT_STORE
    S02_WORKER ==>|写报告与证据包| OBJECT_STORE
    OUTBOX_WORKER ==>|写幂等分析投影| CLICKHOUSE
    API_APP -->|缓存与限流| REDIS
    TEMPORAL ==>|保存耐久历史| TEMPORAL_PG
    API_APP -->|读取应用密钥| VAULT
    COLLECTION_WORKER -->|解封采集凭据| VAULT
    POSTGRES ==>|按允许字段生成| SAFE_PROJECTION
    SAFE_PROJECTION -->|客户可见数据| FRONTENDS
    API_APP ==>|记录操作| AUDIT_LOG
    AUDIT_LOG ==>|持久化| POSTGRES

    API_APP -.->|指标与链路| OTEL
    OUTBOX_WORKER -.->|指标与链路| OTEL
    TEMPORAL -.->|运行指标| OTEL
    COLLECTION_WORKER -.->|指标与链路| OTEL
    SOURCE_WORKER -.->|指标与链路| OTEL
    ANALYSIS_WORKER -.->|指标与链路| OTEL
    S02_WORKER -.->|指标与链路| OTEL
    API_APP -.->|服务日志| ALLOY
    COLLECTION_WORKER -.->|服务日志| ALLOY
    SOURCE_WORKER -.->|服务日志| ALLOY
    ANALYSIS_WORKER -.->|服务日志| ALLOY
    S02_WORKER -.->|服务日志| ALLOY
    OTEL --> PROMETHEUS
    ALLOY --> LOKI
    PROMETHEUS -->|触发告警规则| ALERTMANAGER
    PROMETHEUS -->|指标查询| GRAFANA
    LOKI -->|日志查询| GRAFANA

    POSTGRES -->|备份源| BACKUP
    OBJECT_STORE -->|备份源| BACKUP
    CLICKHOUSE -->|备份源| BACKUP
    BACKUP -->|验证可恢复性| RESTORE_DRILL
    MIGRATION_CHECK -->|通过后允许| RELEASE
    RELEASE -.->|部署| FRONTENDS
    RELEASE -.->|部署| API_APP
    RELEASE -.->|部署| OUTBOX_WORKER
    RELEASE -.->|部署| COLLECTION_WORKER
    RELEASE -.->|部署| SOURCE_WORKER
    RELEASE -.->|部署| ANALYSIS_WORKER
    RELEASE -.->|部署| S02_WORKER
    RELEASE -->|保留前一版本| ROLLBACK

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;
    class POSTGRES,OBJECT_STORE,CLICKHOUSE,SAFE_PROJECTION,AUDIT_LOG fact;
    class FRONTENDS,API_APP,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,RLS,REDIS,TEMPORAL_PG,VAULT,OTEL,ALLOY,PROMETHEUS,LOKI,GRAFANA,ALERTMANAGER,BACKUP,RESTORE_DRILL,MIGRATION_CHECK,RELEASE,ROLLBACK software;
```

</details>

#### 1F｜全连接索引图（适合放大后做开发审计）

<details>
<summary>展开全连接索引图</summary>

这张索引图把五个分面的同名节点合并到一个画布，适合搜索遗漏关系和做架构审计；展示讲解优先使用 1A—1E。

```mermaid
flowchart TB
    subgraph L1["A. 访问角色、产品入口与独立 Agent Skill"]
        direction LR
        CUSTOMER["客户"]
        INVITEE["受邀填报人"]
        OPERATOR["运营人员"]
        ANALYST["信源分析人员"]
        REVIEWER["报告审核人员"]

        CUSTOMER_WEB["customer-web"]
        INTAKE_FORM["intake-form"]
        OPERATIONS_WEB["operations-web"]
        INTELLIGENCE_WEB["intelligence-web"]
        REPORT_STUDIO["report-studio"]
        TERMINAL_EXT["customer-terminal-extension<br/>辅助挑战确认【部分完成】"]
        FRONTEND_PACKAGES["共享前端 packages"]

        QUOTATION_SKILL["geo-quotation<br/>Agent Skill"]
        DIAGRAM_SKILL["workflow-diagram<br/>Agent Skill"]
        PRICING_TOOL["确定性定价规则工具"]
        DOCUMENT_TOOL["确定性文档渲染工具"]
        DIAGRAM_TOOL["确定性图表校验工具"]
        QUOTATION_DOC["报价单"]
        EXPLANATION_DOC["报价说明书"]
        FLOW_DOC["流程图"]
        OPS_APPROVAL["运营批准"]

        CUSTOMER -->|查看结果并确认项目事实| CUSTOMER_WEB
        CUSTOMER -->|在本机完成原生挑战| TERMINAL_EXT
        INVITEE -->|填写品牌与项目资料| INTAKE_FORM
        OPERATOR -->|配置并控制业务| OPERATIONS_WEB
        ANALYST -->|研究并复核信源| INTELLIGENCE_WEB
        REVIEWER -->|审核并发布报告| REPORT_STUDIO

        FRONTEND_PACKAGES -.->|复用认证契约| CUSTOMER_WEB
        FRONTEND_PACKAGES -.->|复用表单组件| INTAKE_FORM
        FRONTEND_PACKAGES -.->|复用工作流组件| OPERATIONS_WEB
        FRONTEND_PACKAGES -.->|复用证据查看器| INTELLIGENCE_WEB
        FRONTEND_PACKAGES -.->|复用领域类型| REPORT_STUDIO

        OPERATOR -->|独立调用| QUOTATION_SKILL
        OPERATOR -->|独立调用| DIAGRAM_SKILL
        QUOTATION_SKILL -->|编排价格计算| PRICING_TOOL
        QUOTATION_SKILL -->|编排内容与版式| DOCUMENT_TOOL
        DIAGRAM_SKILL -->|编排结构与图形| DIAGRAM_TOOL
        PRICING_TOOL --> QUOTATION_DOC
        DOCUMENT_TOOL --> QUOTATION_DOC
        DOCUMENT_TOOL --> EXPLANATION_DOC
        DIAGRAM_TOOL --> FLOW_DOC
        QUOTATION_DOC --> OPS_APPROVAL
        EXPLANATION_DOC --> OPS_APPROVAL
        FLOW_DOC --> OPS_APPROVAL
    end

    subgraph L2["B. 四类边缘入口与逐级授权"]
        direction LR
        CUSTOMER_EDGE["customer edge"]
        INTAKE_EDGE["intake edge"]
        MANAGEMENT_EDGE["management edge"]
        AUXILIARY_EDGE["auxiliary edge"]

        TLS["TLS 终止"]
        ROUTER["域名与路径路由"]
        RATE_LIMIT["请求限流"]
        HEADER_CLEAN["身份头清洗"]
        SESSION_AUTH["会话鉴权"]
        SIGNED_TASK_AUTH["一次性票据与签名任务校验"]
        RBAC["角色授权"]
        CAPABILITY["能力授权"]
        PROJECT_SCOPE["租户与项目范围校验"]
        API_APP["FastAPI 应用边界"]

        CUSTOMER_WEB --> CUSTOMER_EDGE
        INTAKE_FORM --> INTAKE_EDGE
        OPERATIONS_WEB --> MANAGEMENT_EDGE
        INTELLIGENCE_WEB --> MANAGEMENT_EDGE
        REPORT_STUDIO --> MANAGEMENT_EDGE
        TERMINAL_EXT --> AUXILIARY_EDGE

        CUSTOMER_EDGE -->|HTTPS 请求| TLS
        INTAKE_EDGE -->|HTTPS 请求| TLS
        MANAGEMENT_EDGE -->|HTTPS 请求| TLS
        AUXILIARY_EDGE -->|HTTPS 请求| TLS
        TLS --> ROUTER
        ROUTER --> RATE_LIMIT
        RATE_LIMIT --> HEADER_CLEAN
        HEADER_CLEAN -->|常规工作台请求| SESSION_AUTH
        HEADER_CLEAN -->|辅助接管请求| SIGNED_TASK_AUTH
        SESSION_AUTH --> RBAC
        RBAC --> CAPABILITY
        SIGNED_TASK_AUTH --> CAPABILITY
        CAPABILITY --> PROJECT_SCOPE
        PROJECT_SCOPE -->|携带已验证上下文| API_APP
    end

    subgraph L3["C. FastAPI 模块化单体中的业务域关系"]
        direction LR
        IDENTITY["身份域<br/>identity"]
        TENANCY["租户域<br/>tenancy"]
        INTAKE_FORM_API["邀请表单域<br/>intake_form"]
        INTAKE["资料接收域<br/>intake"]
        PROJECTS["项目事实域<br/>projects"]
        QUOTATIONS["报价域<br/>quotations"]
        VARIANTS["问题变体域<br/>variants"]
        ADMISSION["配置冻结与执行准入"]
        COLLECTION["采集控制域<br/>collection"]
        DATASETS["数据集域<br/>datasets"]
        EVIDENCE["证据域<br/>evidence"]
        SOURCE_INTEL["信源情报域<br/>source_intelligence"]
        INTELLIGENCE["智能任务域<br/>intelligence"]
        SOURCE_ANALYSIS["信源分析域<br/>source_analysis"]
        ANALYTICS["指标分析域<br/>analytics"]
        BRANDRANK["推荐排名域<br/>brandrank"]
        SERVICE2_CORPUS["服务二语料域<br/>重建中"]
        POSTING["内容发布域<br/>posting"]
        POST_ANALYSIS["发布复测域<br/>post_analysis"]
        REPORTS["服务事实与报告域<br/>reports"]
        EXPORTS["交付导出域<br/>exports"]
        CUSTOMER_SERVICES["客户服务投影<br/>customer_services"]
        CUSTOMER_DASHBOARD["客户总览投影<br/>customer_dashboard"]
        NOTIFICATIONS["通知与接管协作<br/>notifications<br/>含独立 Feishu Bot"]
        OTP["OTP 协作域<br/>otp"]
        SOP["运行规程域<br/>sop"]

        API_APP -.->|挂载 Router| IDENTITY
        API_APP -.->|挂载 Router| INTAKE_FORM_API
        API_APP -.->|挂载 Router| INTAKE
        API_APP -.->|挂载 Router| PROJECTS
        API_APP -.->|挂载 Router| QUOTATIONS
        API_APP -.->|挂载 Router| COLLECTION
        API_APP -.->|挂载 Router| EVIDENCE
        API_APP -.->|挂载 Router| ANALYTICS
        API_APP -.->|挂载 Router| REPORTS
        API_APP -.->|挂载 Router| CUSTOMER_SERVICES
        API_APP -.->|挂载 Router| SOP

        CUSTOMER_WEB -.->|授权后调用| CUSTOMER_DASHBOARD
        CUSTOMER_WEB -.->|授权后调用| CUSTOMER_SERVICES
        CUSTOMER_WEB -.->|授权后读取| EVIDENCE
        INTAKE_FORM -.->|邀请票据访问| INTAKE_FORM_API
        INTAKE_FORM_API -->|提交确认内容| INTAKE
        OPERATIONS_WEB -.->|授权后控制| COLLECTION
        OPERATIONS_WEB -.->|授权后管理| QUOTATIONS
        INTELLIGENCE_WEB -.->|授权后研究| SOURCE_INTEL
        INTELLIGENCE_WEB -.->|授权后复核| SOURCE_ANALYSIS
        REPORT_STUDIO -.->|授权后审核| REPORTS

        IDENTITY -->|建立主体| TENANCY
        TENANCY -->|限定归属| PROJECTS
        INTAKE -->|形成客户确认事实| PROJECTS
        PROJECTS -->|定义服务范围| QUOTATIONS
        PROJECTS -->|提供问题基线| VARIANTS
        QUOTATIONS -->|提供权益与价格约束| ADMISSION
        VARIANTS -->|提供冻结问题集合| ADMISSION
        PROJECTS -->|提供授权范围| ADMISSION
        ADMISSION -->|准入通过| COLLECTION
        COLLECTION -->|组织回答与运行批次| DATASETS
        DATASETS -->|关联原始回答| EVIDENCE
        EVIDENCE -->|提供引用事实| SOURCE_INTEL
        SOURCE_INTEL -->|形成研究对象| INTELLIGENCE
        INTELLIGENCE -->|提交分析版本| SOURCE_ANALYSIS
        EVIDENCE -->|提供测量事实| ANALYTICS
        EVIDENCE -->|提供回答样本| BRANDRANK
        EVIDENCE -->|提供全 U 重建输入| SERVICE2_CORPUS
        POSTING -->|提供发布事实| POST_ANALYSIS
        ANALYTICS -->|提供指标事实| REPORTS
        BRANDRANK -->|提供服务一事实| REPORTS
        SERVICE2_CORPUS -->|提供服务二迁移与生产事实| REPORTS
        SOURCE_ANALYSIS -->|提供服务三与服务四事实| REPORTS
        POST_ANALYSIS -->|提供服务五事实| REPORTS
        REPORTS -->|冻结交付版本| EXPORTS
        REPORTS -->|生成安全服务视图| CUSTOMER_SERVICES
        ANALYTICS -->|生成趋势投影| CUSTOMER_DASHBOARD
        PROJECTS -->|生成项目投影| CUSTOMER_DASHBOARD
        COLLECTION -.->|异常与状态事件| NOTIFICATIONS
        REPORTS -.->|审核与发布事件| NOTIFICATIONS
        NOTIFICATIONS -.->|发起人工接管| OTP
        SOP -.->|约束暂停与恢复| COLLECTION
        SOP -.->|约束审核与发布| REPORTS
    end

    subgraph L4["D. 耐久命令、Temporal 与独立 Worker"]
        direction LR
        START_COMMAND[("workflow_start_command")]
        SIGNAL_COMMAND[("workflow_signal_command")]
        EVENT_OUTBOX[("outbox_event")]
        SCHEDULER["Scheduler<br/>【已实现】"]
        OUTBOX_WORKER["Outbox Worker<br/>命令派发与分析投影"]
        TEMPORAL["Temporal Server"]
        COLLECTION_WORKER["Collection Worker<br/>登录态采集【已实现并实跑】"]
        SOURCE_WORKER["Source Worker<br/>公开页面采集"]
        ANALYSIS_WORKER["Analysis Worker<br/>语义与风险分析"]
        S02_WORKER["S02 Worker<br/>证据与报告生产"]

        COLLECTION ==>|同一事务提交启动命令| START_COMMAND
        SOURCE_ANALYSIS ==>|同一事务提交分析命令| START_COMMAND
        POST_ANALYSIS ==>|同一事务提交复测命令| START_COMMAND
        REPORTS ==>|同一事务提交报告命令| START_COMMAND
        SOP ==>|同一事务提交规程任务| START_COMMAND
        COLLECTION ==>|同一事务提交控制命令| SIGNAL_COMMAND
        SCHEDULER ==>|到期物化运行| START_COMMAND
        START_COMMAND -->|领取待派发命令| OUTBOX_WORKER
        SIGNAL_COMMAND -->|领取待派发命令| OUTBOX_WORKER
        EVENT_OUTBOX -->|领取待投影事件| OUTBOX_WORKER
        OUTBOX_WORKER -.->|启动 Workflow| TEMPORAL
        OUTBOX_WORKER -.->|发送 Signal| TEMPORAL
        TEMPORAL -->|登录态采集队列| COLLECTION_WORKER
        TEMPORAL -->|公开页面队列| SOURCE_WORKER
        TEMPORAL -->|分析队列| ANALYSIS_WORKER
        TEMPORAL -->|证据与报告队列| S02_WORKER
        COLLECTION_WORKER ==>|保存捕获事件| EVENT_OUTBOX
        ANALYSIS_WORKER ==>|保存分析事件| EVENT_OUTBOX
        COLLECTION_WORKER ==>|提交回答分析命令| START_COMMAND
        COLLECTION_WORKER ==>|提交运行级分析命令| START_COMMAND
    end

    subgraph L5["E. 运行时 AI、资源治理与三类外部测量表面"]
        direction LR
        RESEARCH_AI["品牌公开调研<br/>产品内运行时 AI"]
        QUERY_AI["Query 变体生成<br/>产品内运行时 AI"]
        EXTRACTION_AI["品牌与实体抽取<br/>产品内运行时 AI"]
        RISK_AI["风险与事实候选<br/>产品内运行时 AI"]
        REPORT_AI["报告叙事草稿<br/>产品内运行时 AI"]
        RUNTIME_MODEL["外部运行时模型端点"]

        FINAL_ADMISSION["发送前最终准入"]
        V1_RESOURCE_GATE["v1 账号、会话与浏览器校验<br/>【现行路径】"]
        TYPED_GRANT["Typed Grant<br/>【V2 分阶段接入】"]
        QUOTA["原子配额"]
        LEASE["资源 Lease"]
        FENCING["Fencing Token"]

        API_CREDENTIAL["模型 API 凭据"]
        PLATFORM_ACCOUNT["消费平台账号"]
        RESIDENT_BROWSER["常驻浏览器"]
        REGION_PROXY["地域代理租约"]
        APP_DEVICE["真实移动设备<br/>【待实现】"]
        APP_SESSION["App 会话<br/>【待实现】"]

        PROVIDER_ADAPTER["provider_api Adapter<br/>【待接入】"]
        WEB_ADAPTER["consumer_web Adapter<br/>【五平台已实跑】"]
        APP_ADAPTER["consumer_app Adapter<br/>【待实现】"]
        MODEL_API["模型提供方 API"]
        CONSUMER_WEB["五个消费级 AI 真实网页"]
        CONSUMER_APP["消费级 AI 真实 App"]
        PUBLIC_PAGE["被引用页面与客户官网"]
        MEDIA_PROVIDER["媒体与内容供应商"]
        HUMAN_TAKEOVER["人工同会话接管"]

        INTAKE_FORM_API -.->|请求表单预填候选| RESEARCH_AI
        INTAKE_FORM_API -.->|请求监测问题建议| QUERY_AI
        INTAKE -.->|请求运营端预填候选| RESEARCH_AI
        VARIANTS -.->|请求问题候选| QUERY_AI
        ANALYSIS_WORKER -.->|请求语义候选| EXTRACTION_AI
        ANALYSIS_WORKER -.->|请求审核候选| RISK_AI
        REPORTS -.->|请求受约束草稿| REPORT_AI
        RESEARCH_AI --> RUNTIME_MODEL
        QUERY_AI --> RUNTIME_MODEL
        EXTRACTION_AI --> RUNTIME_MODEL
        RISK_AI --> RUNTIME_MODEL
        REPORT_AI --> RUNTIME_MODEL

        COLLECTION_WORKER -->|申请执行授权| FINAL_ADMISSION
        FINAL_ADMISSION -->|现行路径| V1_RESOURCE_GATE
        V1_RESOURCE_GATE -->|检查并记录平台额度| QUOTA
        V1_RESOURCE_GATE -->|取得资源租约| LEASE
        FINAL_ADMISSION -.->|V2 分阶段强化| TYPED_GRANT
        TYPED_GRANT -->|预留调用额度| QUOTA
        TYPED_GRANT -->|取得独占资源| LEASE
        LEASE -->|阻止旧持有者写入| FENCING

        API_CREDENTIAL --> PROVIDER_ADAPTER
        PLATFORM_ACCOUNT --> WEB_ADAPTER
        RESIDENT_BROWSER --> WEB_ADAPTER
        REGION_PROXY --> WEB_ADAPTER
        APP_DEVICE --> APP_ADAPTER
        APP_SESSION --> APP_ADAPTER
        QUOTA --> PROVIDER_ADAPTER
        QUOTA --> WEB_ADAPTER
        QUOTA --> APP_ADAPTER
        FENCING --> PROVIDER_ADAPTER
        FENCING --> WEB_ADAPTER
        FENCING --> APP_ADAPTER
        PROVIDER_ADAPTER -->|结构化请求与响应| MODEL_API
        WEB_ADAPTER -->|真实浏览器问答| CONSUMER_WEB
        APP_ADAPTER -->|真实 App 问答| CONSUMER_APP
        SOURCE_WORKER -->|HTTP 获取与页面快照| PUBLIC_PAGE
        POSTING -->|提交内容并读取发布状态| MEDIA_PROVIDER
        WEB_ADAPTER -.->|登录或 CAPTCHA| HUMAN_TAKEOVER
        APP_ADAPTER -.->|登录或原生挑战| HUMAN_TAKEOVER
        OPERATOR -->|处理异常| HUMAN_TAKEOVER
        TERMINAL_EXT -.->|提交挑战结果| HUMAN_TAKEOVER
        HUMAN_TAKEOVER -.->|恢复同一采集上下文| WEB_ADAPTER
        HUMAN_TAKEOVER -.->|恢复同一采集上下文| APP_ADAPTER
    end

    subgraph L6["F. 权威事实、不可变证据、运行状态与可观测性"]
        direction LR
        RLS["PostgreSQL RLS"]
        POSTGRES[("PostgreSQL<br/>权威业务事实")]
        OBJECT_STORE[("MinIO 或 S3 CAS<br/>不可变证据对象")]
        CLICKHOUSE[("ClickHouse<br/>可重建分析投影")]
        REDIS[("Redis<br/>缓存与短期协调")]
        TEMPORAL_PG[("Temporal PostgreSQL<br/>Workflow 历史")]
        VAULT["Vault Transit<br/>密钥与敏感状态"]
        SAFE_PROJECTION["安全客户投影"]
        AUDIT_LOG["审计事件"]

        OTEL["OpenTelemetry Collector"]
        TRACE_FILE[("OTel trace file")]
        BUSINESS_METRICS["Business Metrics Exporter"]
        SYSTEMD_JOURNAL["systemd journal"]
        ALLOY["Grafana Alloy"]
        PROMETHEUS["Prometheus"]
        LOKI["Loki"]
        GRAFANA["Grafana"]
        ALERTMANAGER["Alertmanager"]
        NODE_EXPORTER["Node Exporter"]
        ALERT_RECEIVER["Alert Receiver"]
        NOTIFICATION_OUTBOX[("PostgreSQL 通知状态与 Outbox")]
        FEISHU_BOT["Feishu Bot<br/>发送器与卡片回调"]
        FEISHU_API["飞书 OpenAPI"]
        MEDIA_REFRESH_REQUEST[("媒体价格刷新请求文件")]
        MEDIA_REFRESH_WORKER["Media Price Refresh Worker"]
        MEDIA_DATASET[("媒体价格数据集")]
        BACKUP["全量与增量备份"]
        RELEASE["原子发布与版本回滚"]

        API_APP -->|带租户上下文访问| RLS
        RLS --> POSTGRES
        COLLECTION_WORKER ==>|写回答与运行事实| POSTGRES
        SOURCE_WORKER ==>|写 URL 与页面版本事实| POSTGRES
        ANALYSIS_WORKER ==>|写分析候选与版本| POSTGRES
        S02_WORKER ==>|写冻结事实与报告版本| POSTGRES
        COLLECTION_WORKER ==>|写表面原生证据| OBJECT_STORE
        SOURCE_WORKER ==>|写页面与截图证据| OBJECT_STORE
        S02_WORKER ==>|写报告与证据包| OBJECT_STORE
        OUTBOX_WORKER ==>|幂等写分析投影| CLICKHOUSE
        API_APP -->|缓存与限流| REDIS
        TEMPORAL ==>|保存耐久历史| TEMPORAL_PG
        API_APP -->|读取应用密钥| VAULT
        COLLECTION_WORKER -->|解封采集凭据| VAULT
        POSTGRES ==>|生成允许字段| SAFE_PROJECTION
        SAFE_PROJECTION -->|提供客户可见数据| CUSTOMER_SERVICES
        API_APP ==>|记录操作| AUDIT_LOG
        AUDIT_LOG ==>|持久化| POSTGRES

        API_APP -.->|OTLP 链路| OTEL
        OUTBOX_WORKER -.->|OTLP 链路| OTEL
        COLLECTION_WORKER -.->|OTLP 链路| OTEL
        SOURCE_WORKER -.->|OTLP 链路| OTEL
        ANALYSIS_WORKER -.->|OTLP 链路| OTEL
        S02_WORKER -.->|OTLP 链路| OTEL
        OTEL ==>|落盘| TRACE_FILE
        API_APP -.->|抓取 API 指标| PROMETHEUS
        BUSINESS_METRICS -.->|抓取业务指标| PROMETHEUS
        OTEL -.->|抓取 Collector 自身指标| PROMETHEUS
        NODE_EXPORTER -.->|抓取主机指标| PROMETHEUS
        API_APP -.->|服务日志| SYSTEMD_JOURNAL
        OUTBOX_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        COLLECTION_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        SOURCE_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        ANALYSIS_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        S02_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        ALERT_RECEIVER -.->|服务日志| SYSTEMD_JOURNAL
        FEISHU_BOT -.->|服务日志| SYSTEMD_JOURNAL
        MEDIA_REFRESH_WORKER -.->|服务日志| SYSTEMD_JOURNAL
        SYSTEMD_JOURNAL --> ALLOY
        ALLOY --> LOKI
        PROMETHEUS -->|触发规则| ALERTMANAGER
        ALERTMANAGER -->|本地 webhook| ALERT_RECEIVER
        ALERT_RECEIVER ==>|持久化| NOTIFICATION_OUTBOX
        NOTIFICATION_OUTBOX -->|轮询领取发送命令| FEISHU_BOT
        FEISHU_BOT -->|发送或更新卡片| FEISHU_API
        DATASETS ==>|创建耐久请求| MEDIA_REFRESH_REQUEST
        MEDIA_REFRESH_REQUEST -->|systemd path 触发| MEDIA_REFRESH_WORKER
        MEDIA_REFRESH_WORKER ==>|原子替换快照| MEDIA_DATASET
        MEDIA_DATASET -->|提供只读快照| DATASETS
        PROMETHEUS -->|指标查询| GRAFANA
        LOKI -->|日志查询| GRAFANA
        POSTGRES -->|备份源| BACKUP
        OBJECT_STORE -->|备份源| BACKUP
        CLICKHOUSE -->|备份源| BACKUP
        RELEASE -.->|部署| CUSTOMER_WEB
        RELEASE -.->|部署| API_APP
        RELEASE -.->|部署| OUTBOX_WORKER
        RELEASE -.->|部署| ANALYSIS_WORKER
    end

    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef skill fill:#f4ebff,stroke:#7e22ce,color:#581c87,stroke-width:2px;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;

    class CUSTOMER,INVITEE customer;
    class OPERATOR,ANALYST,REVIEWER,OPS_APPROVAL,HUMAN_TAKEOVER human;
    class QUOTATION_SKILL,DIAGRAM_SKILL skill;
    class RESEARCH_AI,QUERY_AI,EXTRACTION_AI,RISK_AI,REPORT_AI runtime;
    class RUNTIME_MODEL,MODEL_API,CONSUMER_WEB,CONSUMER_APP,PUBLIC_PAGE,MEDIA_PROVIDER,FEISHU_API external;
    class QUOTATION_DOC,EXPLANATION_DOC,FLOW_DOC,START_COMMAND,SIGNAL_COMMAND,EVENT_OUTBOX,POSTGRES,OBJECT_STORE,CLICKHOUSE,SAFE_PROJECTION,AUDIT_LOG,TRACE_FILE,NOTIFICATION_OUTBOX,MEDIA_REFRESH_REQUEST,MEDIA_DATASET fact;
    class CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO,TERMINAL_EXT,FRONTEND_PACKAGES,PRICING_TOOL,DOCUMENT_TOOL,DIAGRAM_TOOL software;
    class CUSTOMER_EDGE,INTAKE_EDGE,MANAGEMENT_EDGE,AUXILIARY_EDGE,TLS,ROUTER,RATE_LIMIT,HEADER_CLEAN,SESSION_AUTH,SIGNED_TASK_AUTH,RBAC,CAPABILITY,PROJECT_SCOPE,API_APP software;
    class IDENTITY,TENANCY,INTAKE_FORM_API,INTAKE,PROJECTS,QUOTATIONS,VARIANTS,ADMISSION,COLLECTION,DATASETS,EVIDENCE,SOURCE_INTEL,INTELLIGENCE,SOURCE_ANALYSIS,ANALYTICS,BRANDRANK,SERVICE2_CORPUS,POSTING,POST_ANALYSIS,REPORTS,EXPORTS,CUSTOMER_SERVICES,CUSTOMER_DASHBOARD,NOTIFICATIONS,OTP,SOP software;
    class SCHEDULER,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,FINAL_ADMISSION,V1_RESOURCE_GATE,TYPED_GRANT,QUOTA,LEASE,FENCING software;
    class API_CREDENTIAL,PLATFORM_ACCOUNT,RESIDENT_BROWSER,REGION_PROXY,APP_DEVICE,APP_SESSION,PROVIDER_ADAPTER,WEB_ADAPTER,APP_ADAPTER software;
    class RLS,REDIS,TEMPORAL_PG,VAULT,OTEL,BUSINESS_METRICS,SYSTEMD_JOURNAL,ALLOY,PROMETHEUS,LOKI,GRAFANA,ALERTMANAGER,NODE_EXPORTER,ALERT_RECEIVER,FEISHU_BOT,MEDIA_REFRESH_WORKER,BACKUP,RELEASE software;
```

</details>

讲解重点：

1. 五个工作台分别服务客户、填报、运营、信源分析和报告审核；共享前端包提供复用能力，四类边缘入口保持安全域隔离。
2. FastAPI 是一个部署单元，内部业务域通过项目事实、冻结配置、命令 Outbox、领域事件和安全投影协作；Feishu Bot、Alert Receiver 和各类 Worker 以独立进程运行。
3. Outbox Worker 同时派发 Temporal 启动/信号命令并消费分析事件；Temporal 再把不同任务路由给四类独立 Worker。
4. Collection Worker 承担登录态消费级 AI 网页采集；Source Worker 只获取引用页面与客户官网等公开网页。
5. 三种采集表面共享任务身份与证据协议。现行 v1 路径执行账号、会话、浏览器、配额、Lease 和 Fencing 校验；Typed Grant 是 V2 分阶段强化路径。各 Adapter 连接自己的资源与外部测量对象。
6. 运营侧 Agent Skill 独立生成报价单、报价说明书和流程图；产品内运行时 AI 通过业务 API 或 Worker 生成候选事实与叙事草稿。
7. PostgreSQL、对象存储、ClickHouse、Redis、Temporal PostgreSQL 和 Vault 分别承担权威事实、不可变证据、分析投影、短期状态、Workflow 历史与密钥管理责任。

---

## 核心图二｜领导视角：七层技术体系（对应 02 #8）

这张图展示系统的技术纵深。上层负责角色协作和业务控制，中层负责智能处理与真实世界执行，下层负责证据、专业服务和可信交付。

```mermaid
flowchart TB
    subgraph EXPERIENCE["体验与业务控制"]
        direction LR
        L1["第 1 层｜五个角色工作台<br/>客户・邀请・运营・情报・报告生产"]
        L2["第 2 层｜业务与控制平面<br/>租户・项目・报价・权益・配置・准入・治理"]
    end

    subgraph EXECUTION["智能与真实世界执行"]
        direction LR
        L3A["第 3A 层｜产品内运行时 AI<br/>调研・Query・实体抽取<br/>风险/事实候选・报告叙事草稿"]
        L3S["第 3B 层｜运营侧 Agent Skill<br/>独立运营工具链<br/>报价单・报价说明书・流程图<br/>编排 AI 判断 + 确定性工具 + 人工批准"]
        L4S["第 4 层｜采集执行软件<br/>Temporal・Worker・任务/配额<br/>凭证/浏览器/设备/代理资源<br/>Web Adapter【五平台已实跑】<br/>API Adapter【待接入】・App Adapter【待实现】"]
        L4E["第 4 层｜外部 AI 测量表面<br/>模型 API・消费级 AI 真实网页<br/>消费级 AI 真实 App"]
    end

    subgraph PRODUCTION["证据与专业生产"]
        direction LR
        L5["第 5 层｜证据与测量事实<br/>原始回答・原生证据・URL/Occurrence<br/>页面版本・U/V/W・指标/置信区间"]
        L6["第 6 层｜五项专业服务事实<br/>推荐排名・全 U 贬损・品牌传播<br/>官网效率・内容发布复测・独立审核冻结"]
    end

    L7["第 7 层｜可信交付与企业运行保障<br/>独立审核/冻结・报告版本・安全客户投影<br/>RLS/DLP/Vault・监控/告警/备份/发布"]

    L1 --> L3A
    L2 --> L4S --> L4E
    L3A --> L5
    L4E --> L6
    L5 --> L7
    L6 --> L7

    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef skill fill:#f4ebff,stroke:#7e22ce,color:#581c87,stroke-width:2px;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px;
    classDef external fill:#f3f4f6,stroke:#6b7280,color:#1f2937,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;

    class L1,L2,L4S,L7 software;
    class L4E external;
    class L3S skill;
    class L3A runtime;
    class L5,L6 fact;
```

讲解重点：

1. Agent Skill 是独立调用的运营工具链，负责报价单、报价说明书和流程图等完整制品；人工批准后形成正式商业或流程资料。
2. 产品内运行时 AI 负责品牌调研、问题变体、实体抽取、风险/事实候选和报告草稿。
3. 确定性软件负责权限、定价、任务、采集控制、指标、版本和渲染；Web Adapter 已在五个平台实跑，API Adapter 待接入，App Adapter 待实现。
4. 五项服务分别审核并冻结自己的事实，报告系统随后生成正式交付版本。

---

## 核心图三｜客户视角：客户结果背后的系统（对应 02 #12）

客户通过一个统一工作区查看五项服务结果、案例、证据和报告。下方系统完成采集、取证、分析、审核、版本管理和运行保障。

```mermaid
flowchart LR
    subgraph RUN["真实世界执行与企业运行"]
        direction TB
        SEC["企业运行保障<br/>租户隔离・权限・审计・密钥<br/>监控・告警・备份・发布回滚"]
        ORC["耐久执行系统<br/>配置版本・任务身份・调度・重试<br/>配额・资源隔离・失败恢复"]
        COL["三表面协作采集<br/>模型 API【待接入】<br/>真实网页【五平台已实跑】・真实 App【待实现】"]
        SEC --> ORC --> COL
    end

    subgraph PROD["证据、智能与专业分析"]
        direction TB
        EVI["可追溯证据<br/>原始回答・引用位置・URL/Occurrence<br/>页面版本・表面原生证据・内容哈希"]
        INT["智能增强系统<br/>品牌调研・问题变体・品牌抽取<br/>风险/事实候选・受约束报告草稿"]
        SVC["五项专业服务事实<br/>推荐排名・全 U 贬损・品牌影响传播<br/>官网效率・内容发布复测"]
        EVI --> INT --> SVC
        EVI --> SVC
    end

    subgraph DELIVERY["审核、交付与客户结果"]
        direction TB
        RPT["可信交付版本<br/>服务事实审核冻结・报告版本<br/>DOCX/PDF/XLSX・安全客户视图"]
        TOP["客户可见结果<br/>五项服务工作区・趋势/案例<br/>证据查看・正式报告"]
        RPT --> TOP
    end

    RUN -->|原生回答与证据| PROD
    PROD -->|冻结服务事实| DELIVERY
    RUN -.安全、审计与运行保障.-> DELIVERY

    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef software fill:#eaf2ff,stroke:#2563eb,color:#172554,stroke-width:2px;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.5px;

    class TOP customer;
    class RPT,SVC,EVI fact;
    class INT runtime;
    class COL,ORC,SEC software;
```

讲解重点：

1. 客户看到简洁结果，系统保留每个结果的测量条件和证据来源。
2. 五项服务共享原始事实，并分别采用自己的定义、审核过程和冻结版本。
3. 智能增强系统提供语义处理能力，确定性软件提供稳定计算与版本管理。
4. 企业运行保障覆盖数据隔离、审计、监控、备份和发布回滚。

---

# 分模块说明

## A. 界面、身份与项目

### 01｜五个角色工作台【已上线】

<code>customer-web</code> 向客户展示项目、五项服务、回答、证据和报告。<code>intake-form</code> 收集品牌、产品和材料。<code>operations-web</code> 承载运营控制。<code>intelligence-web</code> 承载信源研究。<code>report-studio</code> 承载报告审核与发布。当前应用直接复用 <code>api-client</code>、<code>auth</code>、<code>design-system</code>、<code>charts</code>、<code>evidence-viewer</code> 和 <code>workflow-ui</code>；<code>domain-types</code> 包已经存在，静态盘点未发现当前直接消费关系。

### 02｜身份、租户与安全【已形成】

边缘入口执行 TLS、限流和身份头清洗。API 执行会话、角色、能力、租户和项目范围校验。PostgreSQL Row-Level Security（行级安全策略）执行数据库级租户隔离。安全投影、DLP、Vault 和审计分别处理字段暴露、敏感数据、密钥和操作记录。

### 03｜客户事实、项目与配置【基础已上线，统一配置目标持续建设】

客户填写或确认品牌、产品、材料、目标和授权范围。品牌公开调研 AI 生成结构化预填候选，客户或运营确认后形成项目事实版本。统一配置模型包含 Query、产品、<code>collection_surface</code>、产品变体、模式、31 个省级地域、每格重复次数、频率、时间窗、交付物和 SLA。

## B. 商业能力与执行控制

### 04｜报价与 Agent Skill【部分可用】

<code>geo-quotation</code> Agent Skill 生成报价单和报价说明书。<code>workflow-diagram</code> Agent Skill 生成流程图。Agent 负责理解资料和组织内容；价格规则、模板校验和文件渲染由确定性工具执行；运营人员批准事实、金额、服务范围和对外版本。报价生产模板当前等待 V2 批准。

### 05｜准入、任务与耐久编排【基础已形成，V2 分阶段接入】

执行准入同时检查报价、权益、配置和目标能力。显式 <code>collection_targets</code> 选择需要测量的产品与采集表面。系统按稳定顺序物化 campaign、target、sampling leg 和 primary slot，并使用 sample ordinal 形成稳定样本身份。Temporal、Scheduler、Outbox、typed grant、原子配额、lease 和 fencing 共同控制长任务与共享资源。

## C. 三表面采集、证据与分析

### 06｜三表面协作采集【Web 有基础，API/App 为目标】

<code>provider_api</code> 定义模型提供方 API 采集路径，Adapter 待接入。<code>consumer_web</code> 通过真实浏览器采集五个消费级 AI 网页，已经完成实现和历史实跑验收。<code>consumer_app</code> 定义真实设备与 App 会话采集路径，Adapter 待实现。每个表面拥有独立 sampling leg、业务键、任务、事实和分母。系统按请求表面执行；资源不可用时记录 blocker 并停止该 sampling leg。

### 07｜证据与 U/V/W 信源模型【部分可用】

U 表示 AI 回答中发现的引用。每次引用保留独立 Occurrence。V 表示该引用页面在本次获取中的可观察状态。W 表示页面正文对 AI 回答的内容贡献证据。当前 W 使用 <code>content-contribution-exact-v1</code> 严格文本匹配。URL 规范化、页面版本、截图、网络材料和内容哈希共同形成可追溯证据。

### 08｜AI 应用与并行分析【已有实现基础】

<code>answer.capture.completed</code> 表示指定采集表面的捕获事实已经保存。随后并行运行品牌/实体抽取、URL 与页面处理、风险/事实/关系候选、当前 W 计算和 ClickHouse 投影。运行时 AI 负责语义候选。确定性规则负责归一化、严格匹配、Schema、证据定位、阈值、统计和版本。

## D. 五项专业服务

### 09｜Service 1：推荐与排名【基础最成熟】

系统在冻结测量矩阵上采集回答。运行时 AI 抽取品牌候选。确定性规则完成品牌归一化、严格匹配和位置识别。指标包括提及率、95% 置信区间、平均/最佳名次、Top1/3/5、可见度、竞品对照和重复一致性。分析人员复核分母、抽取和代表案例后冻结服务事实。

### 10｜Service 2：全 U 主动贬损审计【新定义已冻结，工程重建中】

服务覆盖项目时间窗内可观察的全部 U Occurrence。相同 URL 可以复用页面快照，每次引用上下文保持独立。运行时 AI 提取说话者、目标对象和行为候选。数据模型分别记录负面表达、事实真实性和内容归属。分析人员逐案复核精确页面版本与引文。

### 11｜Service 3：品牌受影响与传播【现有基础可用，目标设计持续建设】

服务从品牌中心的匹配问题组出发，连接 AI 回答、U Occurrence 和页面版本三层证据。通道 A 使用实际被引用的 U。通道 B 使用客户授权的引用池外候选页面。A 陈述账本记录 AI 实际表达，B 曝光账本记录外部内容的传播机会。两类账本分别统计和审核。

### 12｜Service 4：官网 U/V/W 效率【部分可用】

服务把官网内容进入 AI 回答的过程分为 U 发现、V 可观察、W 正文采用和最终引用。文本采用比较先移除回答的参考文献区。20 个及以上规范化字符构成保守直接采用证据，10—19 个字符列为弱证据。U、可观察 V、W 和最终引用分别使用自己的分母。

### 13｜Service 5：内容发布与同条件复测【有限试点】

服务先冻结干预前的测量矩阵和获批内容版本。发布系统分别记录提交、审核、发布、公开访问和可检索状态。发布时间位于前测与后测窗口之间时，系统使用相同问题、产品、地区、模式和重复条件复测。报告输出描述性变化；因果判断需要独立实验设计。媒体目录约 120,841 行，一个供应商协议已经验证。

## E. 交付、数据与运行保障

### 14｜报告与客户交付【内部审核阶段】

五项服务分别审核并冻结事实。报告 AI 只读取冻结事实并生成叙事草稿。审核人员批准事实、措辞和版本。系统随后创建不可变报告版本，并确定性渲染 DOCX、PDF、XLSX 和证据包。安全客户投影过滤内部字段并控制下载权限。

### 15｜数据与事件一致性【已形成】

PostgreSQL 保存权威业务事实。MinIO/S3/CAS 保存不可变证据对象。ClickHouse 保存可由权威事实重建的分析投影。Redis 保存缓存、限流和短期协调状态。Temporal PostgreSQL 保存 Workflow 历史。Vault 保存密钥和敏感状态。Transactional Outbox 在业务事务中同步创建待投递事件。

### 16｜运行保障、SOP 与发布【已形成】

OpenTelemetry 收集链路数据。Prometheus、Loki、Alloy 和 Grafana 提供指标、日志和可视化。Alertmanager 触发告警。SOP 规定暂停、恢复、人工接管、校准和故障处理步骤。全量/增量备份、恢复演练、迁移预检、自动化测试、五前端原子发布和版本回滚共同支撑生产运行。

---

## 实现成熟度摘要

| 能力                             | 实现成熟度                     |
| -------------------------------- | ------------------------------ |
| 五个角色工作台                   | 【已上线】                     |
| 身份、租户、安全、数据与运行保障 | 【已形成】                     |
| consumer_web 五平台真实网页采集  | 【已实现并实跑】               |
| provider_api 模型 API 采集       | 【待接入】                     |
| consumer_app 真实 App 采集       | 【待实现】                     |
| Scheduler 与 Collection Worker   | 【已实现；真实网页采集已实跑】 |
| UVW 基础结构与视图               | 【部分完成】                   |
| Service 1                        | 【基础可用】                   |
| Service 2 新定义                 | 【重建中】                     |
| Service 3 新目标设计             | 【部分建设】                   |
| Service 4                        | 【部分可用】                   |
| Service 5                        | 【有限试点】                   |
| 正式报告生产链                   | 【内部审核阶段】               |
| 报价 Agent Skill                 | 【生产模板待批准】             |

## 资料来源

- [GEO 系统三种视角总说明](../../../developlog/system-pipeline/02-complete-geo-system-three-perspectives.md)
- [GEO 系统逐模块图谱](../../../developlog/system-pipeline/03-geo-system-module-atlas.md)
- [2026-08-24 GEO 系统客户架构审计](../../../developlog/implementation/GEO_SYSTEM_CLIENT_ARCHITECTURE_20260824.md)
- [三表面采集契约与 Collectors 冻结设计](../../../developlog/requirements/20260824-multi-session-optimization-prompts/03-three-surface-collection-contract-and-collectors.md)
