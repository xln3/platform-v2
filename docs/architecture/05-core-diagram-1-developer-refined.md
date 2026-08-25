# 核心图一精绘版：开发者视角的部署、安全域与运行组件

> 本文件单独迭代“核心图一”。它对应 [GEO 系统全景展示](./04-geo-system-showcase.md) 中的开发者视角总图。
>
> 版本基线：2026-08-25T13:07:27+08:00；<code>platform-v2</code> <code>master</code>；提交 <code>c6f53153f4a9ac44490850c74ef7124b05ac2916</code>；开发态工作树包含尚未提交内容。

## 图面规则

本版参考本机 <code>workflow-diagram</code> 技能的制图方法。主图使用固定列网格。局部放大图使用直角折线。主图内的组件、事实、证据和交付物全部使用直角矩形。底色与边框共同表达责任类型、系统边界和建设阶段。

主图使用以下视觉编码：

- 绿色实线：客户或受邀填报人。
- 橙色实线：运营、分析和审核人员。
- 深紫色短虚线：运营侧独立 AI 技能（Agent Skill）。
- 浅紫色点线：产品内运行时 AI。
- 蓝色细实线：确定性软件和运行组件。
- 灰色点线：外部系统和外部测量表面。
- 青色粗实线：持久化事实、证据、命令和交付物。
- 灰白色长虚线：分阶段能力或目标能力。

主图负责展示完整部署结构。主图通过固定列对齐和关系索引表达跨层协作，不绘制连接线。完整的逐节点依赖集中放在后面的跨层审计图和四张局部放大图中。各图使用一致的颜色。主图使用中文能力名；局部图保留实现名，便于开发者定位代码。

版面采用紧凑密度：主图网格间距为 1px，文字行高为 1.05，状态与组件名合并为单行；关系图节点内边距为 4px，同层间距为 10–12px，跨层间距为 20–24px。

## 主图：八层部署结构

~~~mermaid
%%{init: {
  "theme": "base",
  "block": {"padding": 1},
  "themeCSS": ".nodeLabel p { line-height: 1.05 !important; margin: 0 !important; } .label foreignObject div { line-height: 1.05 !important; }",
  "themeVariables": {
    "fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif",
    "fontSize": "13px",
    "lineColor": "#475569"
  }
}}%%
block-beta
    columns 18

    H1["01 使用人员与 AI 工具"]:2
    CUSTOMER["客户"]:3
    INVITEE["受邀填报人"]:3
    OPERATOR["运营人员"]:2
    QUOTATION_SKILL["AI 报价工具"]:2
    DIAGRAM_SKILL["AI 流程图工具"]:2
    ANALYST["分析人员"]:2
    REVIEWER["报告审核人员"]:2

    H2["02 使用入口与交付"]:2
    CUSTOMER_WEB["客户工作台"]:2
    TERMINAL_EXTENSION["浏览器扩展【部分完成】"]:2
    INTAKE_FORM["客户资料填报"]:2
    OPERATIONS_WEB["运营管理台"]:2
    COMMERCIAL_DOCS["报价单与报价说明书"]:2
    FLOW_DOC["流程图"]:2
    INTELLIGENCE_WEB["分析工作台"]:2
    REPORT_STUDIO["报告制作台"]:2

    H3A["03A 访问路由"]:2
    CUSTOMER_EDGE["客户路由策略"]:4
    AUXILIARY_EDGE["扩展程序路由"]:4
    INTAKE_EDGE["填报邀请路由"]:4
    MANAGEMENT_EDGE["内部管理路由"]:4

    H3B["03B 网络与权限"]:2
    TLS["加密访问入口"]:3
    ROUTING["域名与页面路由"]:3
    RATE_LIMIT["请求限流"]:3
    HEADER_CLEAN["身份信息清洗"]:3
    APPLICATION_AUTH["登录与权限校验"]:4

    H4["04 后端业务服务"]:2
    PROJECT_PLANE["项目、合同与报价"]:3
    COLLECTION_PLANE["问答采集与证据"]:3
    ANALYSIS_PLANE["分析与五项服务"]:3
    DELIVERY_PLANE["报告与客户数据"]:3
    API_APP["后端统一入口"]:4

    H5A["05A 任务指令与调度"]:2
    SCHEDULER["定时任务【已实现】"]:2
    SIGNAL_COMMAND["暂停、继续与取消指令"]:3
    DOMAIN_EVENT["业务事件"]:3
    TEMPORAL["工作流调度中心"]:3
    OUTBOX_WORKER["可靠任务派发"]:2
    START_COMMAND["启动任务指令"]:3

    H5B["05B 后台执行程序"]:2
    SOURCE_WORKER["引用网页抓取"]:4
    ANALYSIS_WORKER["分析计算"]:4
    COLLECTION_WORKER["网页问答采集【已实现并实跑】"]:4
    S02_WORKER["专业服务与报告生成"]:4

    H6A["06A 采集资源与程序"]:2
    PROVIDER_ADAPTER["模型接口采集器【待接入】"]:3
    APP_ADAPTER["手机应用采集器【待实现】"]:3
    TYPED_GRANT["新版授权【分阶段建设】"]:2
    V1_GOVERNANCE["现行账号与浏览器管理"]:2
    EXECUTION_CONTEXT["受控浏览器会话"]:3
    WEB_ADAPTER["真实网页采集器【五平台已实跑】"]:3

    H6B["06B 外部问答渠道"]:2
    MODEL_API["模型服务接口"]:2
    CONSUMER_APP["消费级 AI 手机应用"]:2
    PUBLIC_WEB["引用页面与客户官网"]:2
    RUNTIME_AI["产品内 AI 处理"]:2
    MODEL_GATEWAY["模型调用控制"]:2
    RUNTIME_MODEL["外部 AI 模型服务"]:3
    CONSUMER_WEB["消费级 AI 网页"]:3

    H7["07 数据与证据"]:2
    OBJECT_STORE["不可变证据库"]:2
    CLICKHOUSE["统计分析库"]:2
    REDIS["缓存与短期协调"]:2
    TEMPORAL_PG["工作流历史库"]:2
    VAULT["密钥与敏感数据加密"]:2
    RLS["业务数据按客户隔离"]:2
    POSTGRES["业务事实库"]:2
    SAFE_PROJECTION["客户可见数据"]:2

    H8["08 运行保障"]:2
    TRACE_PIPELINE["请求过程监测"]:2
    METRIC_LOG_PIPELINE["指标与日志"]:2
    ALERT_PIPELINE["告警与飞书通知"]:3
    BACKUP_PIPELINE["备份与恢复演练"]:3
    RELEASE_PIPELINE["测试、发布与回滚"]:3
    MEDIA_REFRESH["媒体价格更新"]:3

    classDef header fill:#dbeafe,stroke:#1e3a8a,color:#0f172a,stroke-width:2.4px;
    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef skill fill:#f3e8ff,stroke:#7e22ce,color:#581c87,stroke-width:2.2px,stroke-dasharray:6 3;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px,stroke-dasharray:2 2;
    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.6px;
    classDef external fill:#f8fafc,stroke:#64748b,color:#1f2937,stroke-width:1.6px,stroke-dasharray:2 2;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2.6px;
    classDef target fill:#ffffff,stroke:#64748b,color:#334155,stroke-width:1.8px,stroke-dasharray:8 4;

    class H1,H2,H3A,H3B,H4,H5A,H5B,H6A,H6B,H7,H8 header
    class CUSTOMER,INVITEE customer
    class OPERATOR,ANALYST,REVIEWER human
    class QUOTATION_SKILL,DIAGRAM_SKILL skill
    class RUNTIME_AI runtime
    class MODEL_API,CONSUMER_WEB,PUBLIC_WEB,CONSUMER_APP,RUNTIME_MODEL external
    class TYPED_GRANT,PROVIDER_ADAPTER,APP_ADAPTER,TERMINAL_EXTENSION target
    class COMMERCIAL_DOCS,FLOW_DOC,START_COMMAND,SIGNAL_COMMAND,DOMAIN_EVENT,POSTGRES,OBJECT_STORE,CLICKHOUSE,REDIS,TEMPORAL_PG,SAFE_PROJECTION fact
    class CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO,CUSTOMER_EDGE,INTAKE_EDGE,MANAGEMENT_EDGE,AUXILIARY_EDGE,TLS,ROUTING,RATE_LIMIT,HEADER_CLEAN,APPLICATION_AUTH,API_APP,PROJECT_PLANE,COLLECTION_PLANE,ANALYSIS_PLANE,DELIVERY_PLANE,SCHEDULER,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,V1_GOVERNANCE,EXECUTION_CONTEXT,WEB_ADAPTER,MODEL_GATEWAY,RLS,VAULT,TRACE_PIPELINE,METRIC_LOG_PIPELINE,ALERT_PIPELINE,BACKUP_PIPELINE,RELEASE_PIPELINE,MEDIA_REFRESH software

~~~

主图不绘制箭头。每一行表示一个部署层，节点的固定列位置帮助识别上下游区域。下表用相同组件名展开关键关系；逐节点调用由后面的审计图和局部放大图表示。

### 主图关系索引

| 关系 | 使用者与入口 | 网络与业务服务 | 调度与执行 | 外部资源或 AI | 事实与交付 |
| --- | --- | --- | --- | --- | --- |
| 客户访问与数据发布 | 客户；客户工作台 | 客户路由；加密入口；登录与权限；后端统一入口 | 客户数据发布 | — | 业务事实库；客户可见数据 |
| 受邀资料填报 | 受邀填报人；客户资料填报 | 填报邀请路由；登录与权限；项目、合同与报价 | 业务事件 | — | 业务事实库 |
| 商业制品生产 | 运营人员；AI 报价工具；AI 流程图工具 | 项目、合同与报价 | 人工批准与确定性渲染 | 独立 AI 技能 | 报价单；报价说明书；流程图 |
| 真实网页问答采集 | 运营管理台；问答采集与证据 | 启动任务指令；可靠任务派发；工作流调度中心 | 网页问答采集；账号与浏览器管理；受控浏览器会话 | 真实网页采集器；消费级 AI 网页 | 回答事实；不可变证据 |
| 引用网页与智能分析 | 分析工作台；分析与五项服务 | 工作流调度中心 | 引用网页抓取；分析计算 | 引用页面；产品内 AI；模型调用控制；外部 AI 模型 | 统计分析库；不可变证据 |
| 报告与专业服务 | 报告制作台；报告与客户数据 | 工作流调度中心 | 专业服务与报告生成 | 产品内 AI | 冻结服务事实；正式报告；客户可见数据 |
| 运行保障 | 全部入口和后台组件 | 请求过程监测 | 指标、日志、告警、备份、发布与回滚 | 飞书通知 | 运行记录；恢复材料；发布版本 |

状态说明：图中状态只描述实现成熟度。`【已实现】`表示已有可运行代码与部署入口；`【已实现并实跑】`表示实现链路已有真实运行证据；`【五平台已实跑】`表示豆包、DeepSeek、文心、通义和元宝都留有真实网页问答运行证据，各平台成熟度不同；`【分阶段建设】`、`【待接入】`和`【待实现】`表示后续建设边界。

<details>
<summary>展开跨层全连接审计图</summary>

~~~mermaid
%%{init: {
  "theme": "base",
  "flowchart": {
    "curve": "stepAfter",
    "htmlLabels": true,
    "diagramPadding": 4,
    "padding": 4,
    "nodeSpacing": 12,
    "rankSpacing": 22,
    "subGraphTitleMargin": {"top": 4, "bottom": 4}
  },
  "themeVariables": {
    "fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif",
    "fontSize": "14px",
    "lineColor": "#475569",
    "clusterBkg": "#ffffff",
    "clusterBorder": "#94a3b8"
  }
}}%%
flowchart TB
    subgraph L1["01　访问主体与独立运营工具"]
        direction LR
        CUSTOMER["客户"]
        INVITEE["受邀填报人"]
        OPERATOR["运营人员"]
        ANALYST["分析人员"]
        REVIEWER["报告审核人员"]
        QUOTATION_SKILL["geo-quotation<br/>Agent Skill"]
        DIAGRAM_SKILL["workflow-diagram<br/>Agent Skill"]
        COMMERCIAL_DOCS[("报价单与报价说明书")]
        FLOW_DOC[("流程图")]
    end

    subgraph L2["02　角色化产品入口"]
        direction LR
        CUSTOMER_WEB["customer-web"]
        INTAKE_FORM["intake-form"]
        OPERATIONS_WEB["operations-web"]
        INTELLIGENCE_WEB["intelligence-web"]
        REPORT_STUDIO["report-studio"]
        TERMINAL_EXTENSION["customer-terminal-extension<br/>【部分完成】"]
    end

    subgraph L3["03　边缘入口与逐级授权"]
        direction LR
        CUSTOMER_EDGE["客户路由策略"]
        INTAKE_EDGE["邀请路由策略"]
        MANAGEMENT_EDGE["管理路由策略"]
        AUXILIARY_EDGE["辅助路由策略"]
        NGINX_GUARD["Nginx 边缘保护"]
        AUTHENTICATION["会话与一次性凭证校验"]
        AUTHORIZATION["角色与能力授权"]
        SCOPE_CHECK["租户与项目范围校验"]
    end

    subgraph L4["04　FastAPI 模块化业务后端"]
        direction LR
        API_APP["FastAPI 应用边界"]
        PROJECT_PLANE["项目与商业控制平面"]
        COLLECTION_PLANE["采集与证据平面"]
        ANALYSIS_PLANE["分析与五项服务平面"]
        DELIVERY_PLANE["报告与客户投影平面"]
    end

    subgraph L5["05　耐久命令与独立运行进程"]
        direction LR
        SCHEDULER["Scheduler<br/>【已实现】"]
        DURABLE_COMMANDS[("PostgreSQL 耐久命令与事件")]
        OUTBOX_WORKER["Outbox Worker"]
        TEMPORAL["Temporal Server"]
        COLLECTION_WORKER["Collection Worker<br/>【已实现并实跑】"]
        SOURCE_WORKER["Source Worker"]
        ANALYSIS_WORKER["Analysis Worker"]
        S02_WORKER["S02 Worker"]
    end

    subgraph L6["06　受控自动化与外部测量表面"]
        direction LR
        V1_GOVERNANCE["v1 资源治理<br/>【现行路径】"]
        TYPED_GRANT["Typed Grant<br/>【V2 分阶段接入】"]
        EXECUTION_CONTEXT["配额、Lease 与 Fencing<br/>形成受控执行上下文"]

        PROVIDER_ADAPTER["provider_api Adapter<br/>【待接入】"]
        MODEL_API["模型提供方 API"]

        WEB_ADAPTER["consumer_web Adapter<br/>【五平台已实跑】"]
        CONSUMER_WEB["消费级 AI 真实网页"]

        APP_ADAPTER["consumer_app Adapter<br/>【待实现】"]
        CONSUMER_APP["消费级 AI 真实 App"]

        PUBLIC_WEB["引用页面与客户官网"]

        RUNTIME_AI["产品内运行时 AI"]
        MODEL_GATEWAY["受控模型调用"]
        RUNTIME_MODEL["外部运行时模型端点"]
    end

    subgraph L7["07　权威事实、证据与运行状态"]
        direction LR
        RLS["PostgreSQL RLS"]
        POSTGRES[("PostgreSQL<br/>权威业务事实")]
        OBJECT_STORE[("MinIO 或 S3 CAS<br/>不可变证据")]
        CLICKHOUSE[("ClickHouse<br/>可重建投影")]
        REDIS[("Redis<br/>缓存与短期协调")]
        TEMPORAL_PG[("Temporal PostgreSQL<br/>Workflow 历史")]
        VAULT["Vault Transit"]
        SAFE_PROJECTION[("安全客户投影")]
    end

    subgraph L8["08　可观测、通知、恢复与发布"]
        direction LR
        TRACE_PIPELINE["OTel 链路管线"]
        METRIC_LOG_PIPELINE["指标与日志管线"]
        ALERT_PIPELINE["告警与飞书通知管线"]
        BACKUP_PIPELINE["备份与恢复演练"]
        RELEASE_PIPELINE["测试、发布与回滚"]
        MEDIA_REFRESH["Media Price Refresh Worker"]
    end

    CUSTOMER --> CUSTOMER_WEB
    CUSTOMER --> TERMINAL_EXTENSION
    INVITEE --> INTAKE_FORM
    OPERATOR --> OPERATIONS_WEB
    ANALYST --> INTELLIGENCE_WEB
    REVIEWER --> REPORT_STUDIO
    OPERATOR -->|独立调用| QUOTATION_SKILL
    OPERATOR -->|独立调用| DIAGRAM_SKILL
    QUOTATION_SKILL ==>|生成待批准制品| COMMERCIAL_DOCS
    DIAGRAM_SKILL ==>|生成待批准制品| FLOW_DOC

    CUSTOMER_WEB --> CUSTOMER_EDGE
    INTAKE_FORM --> INTAKE_EDGE
    OPERATIONS_WEB --> MANAGEMENT_EDGE
    INTELLIGENCE_WEB --> MANAGEMENT_EDGE
    REPORT_STUDIO --> MANAGEMENT_EDGE
    TERMINAL_EXTENSION --> AUXILIARY_EDGE
    CUSTOMER_EDGE --> NGINX_GUARD
    INTAKE_EDGE --> NGINX_GUARD
    MANAGEMENT_EDGE --> NGINX_GUARD
    AUXILIARY_EDGE --> NGINX_GUARD
    NGINX_GUARD --> AUTHENTICATION --> AUTHORIZATION --> SCOPE_CHECK --> API_APP

    API_APP --> PROJECT_PLANE
    API_APP --> COLLECTION_PLANE
    API_APP --> ANALYSIS_PLANE
    API_APP --> DELIVERY_PLANE
    PROJECT_PLANE ==>|冻结项目与执行条件| COLLECTION_PLANE
    COLLECTION_PLANE ==>|回答与证据事实| ANALYSIS_PLANE
    ANALYSIS_PLANE ==>|冻结服务事实| DELIVERY_PLANE

    PROJECT_PLANE ==>|写命令| DURABLE_COMMANDS
    COLLECTION_PLANE ==>|写命令与事件| DURABLE_COMMANDS
    ANALYSIS_PLANE ==>|写分析命令| DURABLE_COMMANDS
    DELIVERY_PLANE ==>|写报告命令| DURABLE_COMMANDS
    SCHEDULER ==>|到期物化命令| DURABLE_COMMANDS
    DURABLE_COMMANDS --> OUTBOX_WORKER
    OUTBOX_WORKER -->|启动或通知 Workflow| TEMPORAL
    TEMPORAL --> COLLECTION_WORKER
    TEMPORAL --> SOURCE_WORKER
    TEMPORAL --> ANALYSIS_WORKER
    TEMPORAL --> S02_WORKER

    COLLECTION_WORKER --> V1_GOVERNANCE --> EXECUTION_CONTEXT
    COLLECTION_WORKER -.-> TYPED_GRANT
    TYPED_GRANT -.-> EXECUTION_CONTEXT
    EXECUTION_CONTEXT -.-> PROVIDER_ADAPTER -.-> MODEL_API
    EXECUTION_CONTEXT --> WEB_ADAPTER --> CONSUMER_WEB
    EXECUTION_CONTEXT -.-> APP_ADAPTER -.-> CONSUMER_APP
    SOURCE_WORKER --> PUBLIC_WEB

    PROJECT_PLANE -->|调研与 Query 候选| RUNTIME_AI
    ANALYSIS_WORKER -->|实体、风险与关系候选| RUNTIME_AI
    DELIVERY_PLANE -->|报告叙事草稿| RUNTIME_AI
    RUNTIME_AI --> MODEL_GATEWAY --> RUNTIME_MODEL

    API_APP --> RLS --> POSTGRES
    API_APP --> REDIS
    API_APP --> VAULT
    COLLECTION_WORKER ==>|回答事实| POSTGRES
    COLLECTION_WORKER ==>|表面原生证据| OBJECT_STORE
    COLLECTION_WORKER --> VAULT
    SOURCE_WORKER ==>|页面事实| POSTGRES
    SOURCE_WORKER ==>|页面证据| OBJECT_STORE
    ANALYSIS_WORKER ==>|候选与分析版本| POSTGRES
    S02_WORKER ==>|冻结事实与报告版本| POSTGRES
    S02_WORKER ==>|报告与证据包| OBJECT_STORE
    OUTBOX_WORKER ==>|幂等分析投影| CLICKHOUSE
    TEMPORAL ==>|保存耐久历史| TEMPORAL_PG
    POSTGRES ==>|允许字段| SAFE_PROJECTION

    API_APP -.-> TRACE_PIPELINE
    OUTBOX_WORKER -.-> TRACE_PIPELINE
    COLLECTION_WORKER -.-> TRACE_PIPELINE
    SOURCE_WORKER -.-> TRACE_PIPELINE
    ANALYSIS_WORKER -.-> TRACE_PIPELINE
    S02_WORKER -.-> TRACE_PIPELINE
    TRACE_PIPELINE --> METRIC_LOG_PIPELINE --> ALERT_PIPELINE
    POSTGRES --> BACKUP_PIPELINE
    OBJECT_STORE --> BACKUP_PIPELINE
    CLICKHOUSE --> BACKUP_PIPELINE
    RELEASE_PIPELINE -.-> API_APP
    RELEASE_PIPELINE -.-> COLLECTION_WORKER
    API_APP ==>|创建耐久刷新请求| MEDIA_REFRESH

    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef skill fill:#f4ebff,stroke:#7e22ce,color:#581c87,stroke-width:2px;
    classDef runtime fill:#ede9fe,stroke:#8b5cf6,color:#4c1d95,stroke-width:2px;
    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.7px;
    classDef external fill:#f8fafc,stroke:#64748b,color:#1f2937,stroke-width:1.7px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2px;
    classDef target fill:#f8fafc,stroke:#64748b,color:#334155,stroke-width:1.7px,stroke-dasharray:7 5;

    class CUSTOMER,INVITEE customer;
    class OPERATOR,ANALYST,REVIEWER human;
    class QUOTATION_SKILL,DIAGRAM_SKILL skill;
    class RUNTIME_AI runtime;
    class MODEL_API,CONSUMER_WEB,CONSUMER_APP,PUBLIC_WEB,RUNTIME_MODEL external;
    class PROVIDER_ADAPTER,APP_ADAPTER,TYPED_GRANT,TERMINAL_EXTENSION target;
    class COMMERCIAL_DOCS,FLOW_DOC,DURABLE_COMMANDS,POSTGRES,OBJECT_STORE,CLICKHOUSE,REDIS,TEMPORAL_PG,SAFE_PROJECTION fact;
    class CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO,CUSTOMER_EDGE,INTAKE_EDGE,MANAGEMENT_EDGE,AUXILIARY_EDGE,NGINX_GUARD,AUTHENTICATION,AUTHORIZATION,SCOPE_CHECK,API_APP,PROJECT_PLANE,COLLECTION_PLANE,ANALYSIS_PLANE,DELIVERY_PLANE,SCHEDULER,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,V1_GOVERNANCE,EXECUTION_CONTEXT,WEB_ADAPTER,MODEL_GATEWAY,RLS,VAULT,TRACE_PIPELINE,METRIC_LOG_PIPELINE,ALERT_PIPELINE,BACKUP_PIPELINE,RELEASE_PIPELINE,MEDIA_REFRESH software;

    style L1 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L2 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L3 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L4 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L5 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L6 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L7 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style L8 fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
~~~

</details>

### 主图阅读顺序

1. 角色通过对应工作台进入四类路由策略。
2. Nginx 和 API 依次完成边缘保护、身份校验、能力授权和范围校验。
3. FastAPI 内部的四个业务平面通过项目事实、回答证据、冻结服务事实和交付版本协作。
4. API 与 Scheduler 把执行意图写成 PostgreSQL 耐久命令。Outbox Worker 将命令派发给 Temporal。
5. Temporal 把任务路由到四类独立 Worker。Collection Worker 已实现，并留有真实网页采集的运行证据。
6. Collection Worker 通过现行 v1 资源治理访问五个平台均已实跑的真实网页采集器。Typed Grant 表示 V2 分阶段强化路径。
7. 模型 API 采集器待接入，真实 App 采集器待实现；真实网页采集链已经完成代码实现和历史实跑验收。
8. PostgreSQL、对象存储、ClickHouse、Redis、Temporal PostgreSQL 和 Vault 分别保存不同类型的事实与运行状态。

## 局部放大 A：入口、安全步骤与 FastAPI 边界

~~~mermaid
%%{init: {
  "theme": "base",
  "flowchart": {"curve": "stepAfter", "htmlLabels": true, "diagramPadding": 4, "padding": 4, "nodeSpacing": 12, "rankSpacing": 24, "subGraphTitleMargin": {"top": 4, "bottom": 4}},
  "themeVariables": {"fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif", "fontSize": "14px", "lineColor": "#475569"}
}}%%
flowchart TB
    subgraph ACTORS["01　访问主体"]
        direction LR
        CUSTOMER["客户"]
        INVITEE["受邀填报人"]
        OPERATOR["运营人员"]
        ANALYST["分析人员"]
        REVIEWER["报告审核人员"]
    end

    subgraph PRODUCTS["02　产品入口"]
        direction LR
        CUSTOMER_WEB["customer-web"]
        INTAKE_FORM["intake-form"]
        OPERATIONS_WEB["operations-web"]
        INTELLIGENCE_WEB["intelligence-web"]
        REPORT_STUDIO["report-studio"]
        TERMINAL_EXTENSION["customer-terminal-extension<br/>【部分完成】"]
    end

    subgraph ROUTE_POLICIES["03　路由策略"]
        direction LR
        CUSTOMER_EDGE["客户路由策略"]
        INTAKE_EDGE["邀请路由策略"]
        MANAGEMENT_EDGE["管理路由策略"]
        AUXILIARY_EDGE["辅助路由策略"]
    end

    subgraph NGINX_STEPS["04　Nginx 共同保护步骤"]
        direction LR
        TLS["TLS 终止"]
        ROUTING["域名与路径路由"]
        RATE_LIMIT["请求限流"]
        HEADER_CLEAN["身份头清洗"]
    end

    subgraph AUTH_STEPS["05　应用授权步骤"]
        direction LR
        SESSION_AUTH["会话鉴权"]
        OTP_TICKET["一次性 OTP 票据"]
        SIGNED_TASK["终端签名任务"]
        RBAC["角色授权"]
        CAPABILITY["能力授权"]
        TENANT_SCOPE["租户范围"]
        PROJECT_SCOPE["项目范围"]
        API_APP["FastAPI 应用边界"]
    end

    CUSTOMER --> CUSTOMER_WEB
    CUSTOMER --> TERMINAL_EXTENSION
    INVITEE --> INTAKE_FORM
    OPERATOR --> OPERATIONS_WEB
    ANALYST --> INTELLIGENCE_WEB
    REVIEWER --> REPORT_STUDIO

    CUSTOMER_WEB --> CUSTOMER_EDGE
    INTAKE_FORM --> INTAKE_EDGE
    OPERATIONS_WEB --> MANAGEMENT_EDGE
    INTELLIGENCE_WEB --> MANAGEMENT_EDGE
    REPORT_STUDIO --> MANAGEMENT_EDGE
    TERMINAL_EXTENSION --> AUXILIARY_EDGE

    CUSTOMER_EDGE --> TLS
    INTAKE_EDGE --> TLS
    MANAGEMENT_EDGE --> TLS
    AUXILIARY_EDGE --> TLS
    TLS --> ROUTING --> RATE_LIMIT --> HEADER_CLEAN
    HEADER_CLEAN -->|工作台请求| SESSION_AUTH
    HEADER_CLEAN -->|OTP 接管请求| OTP_TICKET
    HEADER_CLEAN -->|终端扩展请求| SIGNED_TASK
    SESSION_AUTH --> RBAC --> CAPABILITY
    OTP_TICKET --> CAPABILITY
    SIGNED_TASK --> CAPABILITY
    CAPABILITY --> TENANT_SCOPE --> PROJECT_SCOPE --> API_APP

    classDef customer fill:#ecfdf3,stroke:#16a34a,color:#14532d,stroke-width:2px;
    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.7px;
    classDef target fill:#f8fafc,stroke:#64748b,color:#334155,stroke-width:1.7px,stroke-dasharray:7 5;
    class CUSTOMER,INVITEE customer;
    class OPERATOR,ANALYST,REVIEWER human;
    class TERMINAL_EXTENSION target;
    class CUSTOMER_WEB,INTAKE_FORM,OPERATIONS_WEB,INTELLIGENCE_WEB,REPORT_STUDIO,CUSTOMER_EDGE,INTAKE_EDGE,MANAGEMENT_EDGE,AUXILIARY_EDGE,TLS,ROUTING,RATE_LIMIT,HEADER_CLEAN,SESSION_AUTH,OTP_TICKET,SIGNED_TASK,RBAC,CAPABILITY,TENANT_SCOPE,PROJECT_SCOPE,API_APP software;

    style ACTORS fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style PRODUCTS fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style ROUTE_POLICIES fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style NGINX_STEPS fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style AUTH_STEPS fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
~~~

## 局部放大 B：25 个后端模块如何传递事实

~~~mermaid
%%{init: {
  "theme": "base",
  "flowchart": {"curve": "stepAfter", "htmlLabels": true, "diagramPadding": 4, "padding": 4, "nodeSpacing": 10, "rankSpacing": 20, "subGraphTitleMargin": {"top": 4, "bottom": 4}},
  "themeVariables": {"fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif", "fontSize": "14px", "lineColor": "#475569"}
}}%%
flowchart TB
    API_APP["FastAPI 应用边界"]

    subgraph UPPER["控制与采集"]
        direction LR

        subgraph PROJECT_CONTROL["项目与商业控制"]
            direction TB
            IDENTITY["identity"]
            TENANCY["tenancy"]
            INTAKE_FORM_API["intake_form"]
            INTAKE["intake"]
            PROJECTS["projects"]
            QUOTATIONS["quotations"]
            VARIANTS["variants"]
            ADMISSION["配置冻结与执行准入"]

            IDENTITY -->|建立主体| TENANCY
            TENANCY -->|限定归属| PROJECTS
            INTAKE_FORM_API -->|提交邀请表单| INTAKE
            INTAKE -->|形成确认事实| PROJECTS
            PROJECTS -->|定义服务范围| QUOTATIONS
            PROJECTS -->|提供问题基线| VARIANTS
            PROJECTS -->|提供授权范围| ADMISSION
            QUOTATIONS -->|提供权益与价格约束| ADMISSION
            VARIANTS -->|提供冻结问题集合| ADMISSION
        end

        subgraph COLLECTION_EVIDENCE["采集与证据"]
            direction TB
            COLLECTION["collection"]
            DATASETS["datasets"]
            EVIDENCE["evidence"]

            COLLECTION -->|组织运行与回答| DATASETS
            DATASETS -->|关联原始回答| EVIDENCE
        end
    end

    subgraph LOWER["分析与交付"]
        direction LR

        subgraph ANALYSIS_SERVICES["分析与五项服务生产"]
            direction TB
            SOURCE_INTELLIGENCE["source_intelligence"]
            INTELLIGENCE["intelligence"]
            SOURCE_ANALYSIS["source_analysis"]
            ANALYTICS["analytics"]
            BRANDRANK["brandrank"]
            SERVICE2_CORPUS["service2_corpus<br/>【重建中】"]
            POSTING["posting"]
            POST_ANALYSIS["post_analysis"]

            SOURCE_INTELLIGENCE -->|形成研究对象| INTELLIGENCE
            INTELLIGENCE -->|提交分析版本| SOURCE_ANALYSIS
            POSTING -->|提供发布事实| POST_ANALYSIS
        end

        subgraph DELIVERY_COLLAB["交付与协作"]
            direction TB
            REPORTS["reports"]
            EXPORTS["exports"]
            CUSTOMER_SERVICES["customer_services"]
            CUSTOMER_DASHBOARD["customer_dashboard"]
            NOTIFICATIONS["notifications<br/>含独立 Feishu Bot"]
            OTP["otp"]
            SOP["sop"]

            REPORTS -->|冻结交付版本| EXPORTS
            REPORTS -->|生成安全服务视图| CUSTOMER_SERVICES
            NOTIFICATIONS -.->|发起人工接管| OTP
            SOP -.->|约束审核与发布| REPORTS
        end
    end

    API_APP -.->|挂载路由集合| PROJECT_CONTROL
    API_APP -.->|挂载路由集合| COLLECTION_EVIDENCE
    API_APP -.->|挂载路由集合| ANALYSIS_SERVICES
    API_APP -.->|挂载路由集合| DELIVERY_COLLAB

    ADMISSION -->|准入通过| COLLECTION
    EVIDENCE -->|引用事实| SOURCE_INTELLIGENCE
    EVIDENCE -->|测量事实| ANALYTICS
    EVIDENCE -->|回答样本| BRANDRANK
    EVIDENCE -->|全 U 重建输入| SERVICE2_CORPUS

    ANALYTICS -->|服务指标事实| REPORTS
    BRANDRANK -->|服务一事实| REPORTS
    SERVICE2_CORPUS -->|服务二迁移与生产事实| REPORTS
    SOURCE_ANALYSIS -->|服务三与服务四事实| REPORTS
    POST_ANALYSIS -->|服务五事实| REPORTS
    ANALYTICS -->|趋势投影| CUSTOMER_DASHBOARD
    PROJECTS -->|项目投影| CUSTOMER_DASHBOARD
    COLLECTION -.->|异常与状态事件| NOTIFICATIONS
    REPORTS -.->|审核与发布事件| NOTIFICATIONS
    SOP -.->|约束暂停与恢复| COLLECTION

    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.7px;
    classDef status fill:#fff7ed,stroke:#d97706,color:#78350f,stroke-width:1.7px;
    class API_APP,IDENTITY,TENANCY,INTAKE_FORM_API,INTAKE,PROJECTS,QUOTATIONS,VARIANTS,ADMISSION,COLLECTION,DATASETS,EVIDENCE,SOURCE_INTELLIGENCE,INTELLIGENCE,SOURCE_ANALYSIS,ANALYTICS,BRANDRANK,POSTING,POST_ANALYSIS,REPORTS,EXPORTS,CUSTOMER_SERVICES,CUSTOMER_DASHBOARD,NOTIFICATIONS,OTP,SOP software;
    class SERVICE2_CORPUS status;

    style UPPER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style LOWER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style PROJECT_CONTROL fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style COLLECTION_EVIDENCE fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style ANALYSIS_SERVICES fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style DELIVERY_COLLAB fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
~~~

## 局部放大 C：命令、Worker、三类采集表面与数据归属

~~~mermaid
%%{init: {
  "theme": "base",
  "flowchart": {"curve": "stepAfter", "htmlLabels": true, "diagramPadding": 4, "padding": 4, "nodeSpacing": 12, "rankSpacing": 24, "subGraphTitleMargin": {"top": 4, "bottom": 4}},
  "themeVariables": {"fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif", "fontSize": "14px", "lineColor": "#475569"}
}}%%
flowchart TB
    subgraph COMMAND_LAYER["01　耐久命令"]
        direction LR
        API_PRODUCERS["业务 API"]
        SCHEDULER["Scheduler<br/>【已实现】"]
        START_COMMAND[("workflow_start_command")]
        SIGNAL_COMMAND[("workflow_signal_command")]
        DOMAIN_EVENT[("outbox_event")]
        OUTBOX_WORKER["Outbox Worker"]

        API_PRODUCERS ==>|同一事务写| START_COMMAND
        API_PRODUCERS ==>|同一事务写| SIGNAL_COMMAND
        API_PRODUCERS ==>|同一事务写| DOMAIN_EVENT
        SCHEDULER ==>|到期物化| START_COMMAND
        START_COMMAND -->|领取| OUTBOX_WORKER
        SIGNAL_COMMAND -->|领取| OUTBOX_WORKER
        DOMAIN_EVENT -->|领取| OUTBOX_WORKER
    end

    subgraph WORKER_LAYER["02　Temporal 与独立 Worker"]
        direction LR
        TEMPORAL["Temporal Server"]
        COLLECTION_WORKER["Collection Worker<br/>【已实现并实跑】"]
        SOURCE_WORKER["Source Worker"]
        ANALYSIS_WORKER["Analysis Worker"]
        S02_WORKER["S02 Worker"]
    end

    OUTBOX_WORKER -->|启动或通知 Workflow| TEMPORAL
    TEMPORAL -->|登录态采集队列| COLLECTION_WORKER
    TEMPORAL -->|公开页面队列| SOURCE_WORKER
    TEMPORAL -->|语义分析队列| ANALYSIS_WORKER
    TEMPORAL -->|证据与报告队列| S02_WORKER

    subgraph CONTROL_LAYER["03　资源治理与受控执行上下文"]
        direction LR
        V1_GATE["v1 账号、会话与浏览器校验<br/>【现行路径】"]
        V2_GRANT["Typed Grant<br/>【V2 分阶段接入】"]
        QUOTA["原子配额"]
        LEASE["资源 Lease"]
        FENCE["Fencing Token"]
        EXECUTION_CONTEXT["受控执行上下文"]

        V1_GATE --> QUOTA
        V1_GATE --> LEASE
        V2_GRANT -.-> QUOTA
        V2_GRANT -.-> LEASE
        LEASE --> FENCE
        QUOTA --> EXECUTION_CONTEXT
        FENCE --> EXECUTION_CONTEXT
    end

    COLLECTION_WORKER -->|申请现行资源| V1_GATE
    COLLECTION_WORKER -.->|V2 分阶段路径| V2_GRANT

    subgraph SURFACE_LAYER["04　三类采集表面与公开页面"]
        direction LR

        subgraph PROVIDER_SURFACE["模型 API"]
            direction TB
            API_CREDENTIAL["模型 API 凭据"]
            PROVIDER_ADAPTER["provider_api Adapter<br/>【待接入】"]
            MODEL_API["模型提供方 API"]
            API_CREDENTIAL -.-> PROVIDER_ADAPTER
            PROVIDER_ADAPTER -.-> MODEL_API
        end

        subgraph WEB_SURFACE["消费级 AI 真实网页"]
            direction TB
            ACCOUNT["已验证平台账号"]
            BROWSER_ROUTER["Browser Router"]
            RESIDENT_BROWSER["browser@常驻浏览器"]
            PROXY_RELAY["proxy-relay@地域"]
            WEB_ADAPTER["consumer_web Adapter<br/>【五平台已实跑】"]
            CONSUMER_WEB["五个平台真实网页"]
            HUMAN_TAKEOVER["人工同会话接管"]

            ACCOUNT --> BROWSER_ROUTER
            BROWSER_ROUTER --> RESIDENT_BROWSER
            PROXY_RELAY --> RESIDENT_BROWSER
            RESIDENT_BROWSER -->|CDP attach| WEB_ADAPTER
            WEB_ADAPTER --> CONSUMER_WEB
            WEB_ADAPTER -.->|登录或 CAPTCHA| HUMAN_TAKEOVER
            HUMAN_TAKEOVER -.->|恢复原上下文| WEB_ADAPTER
        end

        subgraph APP_SURFACE["消费级 AI 真实 App"]
            direction TB
            APP_DEVICE["真实移动设备<br/>【待实现】"]
            APP_SESSION["App 会话<br/>【待实现】"]
            APP_ADAPTER["consumer_app Adapter<br/>【待实现】"]
            CONSUMER_APP["消费级 AI 真实 App"]
            APP_DEVICE -.-> APP_ADAPTER
            APP_SESSION -.-> APP_ADAPTER
            APP_ADAPTER -.-> CONSUMER_APP
        end

        subgraph PUBLIC_SURFACE["公开页面"]
            direction TB
            PUBLIC_FETCH["公开页面获取"]
            PUBLIC_WEB["引用页面与客户官网"]
            PUBLIC_FETCH --> PUBLIC_WEB
        end
    end

    EXECUTION_CONTEXT -.-> PROVIDER_ADAPTER
    EXECUTION_CONTEXT --> WEB_ADAPTER
    EXECUTION_CONTEXT -.-> APP_ADAPTER
    SOURCE_WORKER --> PUBLIC_FETCH

    subgraph DATA_LAYER["05　数据责任"]
        direction LR
        POSTGRES[("PostgreSQL<br/>权威业务事实")]
        OBJECT_STORE[("MinIO 或 S3 CAS<br/>不可变证据")]
        CLICKHOUSE[("ClickHouse<br/>可重建投影")]
        TEMPORAL_PG[("Temporal PostgreSQL<br/>Workflow 历史")]
        VAULT["Vault Transit"]
    end

    COLLECTION_WORKER ==>|回答与运行事实| POSTGRES
    COLLECTION_WORKER ==>|表面原生证据| OBJECT_STORE
    COLLECTION_WORKER -->|解封采集凭据| VAULT
    SOURCE_WORKER ==>|URL 与页面版本| POSTGRES
    SOURCE_WORKER ==>|页面与截图证据| OBJECT_STORE
    ANALYSIS_WORKER ==>|候选与分析版本| POSTGRES
    S02_WORKER ==>|冻结事实与报告版本| POSTGRES
    S02_WORKER ==>|报告与证据包| OBJECT_STORE
    OUTBOX_WORKER ==>|按 event_id 幂等投影| CLICKHOUSE
    TEMPORAL ==>|保存耐久历史| TEMPORAL_PG

    classDef human fill:#fff7e6,stroke:#d97706,color:#78350f,stroke-width:2px;
    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.7px;
    classDef external fill:#f8fafc,stroke:#64748b,color:#1f2937,stroke-width:1.7px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2px;
    classDef target fill:#f8fafc,stroke:#64748b,color:#334155,stroke-width:1.7px,stroke-dasharray:7 5;
    class HUMAN_TAKEOVER human;
    class MODEL_API,CONSUMER_WEB,CONSUMER_APP,PUBLIC_WEB external;
    class PROVIDER_ADAPTER,API_CREDENTIAL,V2_GRANT,APP_DEVICE,APP_SESSION,APP_ADAPTER target;
    class START_COMMAND,SIGNAL_COMMAND,DOMAIN_EVENT,ACCOUNT,QUOTA,LEASE,FENCE,POSTGRES,OBJECT_STORE,CLICKHOUSE,TEMPORAL_PG fact;
    class API_PRODUCERS,SCHEDULER,OUTBOX_WORKER,TEMPORAL,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,V1_GATE,EXECUTION_CONTEXT,BROWSER_ROUTER,RESIDENT_BROWSER,PROXY_RELAY,WEB_ADAPTER,PUBLIC_FETCH,VAULT software;

    style COMMAND_LAYER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style WORKER_LAYER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style CONTROL_LAYER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style SURFACE_LAYER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style DATA_LAYER fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style PROVIDER_SURFACE fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style WEB_SURFACE fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style APP_SURFACE fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
    style PUBLIC_SURFACE fill:#ffffff,stroke:#94a3b8,stroke-width:1.5px,color:#334155
~~~

## 局部放大 D：监控、通知、备份、刷新与发布

~~~mermaid
%%{init: {
  "theme": "base",
  "flowchart": {"curve": "stepAfter", "htmlLabels": true, "diagramPadding": 4, "padding": 4, "nodeSpacing": 12, "rankSpacing": 24, "subGraphTitleMargin": {"top": 4, "bottom": 4}},
  "themeVariables": {"fontFamily": "Inter, Noto Sans SC, Microsoft YaHei, sans-serif", "fontSize": "14px", "lineColor": "#475569"}
}}%%
flowchart TB
    subgraph RUNTIMES["01　被观测运行组件"]
        direction LR
        FASTAPI["FastAPI"]
        COLLECTION_WORKER["Collection Worker"]
        SOURCE_WORKER["Source Worker"]
        ANALYSIS_WORKER["Analysis Worker"]
        S02_WORKER["S02 Worker"]
        OUTBOX_WORKER["Outbox Worker"]
        ALERT_RECEIVER["Alert Receiver"]
        FEISHU_BOT["Feishu Bot"]
        MEDIA_REFRESH["Media Price Refresh Worker"]
    end

    subgraph OBSERVABILITY["02　链路、指标与日志"]
        direction LR
        OTEL["OpenTelemetry Collector"]
        TRACE_FILE[("trace file")]
        BUSINESS_METRICS["Business Metrics Exporter"]
        NODE_EXPORTER["Node Exporter"]
        PROMETHEUS["Prometheus"]
        JOURNAL["systemd journal"]
        ALLOY["Grafana Alloy"]
        LOKI["Loki"]
        GRAFANA["Grafana"]
    end

    FASTAPI -.->|OTLP 链路| OTEL
    COLLECTION_WORKER -.->|OTLP 链路| OTEL
    SOURCE_WORKER -.->|OTLP 链路| OTEL
    ANALYSIS_WORKER -.->|OTLP 链路| OTEL
    S02_WORKER -.->|OTLP 链路| OTEL
    OUTBOX_WORKER -.->|OTLP 链路| OTEL
    OTEL ==>|保存| TRACE_FILE

    FASTAPI -.->|供抓取 API 指标| PROMETHEUS
    BUSINESS_METRICS -.->|供抓取业务指标| PROMETHEUS
    NODE_EXPORTER -.->|供抓取主机指标| PROMETHEUS
    OTEL -.->|供抓取 Collector 自身指标| PROMETHEUS

    FASTAPI -.-> JOURNAL
    COLLECTION_WORKER -.-> JOURNAL
    SOURCE_WORKER -.-> JOURNAL
    ANALYSIS_WORKER -.-> JOURNAL
    S02_WORKER -.-> JOURNAL
    OUTBOX_WORKER -.-> JOURNAL
    ALERT_RECEIVER -.-> JOURNAL
    FEISHU_BOT -.-> JOURNAL
    MEDIA_REFRESH -.-> JOURNAL
    JOURNAL --> ALLOY --> LOKI --> GRAFANA
    PROMETHEUS -->|指标查询| GRAFANA

    subgraph ALERTING["03　告警通知"]
        direction LR
        ALERTMANAGER["Alertmanager"]
        NOTIFICATION_STATE[("PostgreSQL 通知状态与 Outbox")]
        FEISHU_API["飞书 OpenAPI"]
    end

    PROMETHEUS -->|触发规则| ALERTMANAGER
    ALERTMANAGER -->|本地 webhook| ALERT_RECEIVER
    ALERT_RECEIVER ==>|持久化| NOTIFICATION_STATE
    NOTIFICATION_STATE -->|领取发送命令| FEISHU_BOT
    FEISHU_BOT -->|发送或更新卡片| FEISHU_API

    subgraph RECOVERY_RELEASE["04　数据恢复与版本发布"]
        direction LR
        POSTGRES[("PostgreSQL")]
        OBJECT_STORE[("对象存储")]
        CLICKHOUSE[("ClickHouse")]
        BACKUP["全量与增量备份"]
        RESTORE["恢复演练"]
        TESTS["自动化测试"]
        RELEASE_GATE["迁移与发布预检"]
        RELEASE["原子发布"]
        ROLLBACK["版本回滚"]
    end

    POSTGRES --> BACKUP
    OBJECT_STORE --> BACKUP
    CLICKHOUSE --> BACKUP
    BACKUP --> RESTORE --> RELEASE_GATE
    TESTS --> RELEASE_GATE --> RELEASE --> ROLLBACK

    subgraph DATASET_REFRESH["05　API 外资源控制的媒体价格刷新"]
        direction LR
        DATASETS_API["datasets API"]
        REFRESH_REQUEST[("耐久请求文件")]
        MEDIA_DATASET[("媒体价格数据集")]
        DATASET_CLIENT["工作台或匿名客户端"]
    end

    DATASETS_API ==>|POST 创建请求| REFRESH_REQUEST
    REFRESH_REQUEST -->|systemd path 触发| MEDIA_REFRESH
    MEDIA_REFRESH ==>|原子替换快照| MEDIA_DATASET
    MEDIA_DATASET -->|GET 返回只读快照| DATASET_CLIENT

    classDef software fill:#f8fbff,stroke:#2563eb,color:#172554,stroke-width:1.7px;
    classDef external fill:#f8fafc,stroke:#64748b,color:#1f2937,stroke-width:1.7px;
    classDef fact fill:#ecfeff,stroke:#0891b2,color:#164e63,stroke-width:2px;
    class TRACE_FILE,NOTIFICATION_STATE,POSTGRES,OBJECT_STORE,CLICKHOUSE,REFRESH_REQUEST,MEDIA_DATASET fact;
    class FEISHU_API,DATASET_CLIENT external;
    class FASTAPI,COLLECTION_WORKER,SOURCE_WORKER,ANALYSIS_WORKER,S02_WORKER,OUTBOX_WORKER,ALERT_RECEIVER,FEISHU_BOT,MEDIA_REFRESH,OTEL,BUSINESS_METRICS,NODE_EXPORTER,PROMETHEUS,JOURNAL,ALLOY,LOKI,GRAFANA,ALERTMANAGER,BACKUP,RESTORE,TESTS,RELEASE_GATE,RELEASE,ROLLBACK,DATASETS_API software;

    style RUNTIMES fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style OBSERVABILITY fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style ALERTING fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style RECOVERY_RELEASE fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
    style DATASET_REFRESH fill:#ffffff,stroke:#0f3d5e,stroke-width:2px,color:#0f3d5e
~~~

## 实现成熟度边界

| 能力 | 实现成熟度 | 代码或部署依据 |
| --- | --- | --- |
| 消费级 AI 真实网页采集 | 已实现并实跑；五个平台均有真实网页问答运行证据，各平台成熟度不同 | 五个平台采集器、网页采集进程、浏览器路由、常驻浏览器和地域代理服务 |
| 模型 API 采集 | 待接入；三表面契约已经定义该采集来源 | 三表面 V2 契约与目标节点 |
| 消费级 AI 真实 App 采集 | 待实现；三表面契约已经定义该采集来源 | 三表面 V2 契约与目标节点 |
| v1 资源治理 | 现行实现 | 账号、会话、浏览器、配额、Lease 与 Fencing 路径 |
| Typed Grant | V2 分阶段接入 | V2 collection execution grant 设计与开发态实现 |
| Source Worker | 独立运行 | 公开页面 Activity 与独立 systemd 服务 |
| Outbox Worker | 独立运行 | Temporal 命令派发与 ClickHouse 幂等投影 |
| Feishu Bot | 独立运行 | 通知发送 Outbox、卡片回调与独立 systemd 服务 |
| Media Price Refresh Worker | 独立运行 | API 写耐久请求文件，systemd path 在 API cgroup 外触发 Worker |
