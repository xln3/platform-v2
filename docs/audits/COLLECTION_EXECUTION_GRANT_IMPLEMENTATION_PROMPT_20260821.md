# 采集账号额度预占与浏览器 Fencing 修复：新会话开发提示词

> 用途：把本文完整交给一个新的 Codex 开发会话。本文是自包含的实施任务书，不依赖生成本文的聊天记录。
>
> 日期：2026-08-21，时区：Asia/Shanghai。
>
> 当前安全状态：全部采集、worker、scheduler 与自动 cron 必须继续保持停止，直到本文的恢复门槛全部满足。

## 0. 给新会话的直接任务

你在 `/home/xln/geo-system/platform-v2` 中工作。请实现一套数据库强一致的“采集执行授权协议”，同时解决：

1. 账号日、周、年额度没有原子预占，batch 和并发 run 会重复消费同一份余额；
2. 浏览器 fence 失效后旧 worker 仍可能操作同一 CDP，形成 split-brain；
3. Temporal Activity 重试可能重复向外部平台发送同一题；
4. `CollectionTask` 落库和 account governor 落账分属两个事务，存在永久漏记窗口；
5. 管理端强制释放、账号换绑、全局暂停和异常回收没有统一线性化语义；
6. 共享worker unit的stop/kill跨越多个browser instance，per-instance队列无法阻止跨实例OS动作互相越过；supervisor重启还可能遗忘已交给systemd的in-flight job；
7. Hard Terminate不能把Temporal终态误当作浏览器已物理静默，必须闭合gateway/holder隔离与发送结算证据。
8. Relay/region HTTP probe乱序完成会让旧healthy/failure覆盖较新结果，必须有单调observation generation、hysteresis和manual override epoch，同时保留同地域不同账号/平台的并行采集；
9. 主批次身份必须由campaign freeze前的权威slot/run-origin intent决定，不能由第一个创建/完成或锁赢家猜；豆包缺verified正式账号binding时scheduler与Activity都必须阻断，不能走env/CDP旧路。
10. 所有维持这些不变量的`SECURITY DEFINER`函数必须服从一套全局权限硬化合同；任何同名对象、可控`search_path`、默认`PUBLIC EXECUTE`、直接DML或RLS绕过都不能取得授权能力。

请优先分派子智能体并行完成以下只读核查，再由主智能体统一设计和实施：

- 数据库 schema、锁顺序、额度 bucket/reservation 状态机；
- browser fence、CDP 生命周期、五个平台发送边界；
- Temporal 历史回放、Activity retry、captcha assist 和 continue-as-new；
- PostgreSQL 并发测试、故障注入、生产迁移及回滚审查。

不要把任务简化成“给 `used_today` 加一个 `FOR UPDATE`”或“心跳丢失后多打一条日志”。最终必须形成额度授权、浏览器所有权、发送意图、结果持久化和治理事件的闭环。

## 1. 开工前必须保护的现场

### 1.1 已审计的生产基线

本提示词所引用的已部署基线是：

```text
commit: 6be26f1
snapshot: /home/xln/geo-system/.deploy-backups/platform-v2-sampling-doubao-6be26f1-20260821T085326Z
```

先以该只读快照还原生产行为，再审阅当前工作树相对它的变化。不要把备份目录当作开发目录，也不要修改备份。

### 1.2 当前工作树不是干净基线

2026-08-21 审计时，当前工作树 HEAD 为 `6fdc00d`，同时存在大量其他会话的 UVW、报价、分析链路和适配器未提交改动；其中包括：

```text
workflows/activities/collection.py
workflows/activities/doubao_adapter.py
workflows/activities/deepseek_adapter.py
workflows/activities/tongyi_adapter.py
workflows/activities/yiyan_adapter.py
workflows/activities/yuanbao_adapter.py
workflows/definitions/collection.py
```

这些改动属于用户或其他会话。开工时必须重新执行：

```bash
git status --short
git diff -- <准备修改的文件>
git log -n 10 --oneline
```

逐块合并，不得覆盖、回退、格式化掉或顺手提交无关改动。禁止使用 `git reset --hard`、`git checkout --` 等破坏性恢复命令。

### 1.3 Alembic revision 不得预先硬编码

审计期间工作树中已经出现新的 UVW migration。实施前重新确认全部 Alembic heads，并基于当时真实 head 创建下一 revision；不得照抄本文猜测 revision 编号。

### 1.4 操作边界

- 开发和测试期间继续保持真实采集停止。
- 禁止启动生产 worker、scheduler、cron 或真实浏览器发送。
- 单元测试、隔离 PostgreSQL 集成测试、Temporal 测试服务器和 fake-browser 可以运行。
- 任何真实平台 canary 必须等代码、迁移、故障测试完成，并再次取得用户明确授权。
- 回滚策略只能 fail-closed；不得通过 `GEO_ACCOUNT_GOVERNANCE=off`、`GEO_BROWSER_FENCING=local` 或 env 旧路绕过新协议。

## 2. 已确认的现状与缺陷

### 2.1 额度路由不是授权

当前 `AccountGovernor.resolve_collectable()` 只读取账号状态和额度快照，返回 `remaining_today`。它没有：

- 锁定账号；
- 预占日、周、年额度；
- 创建 claim/reservation；
- 把账号切成真实 running；
- 向后续 adapter 传递不可变账号身份。

`resolve_batch_instance()` 还会对 batch 内每题重复 resolve，随后把 governor 返回的 `platform_account_pub_id` 和 `remaining_today` 丢掉，只留下浏览器实例路由。

直接反例：账号只剩 1 次，4 题 batch 四次都可能读到余额 1，最终发送 4 次。两个 run 也可能先后通过同一旧快照；browser fence 只让它们排队，不会替额度做 reservation。

### 2.2 account governor 的计数和去重不是并发安全的

当前 `used_today/week/year += 1`、browser `error_streak += 1`、mode quota JSON 整体读改写都没有账号/browser 行锁。Outcome 幂等只扫描最近 50 条事件，数据库没有确定性 outcome 唯一键。

因此可能发生：

- lost update；
- 同一 outcome 重复计费；
- normal/deep_think 两个模式 block 相互覆盖；
- 并发失败看不到彼此，熔断延迟；
- old/new 状态事件与真实顺序不一致。

### 2.3 任务落库和治理落账存在双事务空窗

当前 `persist_collection_result()` 先提交 `CollectionTask`、run 计数、证据和分析 outbox，退出事务后才 best-effort 调用 governor。若进程在两次事务之间崩溃，或 governor 临时失败：

```text
CollectionTask 已存在
run 计数已增加
账号额度、墙、熔断和 task_outcome 永久漏记
```

Activity 重试看到 `created=False` 后不会再上报，因而这不是最终一致，而是永久缺口。

### 2.4 当前 browser fence 只是租约互斥，不是完整 fencing

现有 DB acquire/release/heartbeat 使用 token 和行锁，正常竞争路径基本安全；但 token 被藏在 `_BrowserFenceLock` 内，adapter 只拿到 `(context, page, resident)`。

已确认的问题包括：

- heartbeat 返回 false 时只记录 `browser_fence_lost`，业务线程继续；
- heartbeat 数据库异常只 warning，网络分区后旧 worker 可能继续发送；
- 管理端 release 没有 expected holder/token CAS，存在 ABA 误释放；
- 过期 lease 会被直接抢占，但旧 CDP WebSocket 没有被物理终止；
- acquire commit 成功而响应丢失时，没有 acquisition `lease_id` 幂等恢复；
- TTL 使用 worker 本地时间，受节点时钟偏移影响；
- 部分 probe/drill 工具直接 attach 原始 CDP，绕过 fence。

直接反例：A 持 token 7；管理端写 `released_at`；A 只打 lost 日志但继续；B 获得 token 8；A/B 同时操作同一浏览器。

### 2.5 外部发送无法由数据库唯一约束去重

batch Activity 会自动重试。adapter 整批结果主要保存在进程内，只有整批返回后 workflow 才逐题 persist。若平台已经接收前 N 题后 worker 崩溃，下一 attempt 会重发前 N 题。

`UNIQUE(run_id, business_key)` 只能防止数据库最终结果重复，不能撤销外部平台已经接收的请求或已经消耗的额度。

此外，五个平台现有提交 helper 普遍存在 click 异常后尝试 Enter、未观察到 composer 清空后再次发送的逻辑。“click 已经到达浏览器但调用返回异常”时，fallback 本身也可能造成双发。

### 2.6 当前生产只读证据的正确解读

审计时：

- 当前地域、账号计数没有发现明显非法值；
- 1,102 条 `task_outcome` 按现有完整键没有发现重复；
- 治理稳定上线后的任务暂未发现明确漏 outcome；
- 已出现 Activity attempt 2、batch failure 和 browser fence preemption；
- 这些事实证明重试和抢占路径真实发生，但不单独证明每次都发生了重复发送或 split-brain。

“当前没查到脏数据”不能替代结构性并发保证。

## 3. 最终方案必须满足的安全不变量

以下不变量是设计和验收真源。任何实现若违反其中一条，不能恢复采集。

### INV-1：全局暂停具有数据库线性化点

`pause_requested` 提交后不得签发新的submit permit或captcha effect permit。它提交前已经签发的短期permit仍可能在事务提交后执行click/OTP/capture，因此Pause API不能立即宣称完成；必须按冻结manifest等待两类permit逐一取得terminal receipt，或由gateway barrier/holder物理隔离把dispatching事实保守收敛为unknown，再等待holder quiesce，才能提交并对外返回`paused`。仅TTL到期不能证明dispatching permit未转发。已经被平台接受的请求可以完成有界捕获，但不能因为pause自动重发。

### INV-2：无执行授权，不得 attach 或发送

受治理的真实平台 Activity 必须持有有效 execution grant。Grant 同时包含账号额度 reservation 和确切浏览器 lease；只有 quota token 或只有 browser token 都不够。

### INV-3：有限额度永不被并发突破

对权威policy registry为该external platform identity/quota subject、平台和canonical mode推导出的全部适用quota scope（subject全局、当前mode等）的当前day/week/year bucket中的每一个，都必须满足：

```text
reserved_units + debited_units <= quota_limit
```

管理员主动下调 quota 造成历史 exposure 超限时可以保留事实，但新 grant 必须为 0，直到重新回到容量内。

### INV-4：同quota subject/账号同一时刻最多一个 live collection grant

当前是一个external identity只有一个verified运行binding、一个账号唯一绑定一浏览器的模型，因此数据库层用partial unique constraint分别保证一个quota subject和一个账号只有一个`reserved_unactivated/active/finalizing` grant。复制/撤销/重建管理账号行不能产生第二份额度或并发权。未来若支持同subject并发，必须显式引入平台已验证的并发上限，不能删除约束后默认放开。

### INV-5：同浏览器同一时刻只有一个可写 owner

不同地域、不同账号、不同浏览器可以并发；同一 instance key 只能存在一个有效 lease generation。Token/revocation/过期/boot epoch 变化后，系统不得再授权旧 holder 的新写操作；在 gateway barrier ACK 或 kill-holder+restart 物理证据前不得授权新 owner。已经进入 gateway/kernel/Chromium 的旧命令无法被数据库 token 撤回，必须按 forwarded unknown/tainted 处理。

### INV-6：同一逻辑 Activity retry 复用逻辑身份，但不默认复用物理 lease

同一个 Temporal `workflow_run_id + activity_id` 的 attempt 重试必须复用同一 execution request 和逻辑 operation identity，不能凭 retry 新建平行发送事实。已有 live/terminal reservation item 优先恢复；只有旧 grant generation 已安全 terminal、状态机明确允许时才能串行创建新 grant generation。相同幂等键但 payload/hash 不同必须 non-retryable fail-loud。物理 `lease_id/holder session` 属于具体 worker boot 和 Activity attempt；新 attempt 不得默认继承旧 attempt 的活动 CDP lease。只有能证明是同一 acquisition response 恢复时才可继续原 lease，否则必须先 revoke/quarantine 并完成物理 fencing，再以新 token 获取 lease。已有 `dispatching/accepted` item 只恢复状态，不重新发送。

### INV-7：发送结果未知时不自动重发

第三方平台不提供可用业务幂等键，数据库与 click 之间无法做分布式原子提交。因此采用保守 at-most-once 自动发送语义：

- 明确未发送：释放额度；
- 已接受：扣额；
- 是否发送无法证明：记 unknown-debit，禁止自动重发和自动退款。

### INV-8：每题只有一个不可逆提交状态机

每个 granted item 最终且仅最终一次进入 consumed、released 或 unknown-debited。`aborted` 且未执行的题必须 release，不能增加额度或失败 streak。

### INV-9：账号身份和浏览器身份不可事后重新解析

结果、墙、熔断、额度effect和审计必须使用grant中冻结的`quota_subject_id/platform_account_id/binding_version/browser_id/instance_key`。不得在结果落库时按可变`browser_instance_key`重新查账号，也不得因账号重新绑定而把历史effect记到新subject。

### INV-10：所有外部 I/O 都在短数据库事务之外

不得持有 account、bucket、reservation、fence 行锁执行 HTTP、CDP、sleep、等待回答或发送通知。所有数据库状态转换必须是短事务和条件 CAS。

### INV-11：异常所有权丢失必须物理隔离

仅更新 DB token 不能阻止 Chromium 执行旧 WebSocket 命令。过期、force-release、detach 失败或所有权不确定后，必须进入 quarantine，并在旧 holder/连接被物理隔离后才能重新派发：由受控 supervisor 终止旧 holder并更换 browser boot，或由 token-aware CDP gateway 拒绝/断开旧连接。两者都不能撤回已转发命令；存在 side effect 不确定时还必须 taint/reset context 或重启 browser，并按 unknown/consumed 结算。

### INV-12：治理事实必须 durable 且幂等

发送/捕获状态转换必须在terminal事务以唯一gate-effect winner同步更新admission-critical subject/mode/session真源，并写引用它的durable governance outbox，不能再依赖task commit后的best-effort回调。消费者使用数据库唯一键并按quota subject→account/session/governance→browser行锁，只做通知、兼容绝对投影和非critical派生，不再更新critical gate/streak；重复投递、suffix或换binding不得重置/重复改变状态。

### INV-13：所有生产 bypass 都必须关闭

DB enforced 模式下：

- `GEO_ACCOUNT_GOVERNANCE=off` 不得回退 env 账号；
- `GEO_BROWSER_FENCING=local` 不得连接共享 resident browser；
- 未带 execution grant 的旧 Activity 不得直接 acquire/attach；
- 有 gateway 后，probe、drill、OTP、captcha assist 等工具一律不得直连原始 CDP；gateway 上线前，仅允许持有效 purpose lease 的 cooperative direct CDP，并要求任何异常接管 quarantine、终止旧 holder、browser restart。无 lease 的直连始终禁止。

### INV-14：数据库约束承担基础完整性

状态词表、非负数、唯一 live owner、唯一幂等 receipt、item 唯一终态等必须尽量由 `CHECK/UNIQUE/FK` 保证，不能继续以“状态以后可能扩展”为理由全部留在程序层。

### INV-15：正式账号绑定是 grant 前置条件

所有平台、尤其当前缺绑定风险较高的豆包，只有平台会话证明的canonical external identity已进入全局唯一quota subject、规范化账号binding处于verified，且platform、region、resident browser和binding version都完整匹配时才能进入候选集。同一subject至多一个verified binding、同一resident browser至多一个verified binding；quota ledger永远挂subject。缺正式绑定或发现重复/冲突必须在scheduler preflight标为`account_binding_missing/account_binding_conflicted`并在Activity grant最终门再次fail-closed；不得回退env账号、旧CDP URL、临时browser或按手机号/实例名猜账号。Reservation冻结完整binding composite snapshot，运行中换绑必须drain/revoke。

### INV-16：物理恢复按真实 OS 影响域串行且可跨 supervisor 重启证明

Instance fence只描述浏览器所有权，不足以串行共享worker service unit的stop/kill。任何OS命令必须声明完整physical resource set；相交resource set严格串行。共享unit恢复先关闭stable worker runtime scope的normal授权门，再在线性化事务冻结全部blast-radius holder及logical-release blockers；扫描后不得再漏进新grant。Supervisor把命令接受、D-Bus job和终态证据写入fsync durable journal；进程重启后未完成旧job对账前不得adopt/physical seal、开放raw端口或签发normal effect。Physical seal只生成append-only isolation receipt并保持recovering；全部业务blocker终态后才logical free，避免物理证据与free互相等待。

### INV-17：Hard Terminate 的 Temporal 终态不是物理完成

Hard terminate只有在三项事实同时durable后才能把chain置completed：matching Temporal terminal event、所有effect-capable holder/connection的gateway barrier、可信scope-exit或matching worker-scope physical-isolation receipt，以及所有dispatching/forwarded/accepted item的保守settlement。任一缺失时保持closing/quarantined；privileged recovery可以先在recovering内kill/restart/seal以取得物理收据，但不得发布客户终态、把browser置free或签发新holder。

### INV-18：Region/relay 健康结果按探测代际单调应用

HTTP probe的完成顺序不能决定地域健康真相。每次探测先由DB分配单调observation generation，每次worker领取再分配单调claim generation与token；只有current、未过期claim的更新observation能改变automatic projection。Final effective projection由configured state、manual override和automatic projection纯函数派生；force-blocked期间任何auto/diagnostic result都只能审计no-op，clear后必须新probe恢复。Grant短暂共享锁并冻结health epoch，不持锁执行HTTP；因此健康翻转可线性化，而同地域不同账号、平台和browser仍能并行采集。

### INV-19：Temporal Workflow定义演进必须保持每条历史可确定重放

`collection-execution-grant-v1`只允许定义首次v0→v1分叉；首条生产v1 history形成后不得复用它承载后续command-sequence变化。此后每次新增/删除/重排Activity、timer、signal、wait、Continue-As-New或改变结果驱动分支，都必须使用新的唯一patch ID并永久保留旧branch，或者由Temporal侧可验证地把整个workflow run lifetime固定到兼容definition build。Activity poller DB gate不保护Workflow Task replay。每个候选release必须用v0、v1 baseline和所有后续patch absent/present真实history Replayer通过，不能只证明fresh execution可运行。

## 4. 术语和目标链路

### 4.1 术语

- **External platform identity / quota subject**：由平台会话稳定subject证据规范化得到的真实外部账号身份；所有额度ledger都挂它，不挂可撤销的管理账号行。
- **Quota bucket**：一个quota subject在某个scope的day/week/year时间桶内的额度事实。
- **Reservation**：为一个 Activity 的一个连续任务 chunk 暂时保留的额度。
- **Execution grant**：reservation 与 browser fence lease 的联合授权。
- **Reservation item**：一题在某个 grant generation 中实际获得账号额度与浏览器资源的物理授权身份。
- **Submission operation**：一题某个 submission generation 被允许跨外部发送一次的业务身份；它可以经历多个明确未发送的物理 grant，但只能有一个不可逆发送事实。
- **Debit**：保守认定平台额度已被占用；不等于成功得到答案。
- **Success**：成功采集到合格结果，是 debit 的子集。
- **Unknown-debit**：无法证明是否已发送，按已消费处理并等待对账。
- **Fence lease**：`lease_id + holder_id + fencing_token + expires_at + boot_id`。
- **Quarantine**：浏览器所有权不可信，禁止派发，必须物理恢复。
- **Physical seal / isolation receipt**：supervisor已经证明旧holder/unit/连接不再可写、新boot/context/gateway状态受控的append-only物理事实；此时fence仍可处于recovering，不代表可重新派发。
- **Logical release**：在physical seal之后，全部workflow、quota、materialization与管理blocker终态时才把fence置free、child completed并最终重开worker scope。
- **Execution request**：Temporal retry 间稳定的逻辑请求身份；它可以经历多个串行 grant generation，但同一时刻最多一个 live reservation。
- **Grant generation**：一次具体账号、bucket 和浏览器物理授权代际；资源变化或明确未发送后的安全重授权会生成新代际。
- **Submission generation**：同一业务题被允许发起外部提交的业务代际；accepted/unknown 后不得自动增加，只有明确未发送或人工批准才能创建下一代。
- **Capture generation**：对同一 submission operation 追加捕获证据/答案的代际；它不代表再次提交。unknown/accepted-capture-failure 的原终态 manifest 永久保留，后续验证码或人工对账得到的 verified answer 写新 generation。
- **Execution protocol assignment**：在业务 run 启动前持久化并冻结的协议版本、task queue 和 expected worker build。不能从 Activity result 是否缺字段反推新旧协议。

### 4.2 目标时序

```text
Temporal Activity 开始
  -> 生成稳定 execution request key
  -> 短事务原子取得：dispatch gate + region gate + account quota + browser fence
  -> 立即启动 grant/fence heartbeat
  -> attach 到授权的 browser instance
  -> 每题准备
  -> 点击前短事务：验证 pause/region/account/reservation/fence，写 dispatching 并保守 debit
  -> 执行唯一一次外部 submit
  -> 写 accepted 或 unknown
  -> 捕获答案并写 durable staging/hash
  -> 写 item outcome + authoritative governance gate effect + governance outbox
  -> Activity 内结算 reservation，detach CDP，finalize/release 或 quarantine fence
  -> 返回等长 batch result
  -> workflow 幂等持久化 CollectionTask 并关联 submission operation；获 grant 的结果同时关联 reservation item
```

## 5. 选定的总体架构

### 5.1 使用联合 execution grant，不使用两个互不相干的锁

目标实现应在一个短数据库事务中同时：

1. 检查全局 dispatch control 和地域健康；
2. 选择并锁定账号；
3. 计算并预占账号全局与当前 mode 等所有适用 scope 的 day/week/year quota；
4. 创建幂等 reservation 和 item；
5. 取得该账号绑定浏览器的 fence lease/token。

这样可以消除“额度已经预占但浏览器被其他 owner 长时间占用”以及“拿到浏览器但额度并未授权”的中间状态。

事务内不 attach CDP，也不等待浏览器。若 fence 当前 busy/quarantined，该账号不能获得 grant；选择其他账号或返回无容量。

### 5.2 不新增不必要的 Workflow 命令序列

优先在现有 `collect_*_batch` 和 legacy-named `collect_with_adapter` Activity内部接入统一execution coordinator。Activity代码本身不进入Temporal deterministic replay，但返回类型及workflow对结果的解释可能改变后续command序列，所以只有DB assignment=1、workflow history记录v1基线marker且实际Workflow Task由兼容definition release处理的新执行可以消费typed deferred/captcha envelope；completed v0 history仅做Replayer兼容。Activity可以用Temporal的`workflow_run_id + activity_id`生成稳定request key，但operation key还必须使用DB冻结的tenant/run/business contract。

首次引入execution-grant协议时，在循环外稳定位置使用：

```python
workflow.patched("collection-execution-grant-v1")
```

这个ID只表示**首次v0→v1命令序列分叉**。第一个生产v1 history形成后，该marker的调用位置、调用次数以及它控制的旧/新command branch必须永久冻结；旧history无marker走旧分支，带marker history永远重放同一v1基线分支。严禁以后把新的Activity/timer/signal/wait/CAN/cancel顺序继续塞进这个已存在marker内部。

此后每一次可能改变Workflow command序列、异常分支到达顺序或Activity result解释方式的变更，都必须二选一：

1. **默认方案：新的唯一patch ID。** 例如`collection-execution-grant-v1-deferred-wait-v1`、`...-hard-abort-v1`、`...-history-budget-can-v1`；每个ID只对应一项有边界的command变化，调用点确定且不在数据依赖循环中增减次数，永久保留旧branch。Patch registry记录ID、引入release、精确旧/新command trace、适用workflow类型、CAN行为、Replayer corpus和允许deprecate的证明；不得复用、改义或删除ID；
2. **经验证的Workflow Task版本固定。** 只有实际Temporal Server和当前锁定Python SDK均通过隔离实验，且部署收据证明每个既有workflow run整个history生命周期都路由到兼容Worker Deployment/build时，才可在该run内不patch地改变新build command序列。Activity poller的DB gate、artifact digest或“新Activity不会发送”都不能保护Workflow Task replay。长驻且会Continue-As-New的workflow若选择每run pinned、仅在CAN边界升级，必须让predecessor授权intent/input冻结next workflow definition release，Temporal真实successor也被路由到该release；不得让CAN隐式漂到未知current build。

当前仓库锁定`temporalio==1.15.0`，而部署端Server/Worker Deployment能力尚未在本文审计中证明，所以实施默认采用“唯一patch ID+旧分支保留”；Worker Versioning只作为后续经实验启用的增强，不能作为本次安全性的未验证前提。每个run assignment/start receipt另冻结`workflow_definition_release_id/workflow_patch_set_hash/versioning_behavior/expected_workflow_task_deployment+build`；Workflow Task routing由Temporal侧事实证明，DB字段只做交叉审计，不能反过来自称已路由。

### 5.3 Activity 内部支持安全的 partial grant

一个 segment 请求 N 题时，grant 可以只批准前 K 题。共享 coordinator 应按原顺序：

1. 为剩余前缀取得 grant；
2. 在该账号/浏览器上顺序完成 K 题；
3. finalize/release；
4. 如策略允许，再为剩余题选择另一个账号；
5. 单次 pause/busy/health/quota=0 只是 transient deferred，不能立刻生成失败占位。只有达到 run 的明确业务 deadline、用户取消，或能证明所有 eligible account 在本 run 窗口内均无容量时，workflow 才把尾题 terminalize 为 neutral `account_quota_unreserved`，并保存完整 terminal manifest 和原因。

任何时刻都不得把未 grant 的题传给 adapter 发送。返回结果仍必须与输入等长、同序。

### 5.4 外部发送采用保守 at-most-once，而非伪造 exactly-once

数据库在 click 前写 `dispatching`。如果进程随后崩溃，不能判断 click 是否到达 Chromium，因此重试看到 `dispatching` 必须产出 `submission_outcome_unknown`，不能再次发送。

为尽量减少 unknown，同时保存 durable capture staging；若平台已返回答案但 Activity ACK 丢失，重试应从 staging 恢复结果，而不是重新访问平台。

### 5.5 浏览器异常接管使用 quarantine

正常release只有在当前generation全部operation已终态、无未决side effect/frame/stream，页面/context已由matching holder从`owned_dirty`清理为`clean`并取得quiescence/cleanup receipt，随后adapter成功detach CDP时，才能把fence置为free。以下情况一律quarantine，不允许直接抢占：

- lease 过期；
- 管理员 force-release；
- heartbeat/token 不确定；
- CDP detach 失败；
- holder 进程失联；
- browser boot identity 不匹配。
- `submission_outcome_unknown`，或 accepted 后 capture/页面状态无法确认，旧命令可能仍在运行。

恢复需要停止旧 generation 的新命令，并处理已经转发的命令：

1. 无 gateway 时先终止并验证旧 holder worker boot/PID/cgroup 已退出，再重启 resident browser，验证新的 systemd InvocationID/browser boot ID 和 CDP 健康；只重启 Chrome 而让失控 holder 存活，它仍可能重新连接静态 raw URL，不构成严格恢复；
2. 上线 token-aware 本机 CDP gateway，由 gateway 在 token/epoch 变化时拒绝并关闭旧 WebSocket；但 gateway 不能撤回已经转发给 Chromium 的命令，存在 dispatching/accepted 或无法证明零副作用时仍须等待可证明 quiescence、重置 page/context，无法证明则重启 resident browser。

在原始 CDP 端口仍可被任意 worker/tool 直连时，只能称为 cooperative lease，不能宣称严格 fencing。

## 6. 数据库目标模型

以下名称是建议名。实现前检查现有命名约定，但不要降低字段和约束表达的语义。

### 6.1 `collection_dispatch_control`

建立机器域 singleton/分层控制表，作为真正的数据库 kill switch：

| 字段                                               | 语义                                                                          |
| -------------------------------------------------- | ----------------------------------------------------------------------------- |
| `scope`                                            | `global`，以后可扩展 platform/instance                                        |
| `state`                                            | `open / draining / pause_requested / paused / emergency`                      |
| `epoch`                                            | 每次 pause/resume/reconfigure 单调递增                                        |
| `effect_authorization_epoch`                       | 每次离开/重新进入open、protocol enforce或其他effect权限边界单调递增，永不回退 |
| `protocol_version`                                 | 当前强制执行的 execution grant 协议版本                                       |
| `service_clock_elapsed_ms`                         | 已累计的可采集服务时间，只在 open 期间增长                                    |
| `service_clock_resumed_at`                         | 当前 open 区间的 DB 起点；非 open 时为空                                      |
| `service_clock_last_tick_ms/service_clock_version` | 防时钟回拨的单调下界和 CAS/version                                            |
| `reason`                                           | 操作原因                                                                      |
| `changed_by`                                       | 操作者                                                                        |
| `changed_at`                                       | DB 时间                                                                       |

约束：

- `UNIQUE(scope)`；
- state `CHECK`；
- epoch 非负；
- effect authorization epoch 非负、只能由受控状态转换函数递增；
- 生产必须存在 `global` 行，缺行 fail-closed。

语义：

- `draining`：禁止新 grant，允许已经 `dispatching/accepted` 的题完成；
- `pause_requested`：禁止新grant、新submit permit和新captcha effect permit，等待冻结的两类permit terminal manifest与holder quiesce；
- `paused`：已确认没有仍可合法 click 的 permit/holder；
- `emergency`：除上述限制外，请求 revocation/quarantine 所有活动浏览器；
- admission 和 `begin_submission` 用 `FOR SHARE` 读取该行；pause request 用 `FOR UPDATE`。线性化点保证 pause request 后不再签发新 permit，但不能撤销 request 前已提交的 permit；只有 quiescence 完成后的 `paused` 才表示没有后续合法 click。
- control状态转换/terminalizer通过受控函数在同一`FOR UPDATE`事务维护单调“采集服务时钟”。有效tick定义为`max(last_tick, elapsed_ms + (state=open ? max(0, db_now-resumed_at) : 0))`；每次写回同时推进last_tick/version。离开open时把有效tick固化到elapsed并清空resumed_at，重新open只设置新的DB anchor。`pause_requested/paused/draining/emergency`期间服务时钟冻结。DB/NTP时钟回拨不得让tick下降或提前终结请求；若时间源异常导致tick停滞必须告警、fail-safe延后而非提前过期。Run assignment创建事务在同一control snapshot上冻结`service_deadline_tick_ms=current_tick+service_budget_ms`；该run后续所有segment/request只复制同一个tick，不能各自获得一份新预算。Pause/resume/terminalizer都比较同一clock version。这样无需pause时批量改每一行，也不会因Activity retry、late segment或continue-as-new偷偷延长run deadline。

另建 durable `collection_dispatch_control_operation`，至少保存 `operation_id/idempotency_key/expected_epoch/target_state/state/requested_by/reason/requested_at/completed_at/quiescence_snapshot_hash/last_error`。`UNIQUE(idempotency_key)`；pause API、signal outbox、reconciler 和 UI 都引用同一 operation，不能靠内存 future 判断暂停完成。

### 6.1.1 `collection_execution_protocol_assignment`

新旧 Temporal 协议不能靠“result 是否缺新字段”猜测。Expand 阶段给 `CollectionRun` 增加 immutable `execution_protocol_version`（既有行显式回填 0），并建立一对一 assignment/等价受控表：

```text
run_pub_id PRIMARY KEY / FK ON DELETE RESTRICT
protocol_version: 0 / 1
workflow_type, workflow_id
task_queue
expected_worker_release_id
expected_worker_deployment, expected_worker_build_id
expected_worker_artifact_digest, expected_config_contract_hash
workflow_definition_release_id, workflow_patch_set_hash
workflow_versioning_behavior: unversioned_patched / pinned / auto_upgrade
expected_workflow_task_deployment, expected_workflow_task_build_id
initial_workflow_routing_revision_id, current_workflow_routing_revision_id
compatible_workflow_definition_release_set_hash
workflow_input_contract_hash
launch_attempt_id, launch_eligibility_evaluation_id
sampling_campaign_id, sampling_policy_version
sampling_run_origin_intent_id, sampling_run_origin_contract_hash
sampling_leg_assignment_manifest_hash, mode_segment_manifest_hash
run_execution_item_count, run_execution_item_set_hash
deadline_policy: service_clock / absolute
service_budget_ms NULLABLE, service_deadline_tick_ms NULLABLE
absolute_business_deadline_at NULLABLE
terminalization_policy_version, terminalization_policy_hash
task_persistence_policy: persist / suppress
task_persistence_policy_hash
termination_root_id NULLABLE
current_assignment_terminal_receipt_id NULLABLE
termination_obligation_epoch bigint NOT NULL DEFAULT 0
assignment_state_version bigint NOT NULL DEFAULT 0
state: building / frozen / superseded
replacement_run_pub_id NULLABLE
assigned_by, assignment_reason, assigned_at

collection_execution_protocol_assignment_item
id, assignment_id, launch_attempt_id, ordinal
segment_ordinal, launch_required_member_id, launch_target_item_id
formal_leg_id, canonical_item_key, business_key
source_mapping_id, source_platform, source_model, source_region, source_mode
canonical_execution_mode, formal_mode
query_text_hash, immutable_query_ref, immutable_query_version
workflow_item_payload_jsonb, workflow_item_payload_hash
item_contract_hash, created_at
UNIQUE(assignment_id, ordinal)
UNIQUE(assignment_id, launch_required_member_id, business_key, canonical_execution_mode)
UNIQUE(id, assignment_id, launch_target_item_id)
composite FK(launch_required_member_id, launch_attempt_id)
  -> launch_attempt_required_member(id, launch_attempt_id)
composite FK(launch_target_item_id, launch_attempt_id, launch_required_member_id)
  -> launch_attempt_target_item(id, launch_attempt_id, required_member_id)
CHECK(jsonb canonical hash and item contract hash match stored immutable fields)

collection_launch_attempt
id, launch_key UNIQUE, launch_series_id NULLABLE, series_attempt_generation NULLABLE
launch_series_planner_revision_id NULLABLE, planner_revision_version NULLABLE
planner_epoch NULLABLE
campaign_id, run_origin_intent_id UNIQUE
schedule_lineage_key, schedule_lineage_version, business_occurrence_group_key
required_formal_leg_count, required_formal_leg_set_hash
required_member_count, required_member_set_hash
target_item_count, target_item_set_hash
deadline_policy: service_clock_on_mint / absolute_launch_cutoff
service_budget_ms NULLABLE, absolute_business_deadline_at NULLABLE
launch_min_validity_ms
deadline_contract_hash
state: waiting_eligibility / consumed / expired / cancelled / satisfied_without_run
next_evaluation_generation, last_evaluation_id NULLABLE, last_evaluation_snapshot_hash NULLABLE
consumed_run_pub_id NULLABLE UNIQUE
terminal_receipt_id NULLABLE UNIQUE
created_at, consumed_at NULLABLE
UNIQUE(id, campaign_id, run_origin_intent_id)
UNIQUE(id, launch_series_id)
UNIQUE(id, campaign_id, launch_series_id, launch_series_planner_revision_id,
       planner_revision_version)
UNIQUE(id, launch_series_id, launch_series_planner_revision_id,
       planner_revision_version)
UNIQUE(id, launch_series_id, launch_series_planner_revision_id,
       planner_revision_version, planner_epoch)
UNIQUE(launch_series_id, series_attempt_generation)
partial UNIQUE(launch_series_id) WHERE state=waiting_eligibility
CHECK((launch_series_id IS NULL AND series_attempt_generation IS NULL
       AND launch_series_planner_revision_id IS NULL
       AND planner_revision_version IS NULL AND planner_epoch IS NULL)
   OR (launch_series_id IS NOT NULL AND series_attempt_generation IS NOT NULL
       AND launch_series_planner_revision_id IS NOT NULL
       AND planner_revision_version IS NOT NULL AND planner_epoch IS NOT NULL))
composite FK(launch_series_planner_revision_id, launch_series_id,
             planner_revision_version, planner_epoch)
  -> launch_series_planner_revision(id, launch_series_id,
                                    planner_revision_version, planner_epoch)
composite DEFERRABLE FK(terminal_receipt_id, id, campaign_id, run_origin_intent_id)
  -> launch_attempt_terminal_receipt(id, launch_attempt_id, campaign_id, run_origin_intent_id)
CHECK((deadline_policy=service_clock_on_mint AND service_budget_ms IS NOT NULL
       AND absolute_business_deadline_at IS NULL)
   OR (deadline_policy=absolute_launch_cutoff AND service_budget_ms IS NULL
       AND absolute_business_deadline_at IS NOT NULL))

collection_launch_attempt_required_member
id, launch_attempt_id, campaign_id, member_ordinal, formal_leg_id
launch_series_id NULLABLE, planner_revision_id NULLABLE
planner_revision_version NULLABLE, planner_required_member_id NULLABLE
source_mapping_id, source_platform, source_region, source_mode
canonical_execution_mode, formal_mode
member_contract_hash
UNIQUE(launch_attempt_id, member_ordinal)
UNIQUE(launch_attempt_id, formal_leg_id, canonical_execution_mode)
UNIQUE(id, launch_attempt_id)
UNIQUE(id, launch_attempt_id, campaign_id, launch_series_id, planner_revision_id,
       planner_revision_version)
UNIQUE(id, launch_attempt_id, launch_series_id, campaign_id, planner_revision_id,
       planner_revision_version)
composite FK(launch_attempt_id, campaign_id, launch_series_id, planner_revision_id,
             planner_revision_version)
  -> launch_attempt(id, campaign_id, launch_series_id,
                    launch_series_planner_revision_id, planner_revision_version)
composite FK(planner_required_member_id, planner_revision_id,
             campaign_id, launch_series_id, planner_revision_version)
  -> launch_series_planner_required_member(id, planner_revision_id,
                                            campaign_id, launch_series_id,
                                            planner_revision_version)
composite FK(source_mapping_id, formal_leg_id, source_platform, source_region,
             source_mode, canonical_execution_mode)
  -> sampling_formal_leg_source_mapping(id, formal_leg_id, source_platform,
                                        source_region, source_mode,
                                        canonical_execution_mode)
DEFERRABLE constraint trigger: series-backed parent iff all four planner/series
  columns are non-NULL and match the parent attempt; standalone parent iff all
  four are NULL.  This is deliberately not expressed as a cross-table CHECK.

collection_launch_attempt_target_item
id, launch_attempt_id, target_ordinal, required_member_id
launch_series_id NULLABLE, planner_revision_id NULLABLE, planner_revision_version NULLABLE
planner_required_member_id NULLABLE, planner_target_id NULLABLE
business_work_item_id, business_work_requirement_id
campaign_id, business_occurrence_group_key, work_requirement_generation
formal_leg_id, canonical_execution_mode
canonical_item_key, immutable_query_ref, immutable_query_version, query_text_hash
business_key, workflow_item_payload_jsonb, workflow_item_payload_hash
target_kind: canonical_main / top_up / supplemental / run_now
target_reason, sampling_cell_id NULLABLE, sampling_execution_target_id NULLABLE
sampling_target_need_version NULLABLE
campaign_need_event_id NULLABLE, campaign_need_projection_version NULLABLE
target_contract_hash
UNIQUE(launch_attempt_id, target_ordinal)
UNIQUE(launch_attempt_id, required_member_id, business_key, canonical_execution_mode)
UNIQUE(id, launch_attempt_id, required_member_id)
UNIQUE(id, business_work_item_id)
UNIQUE(id, business_work_item_id, business_work_requirement_id)
UNIQUE(id, business_work_item_id, launch_attempt_id, campaign_id,
       formal_leg_id, canonical_item_key, canonical_execution_mode,
       sampling_execution_target_id, sampling_target_need_version)
UNIQUE(id, business_work_item_id, business_work_requirement_id,
       launch_attempt_id, campaign_id, formal_leg_id, canonical_item_key,
       canonical_execution_mode, sampling_execution_target_id,
       sampling_target_need_version)
composite FK(required_member_id, launch_attempt_id, launch_series_id,
             campaign_id, planner_revision_id, planner_revision_version)
  -> launch_attempt_required_member(id, launch_attempt_id, launch_series_id,
                                    campaign_id, planner_revision_id,
                                    planner_revision_version)
composite FK(planner_target_id, planner_revision_id, planner_required_member_id,
             campaign_id, launch_series_id, planner_revision_version)
  -> launch_series_planner_target(id, planner_revision_id,
                                  planner_required_member_id, campaign_id,
                                  launch_series_id, planner_revision_version)
DEFERRABLE constraint trigger: series-backed parent iff all five planner/series
  columns are non-NULL and point to the exact parent attempt/revision/member;
  standalone parent iff all five are NULL.  Mixed arms fail at commit.
composite FK(sampling_execution_target_id, campaign_id, sampling_target_need_version)
  -> sampling_execution_target(id, campaign_id, need_version)
composite FK(campaign_need_event_id, campaign_id,
             campaign_need_projection_version, sampling_execution_target_id,
             sampling_target_need_version)
  -> sampling_execution_need_event_member(need_event_id, campaign_id,
                                           need_projection_version,
                                           execution_target_id,
                                           target_need_version)
composite FK(business_work_requirement_id, business_work_item_id, campaign_id,
             formal_leg_id, canonical_item_key, canonical_execution_mode,
             sampling_execution_target_id, sampling_target_need_version,
             work_requirement_generation)
  -> launch_business_work_requirement(id, business_work_item_id, campaign_id,
                                      formal_leg_id, canonical_item_key,
                                      canonical_execution_mode,
                                      sampling_execution_target_id,
                                      sampling_target_need_version,
                                      requirement_generation)

collection_launch_schedule_lineage
id, lineage_key UNIQUE, campaign_id, producer_kind
state: active / draining / retired / quarantined
lineage_version, current_revision_id NULLABLE, current_revision_version NULLABLE
created_at, updated_at
UNIQUE(id, campaign_id, producer_kind)
UNIQUE(id, campaign_id)
composite DEFERRABLE FK(current_revision_id, id, current_revision_version)
  -> launch_schedule_revision(id, lineage_id, revision_version)

collection_launch_schedule_revision
id, lineage_id, campaign_id, producer_kind, revision_version
timezone_name, business_calendar_id, business_calendar_version
schedule_expression_kind, schedule_expression_canonical
occurrence_key_derivation_version, tick_key_derivation_version
deadline_policy, service_budget_ms NULLABLE, absolute_cutoff_rule NULLABLE
launch_min_validity_ms, missed_occurrence_policy: coalesce_one_pending
target_partition_policy_version, target_partition_contract_hash
schedule_contract_hash, state: prepared / verified / retired
effective_from, effective_to NULLABLE, evidence_hash, created_at, verified_at NULLABLE
UNIQUE(lineage_id, revision_version)
UNIQUE(id, lineage_id, revision_version)
UNIQUE(id, lineage_id, revision_version, campaign_id, producer_kind)
composite FK(lineage_id, campaign_id, producer_kind)
  -> launch_schedule_lineage(id, campaign_id, producer_kind)
CHECK(deadline policy arms and required schedule/calendar fields are complete)

collection_launch_partition
id, partition_key UNIQUE, campaign_id, partition_purpose
state: active / draining / retired / quarantined
partition_version, current_revision_id, current_revision_version
created_at, updated_at
UNIQUE(id, campaign_id)
composite DEFERRABLE FK(current_revision_id, id, current_revision_version)
  -> launch_partition_revision(id, launch_partition_id, revision_version)

collection_launch_partition_revision
id, launch_partition_id, campaign_id, revision_version
required_member_count, required_member_set_hash
state: prepared / verified / retired
predecessor_revision_id NULLABLE, partition_contract_hash
created_at, verified_at NULLABLE
UNIQUE(launch_partition_id, revision_version)
UNIQUE(id, launch_partition_id, revision_version)
UNIQUE(id, launch_partition_id, campaign_id, revision_version)
composite FK(launch_partition_id, campaign_id)
  -> launch_partition(id, campaign_id)

collection_launch_partition_revision_member
partition_revision_id, launch_partition_id, campaign_id
member_ordinal, formal_leg_id, canonical_execution_mode
source_platform, source_region, source_mode, formal_mode
member_contract_hash
UNIQUE(partition_revision_id, member_ordinal)
UNIQUE(partition_revision_id, formal_leg_id, canonical_execution_mode)
composite FK(partition_revision_id, launch_partition_id, campaign_id)
  -> launch_partition_revision(id, launch_partition_id, campaign_id)

collection_launch_series
id, series_key UNIQUE, campaign_id, producer_kind
schedule_lineage_id, launch_partition_id
state: active / draining / closed / quarantined
series_version, next_attempt_generation
current_schedule_revision_id, current_schedule_revision_version
current_planner_revision_id, current_planner_revision_version
current_pending_attempt_id NULLABLE UNIQUE
coalesced_through_tick_key NULLABLE, coalesced_tick_count
created_at, updated_at
UNIQUE(campaign_id, producer_kind, schedule_lineage_id, launch_partition_id)
UNIQUE(id, campaign_id, schedule_lineage_id)
UNIQUE(id, campaign_id, producer_kind, schedule_lineage_id, launch_partition_id)
composite FK(schedule_lineage_id, campaign_id, producer_kind)
  -> launch_schedule_lineage(id, campaign_id, producer_kind)
composite FK(launch_partition_id, campaign_id)
  -> launch_partition(id, campaign_id)
composite DEFERRABLE FK(current_schedule_revision_id, schedule_lineage_id,
                        current_schedule_revision_version)
  -> launch_schedule_revision(id, lineage_id, revision_version)
composite DEFERRABLE FK(current_pending_attempt_id, id)
  -> launch_attempt(id, launch_series_id)
composite DEFERRABLE FK(current_planner_revision_id, id, current_planner_revision_version)
  -> launch_series_planner_revision(id, launch_series_id, planner_revision_version)

collection_launch_series_planner_revision
id, launch_series_id, campaign_id, producer_kind
planner_revision_version, planner_epoch
schedule_lineage_id, schedule_revision_id, schedule_revision_version
launch_partition_id, partition_revision_id, partition_revision_version
campaign_need_event_id, target_partition_contract_hash
business_occurrence_group_key, missed_occurrence_policy: coalesce_one_pending
required_member_count, required_member_set_hash
target_item_count, target_item_set_hash
campaign_need_projection_version, campaign_need_outstanding_count, campaign_need_set_hash
state: prepared / current / retired
predecessor_revision_id NULLABLE, planner_contract_hash
created_at, activated_at NULLABLE
UNIQUE(launch_series_id, planner_revision_version)
UNIQUE(id, launch_series_id, planner_revision_version)
UNIQUE(id, launch_series_id, planner_revision_version, planner_epoch)
UNIQUE(id, launch_series_id, campaign_id, planner_revision_version)
composite FK(launch_series_id, campaign_id, producer_kind, schedule_lineage_id,
             launch_partition_id)
  -> launch_series(id, campaign_id, producer_kind, schedule_lineage_id,
                   launch_partition_id)
composite FK(schedule_revision_id, schedule_lineage_id, schedule_revision_version,
             campaign_id, producer_kind)
  -> launch_schedule_revision(id, lineage_id, revision_version,
                              campaign_id, producer_kind)
composite FK(partition_revision_id, launch_partition_id, campaign_id,
             partition_revision_version)
  -> launch_partition_revision(id, launch_partition_id, campaign_id,
                               revision_version)
composite FK(campaign_need_event_id, campaign_id,
             campaign_need_projection_version, campaign_need_outstanding_count,
             campaign_need_set_hash)
  -> sampling_execution_need_event(id, campaign_id,
                                   need_projection_version,
                                   outstanding_target_count,
                                   outstanding_target_set_hash)

collection_launch_series_planner_required_member
id, planner_revision_id, campaign_id, launch_series_id, planner_revision_version
member_ordinal, formal_leg_id
source_mapping_id, source_platform, source_region, source_mode
canonical_execution_mode, formal_mode
member_contract_hash
UNIQUE(planner_revision_id, member_ordinal)
UNIQUE(planner_revision_id, formal_leg_id, canonical_execution_mode)
UNIQUE(id, planner_revision_id)
UNIQUE(id, planner_revision_id, launch_series_id, planner_revision_version)
UNIQUE(id, planner_revision_id, campaign_id, launch_series_id,
       planner_revision_version)
composite FK(planner_revision_id, launch_series_id, campaign_id,
             planner_revision_version)
  -> launch_series_planner_revision(id, launch_series_id, campaign_id,
                                    planner_revision_version)
composite FK(source_mapping_id, formal_leg_id, source_platform, source_region,
             source_mode, canonical_execution_mode)
  -> sampling_formal_leg_source_mapping(id, formal_leg_id, source_platform,
                                        source_region, source_mode,
                                        canonical_execution_mode)

collection_launch_series_planner_target
id, planner_revision_id, launch_series_id, planner_revision_version
target_ordinal, planner_required_member_id
business_work_item_id, business_work_requirement_id
campaign_id, business_occurrence_group_key
work_requirement_generation
formal_leg_id, canonical_execution_mode
canonical_item_key, immutable_query_ref, immutable_query_version, query_text_hash
business_key, payload_contract_hash, target_kind, target_reason
sampling_cell_id NULLABLE, sampling_execution_target_id NULLABLE
sampling_target_need_version NULLABLE
campaign_need_event_id NULLABLE, campaign_need_projection_version NULLABLE
target_contract_hash
UNIQUE(planner_revision_id, target_ordinal)
UNIQUE(planner_revision_id, planner_required_member_id,
       business_key, canonical_execution_mode)
UNIQUE(id, planner_revision_id)
UNIQUE(id, planner_revision_id, planner_required_member_id)
UNIQUE(id, planner_revision_id, planner_required_member_id,
       launch_series_id, planner_revision_version)
UNIQUE(id, planner_revision_id, planner_required_member_id, campaign_id,
       launch_series_id, planner_revision_version)
UNIQUE(id, planner_revision_id, business_work_item_id)
UNIQUE(id, planner_revision_id, business_work_item_id, business_work_requirement_id)
composite FK(planner_required_member_id, planner_revision_id,
             campaign_id, launch_series_id, planner_revision_version)
  -> launch_series_planner_required_member(id, planner_revision_id,
                                            campaign_id, launch_series_id,
                                            planner_revision_version)
composite FK(sampling_execution_target_id, campaign_id, sampling_target_need_version)
  -> sampling_execution_target(id, campaign_id, need_version)
composite FK(campaign_need_event_id, campaign_id,
             campaign_need_projection_version, sampling_execution_target_id,
             sampling_target_need_version)
  -> sampling_execution_need_event_member(need_event_id, campaign_id,
                                           need_projection_version,
                                           execution_target_id,
                                           target_need_version)
CHECK(top_up target iff need-event fields and sampling execution target are non-NULL;
      other target kinds follow their frozen policy arm)
composite FK(business_work_requirement_id, business_work_item_id, campaign_id,
             formal_leg_id, canonical_item_key, canonical_execution_mode,
             sampling_execution_target_id, sampling_target_need_version,
             work_requirement_generation)
  -> launch_business_work_requirement(id, business_work_item_id, campaign_id,
                                      formal_leg_id, canonical_item_key,
                                      canonical_execution_mode,
                                      sampling_execution_target_id,
                                      sampling_target_need_version,
                                      requirement_generation)

collection_launch_business_work_item
id, semantic_work_key GENERATED ALWAYS UNIQUE, campaign_id, producer_kind, work_purpose
schedule_lineage_id NULLABLE, launch_partition_id NULLABLE
standalone_run_origin_intent_id NULLABLE
business_occurrence_group_key, formal_leg_id, canonical_item_key, canonical_execution_mode
state: open / closed
current_requirement_id, current_requirement_generation
work_contract_hash, created_at, updated_at
UNIQUE(id, campaign_id, business_occurrence_group_key,
       formal_leg_id, canonical_item_key, canonical_execution_mode)
partial UNIQUE(campaign_id, work_purpose, schedule_lineage_id,
               business_occurrence_group_key, formal_leg_id, canonical_item_key,
               canonical_execution_mode)
  WHERE schedule_lineage_id IS NOT NULL
partial UNIQUE(campaign_id, work_purpose, standalone_run_origin_intent_id,
               business_occurrence_group_key, formal_leg_id, canonical_item_key,
               canonical_execution_mode)
  WHERE standalone_run_origin_intent_id IS NOT NULL
CHECK(series arm has schedule_lineage_id and launch_partition_id both non-NULL;
      standalone arm has both NULL and standalone_run_origin_intent_id non-NULL)
composite FK(schedule_lineage_id, campaign_id, producer_kind)
  -> launch_schedule_lineage(id, campaign_id, producer_kind)
composite FK(launch_partition_id, campaign_id)
  -> launch_partition(id, campaign_id)
composite FK(standalone_run_origin_intent_id, campaign_id, producer_kind)
  -> sampling_run_origin_intent(id, campaign_id, producer_kind)
composite DEFERRABLE FK(current_requirement_id, id, current_requirement_generation)
  -> launch_business_work_requirement(id, business_work_item_id,
                                      requirement_generation)

collection_launch_business_work_requirement
id, business_work_item_id, requirement_generation
campaign_id, business_occurrence_group_key
formal_leg_id, canonical_item_key, canonical_execution_mode
sampling_execution_target_id NULLABLE, sampling_target_need_version NULLABLE
state: pending / in_flight / fulfilled / terminal / superseded
current_attempt_target_id NULLABLE UNIQUE
consumed_assignment_item_id NULLABLE UNIQUE, consumed_run_pub_id NULLABLE
start_operation_id NULLABLE
fulfillment_receipt_id NULLABLE, termination_receipt_id NULLABLE
requirement_contract_hash, created_at, updated_at
UNIQUE(business_work_item_id, requirement_generation)
UNIQUE(id, business_work_item_id, requirement_generation)
UNIQUE(id, business_work_item_id, campaign_id, business_occurrence_group_key, formal_leg_id,
       canonical_item_key, canonical_execution_mode,
       sampling_execution_target_id, sampling_target_need_version,
       requirement_generation)
composite FK(business_work_item_id, campaign_id, business_occurrence_group_key, formal_leg_id,
             canonical_item_key, canonical_execution_mode)
  -> launch_business_work_item(id, campaign_id, business_occurrence_group_key, formal_leg_id,
                               canonical_item_key, canonical_execution_mode)
composite FK(sampling_execution_target_id, campaign_id, sampling_target_need_version)
  -> sampling_execution_target(id, campaign_id, need_version)
composite DEFERRABLE FK(current_attempt_target_id, business_work_item_id, id)
  -> launch_attempt_target_item(id, business_work_item_id,
                                business_work_requirement_id)
composite DEFERRABLE FK(consumed_assignment_item_id, consumed_run_pub_id,
                        current_attempt_target_id)
  -> execution_protocol_assignment_item(id, assignment_id, launch_target_item_id)
composite DEFERRABLE FK(start_operation_id, consumed_run_pub_id)
  -> workflow_start_operation(id, assignment_id)
composite DEFERRABLE FK(fulfillment_receipt_id, sampling_execution_target_id,
                        campaign_id, sampling_target_need_version)
  -> sampling_execution_target_fulfillment_receipt(
       id, execution_target_id, campaign_id, need_version)
composite DEFERRABLE FK(termination_receipt_id, business_work_item_id, id)
  -> launch_business_work_terminal_receipt(id, business_work_item_id,
                                           business_work_requirement_id)

collection_launch_business_work_terminal_receipt
id, business_work_item_id, business_work_requirement_id UNIQUE
receipt_kind: terminal / superseded
source_launch_terminal_receipt_id NULLABLE
source_partition_operation_id NULLABLE, source_cutover_operation_id NULLABLE
reason, evidence_hash, applied_at
UNIQUE(id, business_work_item_id, business_work_requirement_id)
CHECK(exactly one typed source is present and compatible with receipt_kind)

collection_launch_series_planner_cutover_operation
id, operation_key UNIQUE, launch_series_id
schedule_lineage_id
old_schedule_revision_id, old_schedule_revision_version
new_schedule_revision_id, new_schedule_revision_version
old_planner_revision_id, old_planner_revision_version
new_planner_revision_id, new_planner_revision_version
expected_series_version, expected_pending_attempt_id NULLABLE
pending_disposition: none / superseded_by_partition / superseded_supplemental
partition_operation_id NULLABLE, replacement_attempt_id NULLABLE
old_new_work_overlap_proof_hash, state: prepared / completed / quarantined
created_at, completed_at NULLABLE
UNIQUE(id, launch_series_id, old_planner_revision_id, new_planner_revision_id)
composite FK(old_planner_revision_id, launch_series_id, old_planner_revision_version)
  -> launch_series_planner_revision(id, launch_series_id, planner_revision_version)
composite FK(new_planner_revision_id, launch_series_id, new_planner_revision_version)
  -> launch_series_planner_revision(id, launch_series_id, planner_revision_version)
composite FK(old_schedule_revision_id, schedule_lineage_id,
             old_schedule_revision_version)
  -> launch_schedule_revision(id, lineage_id, revision_version)
composite FK(new_schedule_revision_id, schedule_lineage_id,
             new_schedule_revision_version)
  -> launch_schedule_revision(id, lineage_id, revision_version)
DEFERRABLE constraint trigger requires old/new planner revisions to freeze the
  corresponding old/new schedule revisions above, and requires the series,
  schedule lineage and all current pointers to change in the same commit.

collection_launch_series_planner_cutover_target
cutover_operation_id
old_planner_revision_id, new_planner_revision_id
old_planner_target_id NULLABLE, new_planner_target_id NULLABLE
old_business_work_item_id NULLABLE, new_business_work_item_id NULLABLE
old_business_work_requirement_id NULLABLE, new_business_work_requirement_id NULLABLE
disposition: carried_forward / satisfied_elsewhere / newly_required /
             superseded / in_flight_owned
fulfillment_receipt_id NULLABLE, termination_or_replacement_receipt_id NULLABLE
target_contract_hash
CHECK(old/new target NULLability matches disposition)
UNIQUE(cutover_operation_id, old_planner_target_id)
  WHERE old_planner_target_id IS NOT NULL
UNIQUE(cutover_operation_id, new_planner_target_id)
  WHERE new_planner_target_id IS NOT NULL
composite FK(old_planner_target_id, old_planner_revision_id)
  -> launch_series_planner_target(id, planner_revision_id)
composite FK(new_planner_target_id, new_planner_revision_id)
  -> launch_series_planner_target(id, planner_revision_id)
composite FK(old_planner_target_id, old_planner_revision_id,
             old_business_work_item_id, old_business_work_requirement_id)
  -> launch_series_planner_target(id, planner_revision_id, business_work_item_id,
                                  business_work_requirement_id)
composite FK(new_planner_target_id, new_planner_revision_id,
             new_business_work_item_id, new_business_work_requirement_id)
  -> launch_series_planner_target(id, planner_revision_id, business_work_item_id,
                                  business_work_requirement_id)
DEFERRABLE constraint trigger binds both revision IDs to the cutover header,
  verifies typed fulfillment/termination evidence against the same work identity,
  and enforces the disposition matrix described below.

collection_launch_tick_receipt
id, launch_series_id, schedule_lineage_id, scheduler_tick_key
schedule_revision_id, schedule_revision_version
planner_revision_id, planner_revision_version, planner_epoch
derived_business_occurrence_group_key, tick_key_derivation_version
observed_series_version, target_launch_attempt_id NULLABLE
disposition: attempt_created / coalesced_to_pending / already_consumed /
             stale_schedule_noop / series_closed / contract_conflict
tick_contract_hash, applied_at
UNIQUE(launch_series_id, scheduler_tick_key)
composite FK(schedule_revision_id, schedule_lineage_id, schedule_revision_version)
  -> launch_schedule_revision(id, lineage_id, revision_version)
composite FK(planner_revision_id, launch_series_id,
             planner_revision_version, planner_epoch)
  -> launch_series_planner_revision(id, launch_series_id,
                                    planner_revision_version, planner_epoch)
composite FK(target_launch_attempt_id, launch_series_id, planner_revision_id,
             planner_revision_version, planner_epoch)
  -> launch_attempt(id, launch_series_id, launch_series_planner_revision_id,
                    planner_revision_version, planner_epoch)
CHECK(target attempt is non-NULL exactly for attempt_created/coalesced/already_consumed)

collection_launch_candidate_pool
id, source_platform, region_id, canonical_execution_mode
state: draft / active / draining / retired
pool_epoch, current_revision_id, current_revision_version
UNIQUE(source_platform, region_id, canonical_execution_mode)
UNIQUE(id, current_revision_id, current_revision_version)

collection_launch_candidate_pool_revision
id, pool_id, revision_version
selection_policy_version, selection_policy_hash
candidate_count, candidate_set_hash, state: prepared / verified / retired
created_at, verified_at
UNIQUE(pool_id, revision_version)
UNIQUE(id, pool_id, revision_version)

collection_launch_candidate_pool_member
pool_revision_id, candidate_ordinal, platform_account_id
selection_rank, member_contract_hash
UNIQUE(pool_revision_id, candidate_ordinal)
UNIQUE(pool_revision_id, platform_account_id)

collection_launch_evaluation
id, launch_attempt_id, evaluation_generation
outcome: blocked / eligible_consumed
blocked_reason: global_not_open / region_unavailable / region_stale /
                account_binding_missing / account_binding_conflicted / account_session_unverified /
                quota_config_unavailable / quota_scope_unavailable / quota_bucket_unavailable /
                quota_exhausted /
                governance_blocked / browser_health_unready / browser_context_unready /
                launch_contract_conflict / none
control_epoch, eligibility_policy_version, blocked_reason_precedence_version
blocked_reason_precedence_hash, required_member_count, required_member_set_hash
evaluation_snapshot_hash, consumed_run_pub_id NULLABLE UNIQUE
eligibility_checked_at, minimum_selected_path_valid_until NULLABLE
eligibility_validity_margin_ms, committed_at
UNIQUE(launch_attempt_id, evaluation_generation)
UNIQUE(launch_attempt_id, evaluation_snapshot_hash)
UNIQUE(id, launch_attempt_id)
UNIQUE(id, launch_attempt_id, consumed_run_pub_id)
CHECK((outcome=eligible_consumed AND blocked_reason=none AND consumed_run_pub_id IS NOT NULL)
   OR (outcome=blocked AND blocked_reason<>none AND consumed_run_pub_id IS NULL))

collection_launch_attempt_terminal_receipt
id, launch_attempt_id UNIQUE, campaign_id, run_origin_intent_id
terminal_kind: expired / cancelled / targets_already_satisfied
observed_control_epoch, eligibility_checked_at
deadline_policy, absolute_business_deadline_at NULLABLE, deadline_contract_hash
prior_evaluation_count, prior_evaluation_set_hash
affected_primary_slot_count, affected_primary_slot_set_hash
descendant_run_count, descendant_assignment_count, descendant_start_count
superseding_partition_operation_id NULLABLE
reason, evidence_hash, applied_at
UNIQUE(id, launch_attempt_id, campaign_id, run_origin_intent_id, terminal_kind)
UNIQUE(id, launch_attempt_id, campaign_id, run_origin_intent_id)
composite FK(launch_attempt_id, campaign_id, run_origin_intent_id)
  -> launch_attempt(id, campaign_id, run_origin_intent_id)
CHECK(descendant counts are all 0)

collection_launch_attempt_terminal_slot_member
terminal_receipt_id, launch_attempt_id, campaign_id, run_origin_intent_id
slot_member_ordinal, formal_leg_id
primary_slot_id, slot_revision_id, prior_slot_state, new_slot_state
UNIQUE(terminal_receipt_id, slot_member_ordinal)
UNIQUE(terminal_receipt_id, primary_slot_id)
composite FK(terminal_receipt_id, launch_attempt_id, campaign_id, run_origin_intent_id)
  -> launch_attempt_terminal_receipt(id, launch_attempt_id, campaign_id, run_origin_intent_id)
composite FK(run_origin_intent_id, campaign_id, formal_leg_id)
  -> sampling_run_origin_intent_leg(run_origin_intent_id, campaign_id, formal_leg_id)
composite FK(primary_slot_id, campaign_id, formal_leg_id)
  -> sampling_primary_slot(id, campaign_id, formal_leg_id)
composite FK(slot_revision_id, primary_slot_id)
  -> sampling_primary_slot_revision(id, primary_slot_id)

collection_launch_attempt_terminal_target_member
terminal_receipt_id, launch_attempt_id, target_ordinal
launch_target_item_id, business_work_item_id, business_work_requirement_id
campaign_id, formal_leg_id, canonical_item_key, canonical_execution_mode
sampling_execution_target_id, sampling_target_need_version, work_requirement_generation
fulfillment_receipt_id
UNIQUE(terminal_receipt_id, target_ordinal)
UNIQUE(terminal_receipt_id, launch_target_item_id)
composite FK(launch_target_item_id, business_work_item_id,
             business_work_requirement_id, launch_attempt_id, campaign_id,
             formal_leg_id, canonical_item_key, canonical_execution_mode,
             sampling_execution_target_id, sampling_target_need_version)
  -> launch_attempt_target_item(id, business_work_item_id,
                                business_work_requirement_id, launch_attempt_id,
                                campaign_id, formal_leg_id, canonical_item_key,
                                canonical_execution_mode,
                                sampling_execution_target_id,
                                sampling_target_need_version)
composite FK(fulfillment_receipt_id, sampling_execution_target_id,
             campaign_id, formal_leg_id, canonical_item_key,
             canonical_execution_mode, sampling_target_need_version)
  -> sampling_execution_target_fulfillment_receipt(
       id, execution_target_id, campaign_id, formal_leg_id,
       canonical_item_key, canonical_execution_mode, need_version)

collection_launch_evaluation_member
launch_evaluation_id, launch_attempt_id, required_member_id, member_ordinal, formal_leg_id
source_platform, source_region, canonical_execution_mode
pool_resolution_outcome: resolved / pool_missing / revision_unverified
candidate_pool_id NULLABLE, candidate_pool_revision_id NULLABLE, candidate_pool_epoch NULLABLE
candidate_count, candidate_set_hash
selection_policy_version NULLABLE, selection_policy_hash NULLABLE
selected_candidate_ordinal NULLABLE
region_id, region_projection_event_id, region_health_epoch
region_applied_projection_generation, region_effective_state, region_effective_source
region_effective_fresh_until NULLABLE, region_health_policy_version
quota_subject_id NULLABLE, platform_account_id NULLABLE
binding_revision_id NULLABLE, binding_session_revision_id NULLABLE
quota_config_gate_epoch NULLABLE, quota_policy_id NULLABLE
required_quota_scope_count, required_quota_scope_set_hash
subject_global_gate_epoch NULLABLE, subject_mode_gate_epoch NULLABLE
browser_id NULLABLE, browser_health_policy_id NULLABLE
browser_health_gate_epoch NULLABLE, browser_context_generation_id NULLABLE
browser_readiness_receipt_id NULLABLE
member_outcome: eligible / blocked, blocked_reason, member_contract_hash
UNIQUE(launch_evaluation_id, member_ordinal)
UNIQUE(launch_evaluation_id, formal_leg_id, canonical_execution_mode)
composite FK(required_member_id, launch_attempt_id)
  -> launch_attempt_required_member(id, launch_attempt_id)
CHECK(pool resolved iff pool/revision/epoch/selection-policy fields are all non-NULL;
      pool missing/unverified iff candidate_count=0, selected candidate is NULL,
      and pool revision/selection-policy fields required by that path are NULL)

collection_launch_evaluation_candidate
launch_evaluation_id, member_ordinal, candidate_ordinal
platform_account_id, quota_subject_id NULLABLE
binding_revision_id NULLABLE, binding_session_revision_id NULLABLE
resident_browser_id NULLABLE, selection_rank, candidate_contract_hash
account_state_snapshot, session_state_snapshot, session_verified_until NULLABLE
subject_global_gate_epoch NULLABLE, subject_mode_gate_epoch NULLABLE
browser_health_policy_id NULLABLE, browser_health_gate_epoch NULLABLE
browser_context_generation_id NULLABLE, browser_readiness_receipt_id NULLABLE
browser_readiness_valid_until NULLABLE
candidate_outcome: eligible / blocked, blocked_reason
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal)
UNIQUE(launch_evaluation_id, member_ordinal, platform_account_id)

collection_launch_evaluation_quota_scope
launch_evaluation_id, member_ordinal, candidate_ordinal, scope_ordinal
required_scope_kind, required_canonical_mode NULLABLE
required_period_count, required_period_set_hash
resolution_outcome: resolved / account_unresolved / config_unavailable /
                    policy_unavailable / scope_missing
resolution_reason
quota_subject_id NULLABLE, quota_scope_id NULLABLE
quota_config_gate_epoch NULLABLE, quota_policy_id NULLABLE, policy_scope_revision_id NULLABLE
scope_gate_epoch NULLABLE, scope_grant_state NULLABLE, policy_scope_effective_to NULLABLE
required_bucket_count, required_bucket_set_hash, scope_snapshot_hash
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal, scope_ordinal)
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal, quota_scope_id)
  WHERE quota_scope_id IS NOT NULL
CHECK(resolved iff all resolved identity/policy/scope/gate fields are non-NULL;
      unresolved iff all those fields are NULL and required_bucket_count=0)

collection_launch_evaluation_quota_bucket
launch_evaluation_id, member_ordinal, candidate_ordinal, scope_ordinal, bucket_ordinal
period, resolution_outcome: resolved / bucket_missing
quota_subject_id NULLABLE, quota_scope_id NULLABLE, quota_bucket_id NULLABLE
bucket_key NULLABLE, starts_at NULLABLE, ends_at NULLABLE, baseline_state NULLABLE
bucket_ledger_version NULLABLE, quota_limit_snapshot NULLABLE
reserved_units NULLABLE, debited_units NULLABLE, available_units NULLABLE
capacity_eligible, grant_blocked_reason NULLABLE, blocked_at NULLABLE
baseline_source_hash NULLABLE
bucket_snapshot_hash
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal, scope_ordinal, bucket_ordinal)
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal, scope_ordinal, period)
UNIQUE(launch_evaluation_id, member_ordinal, candidate_ordinal, quota_bucket_id)
  WHERE quota_bucket_id IS NOT NULL
CHECK(bucket_missing iff all bucket identity/counter/boundary/baseline fields are NULL
      and capacity_eligible=false;
      resolved iff required identity/boundary/baseline/version/counters are non-NULL;
      on resolved row unlimited iff quota_limit_snapshot/available_units are both NULL;
      on resolved finite row available_units = greatest(0, quota_limit_snapshot-reserved_units-debited_units);
      capacity_eligible iff resolved, baseline verified, no bucket blocker,
      and unlimited or available_units > 0)
```

Candidate pool是授权配置，不是普通查询视图。Header的`(current_revision_id,id,current_revision_version)`必须以`DEFERRABLE INITIALLY DEFERRED`复合FK指向`revision(id,pool_id,revision_version)`；commit-time trigger再要求active header恰指verified revision、header platform/region/mode与该revision全部members的account binding platform/region契约一致、revision count/hash与immutable member行双向anti-join为零。Verified revision的member集合就是完整候选集合，不保留可被不同reader解释的`enabled`软开关；停用账号必须追加新revision并从成员集合移除，历史revision不改写。创建revision时预生成header/revision ID，在同一事务插draft header、prepared revision、全部members，设置复合pointer，校验后才原子转revision verified/header active；更新则追加完整prepared revision，按`region -> pool header/current revision -> old/new candidate identities/accounts稳定排序 -> revision/member -> operation final`锁序验证后切pointer并严格`pool_epoch+1`，旧revision只retire不改写。ACK丢失按operation/revision ID及pointer read-back。普通scheduler/UI/worker没有pool/revision/member直接DML权。

每个campaign launch plan freeze之前，必须为其每个required source platform/region/execution-mode组合预建active pool header和verified revision；**零候选也必须是显式revision 0（candidate_count=0、canonical empty-set hash）**，这样absence有可锁父行。Migration同样seed所有required组合。正常fresh attempt建立也必须复核这组pool真源完整；若历史/损坏数据仍出现pool完全缺失或current revision未verified，evaluation member分别走`pool_missing/revision_unverified` path，以同一规范pool-key advisory lock与并发首次创建线性化，pool/revision字段按path为NULL、candidate_count=0、reason=`launch_contract_conflict`并零Run。它不能用“查询无行”判断为普通账号不足或授权，也不能每tick事务失败；empty **verified** pool才确定性产生`account_binding_missing`。Pool从0变为有成员必须追加revision并推进epoch，所以原blocked attempt会得到新snapshot。首次并发建池以region后、identity前的规范pool-key advisory xact lock加唯一约束收敛，不能让launch和pool create各自认为自己赢了。

Cron tick不是业务occurrence。Canonical schedule及自动top-up producer必须先进入durable `collection_launch_schedule_lineage -> verified schedule revision -> stable launch partition -> collection_launch_series`。Lineage ID是跨cron表达式、时区、日历、deadline规则和planner配置变更保持稳定的业务身份；revision冻结这些可变合同及其生效边界。Partition header是独立并行/故障域的稳定身份，partition revision用规范member行冻结formal-leg×execution-mode集合；membership改变只追加revision并走planner cutover，不用可变member hash生成新series。两个无重叠partition各自最多一个pending，因此一条坏地域/账号只阻断所属partition，其余partition仍可mint；若partition配置错误地重叠到同一语义题，跨partition仍共享不含partition ID的business-work自然唯一键，第二owner必须失败/等待显式partition修复，不能双采。首版若某产品组要求“六腿全有或全无”，就把六腿放在同一verified partition；不能靠全campaign隐式串行实现。

Series是**不含schedule revision/version、partition revision/version、planner epoch、required-member hash或target hash**的稳定逻辑header，其唯一域只含campaign、stable partition ID、producer kind和stable schedule-lineage ID；schedule、partition或planner变化只能追加verified revision并切同一header current pointer，绝不能通过把revision/version拼入series key另开第二pending域。Lineage/current revision、partition/current revision、series/current schedule与planner冻结的schedule/partition revision都用复合FK和deferred trigger逐项相等；active series只能指verified且在生效窗口内的schedule revision与verified partition revision，旧pending attempt仍冻结旧revision，必须经过同一planner-cutover处置后才能让新revision生效，不能被新tick直接消费。

每次schedule配置切换都走durable cutover operation：按`lineage advisory -> control -> schedule lineage/current revision -> stable series/current pending -> old/new schedule revision -> old/new planner -> work requirements -> cutover final`锁定，验证新revision的timezone/calendar/occurrence-key/deadline/partition policy均完整、old pending处置已证明后，同commit切lineage、series和planner三个current pointer并推进版本。原地UPDATE verified revision、让series指另一lineage的revision、同version不同hash、或仅切schedule pointer不切planner均必须失败。`schedule_revision_id/version`只影响attempt contract和审计，不改变stable series/pending唯一域；因此revision切换前后N个并发tick仍只能看到一个pending owner。

Attempt冻结current planner revision/epoch、稳定`business_occurrence_group_key`、required-member set与target-item set；每次唤醒使用另一个`scheduler_tick_key`写幂等tick receipt。首版missed-occurrence policy固定`coalesce_one_pending`：同series由partial unique和双向current pointer最多一个`waiting_eligibility` attempt，后续不同tick在`series advisory -> control -> series row`同一前缀只read-back该attempt并写`coalesced_to_pending`，不得新建origin intent/attempt/evaluation“配置”。Attempt consumed/terminal后，series cursor只有在权威planner证明下一业务组确实需要工作时才推进generation；已经fulfilled的canonical slot返回`already_consumed`。若业务将来确实要求逐occurrence catch-up，必须另写有硬上限K、supersede/聚合receipt和恢复速率的policy及迁移，不能悄悄把raw cron tick当新occurrence；首版不支持无界catch-up。

Series header的current-planner与current-pending两个pointer都使用同series DEFERRABLE复合FK；trigger验证attempt的campaign、lineage/version、planner revision/epoch、business group、required-member及target-item count/hash与revision完全相同，pointer存在当且仅当恰有一个waiting attempt。Eligible consume、expired/cancelled/targets-satisfied terminal和series pointer清除必须同commit；tick固定按`control -> series -> pending attempt`，run-now因series NULL跳过。Tick receipt复合绑定同series/current planner/target attempt并冻结当时version，ACK丢失只read-back；同tick异合同fail-loud。Region/account/browser长期blocked或global pause跨N个tick时，pending attempt、origin intent和blocked evaluation snapshot数量保持策略有界；恢复时无论N个scheduler/reconciler并发都只允许该pending attempt消费一次。`service_clock_on_mint`等待期不计时不等于允许积累N个attempt。显式run-now使用各自客户端Idempotency-Key、`launch_series_id=NULL`，不与自动series合并，但仍受完整launch eligibility与quota最终gate。

Planner revision必须有规范化required-member与target rows/count/hash，不能只存摘要。Series-backed attempt的header、required member与target都冗余`launch_series_id + planner_revision_id/version`并以复合FK指向同一planner；deferred trigger证明attempt member集合是planner member集合的**精确复制**，attempt target集合是planner target/work-requirement集合的精确复制，ordinal、formal leg、mode、query、payload、execution target与need version逐项相同。Standalone run-now/manual attempt的header及所有child planner列必须全部NULL，并从immutable run-now request contract生成；不允许“standalone header + planner child”或“series header + NULL/cross-revision child”。Planner revision自身的member/target count/hash也以双向anti-join重算。Canonical-main从frozen campaign query set×required execution-mode target生成，automatic top-up从权威mode-execution outstanding projection生成。Assignment item再一对一复合指向attempt target，payload只能从target确定性派生，assignment target集合与attempt双向anti-join为零；“每个mode只放一题”、漏第2..136题、额外题、错query revision或换payload都不能以自洽assignment hash通过。

`collection_launch_business_work_item`不是可由caller提供hash的幂等装饰，而是跨tick、series cutover和scheduler重启的永久业务工作真源。Header自然身份的两臂为：series-backed=`campaign + work purpose + stable schedule lineage + business occurrence group + formal leg + canonical item + canonical execution mode`；standalone=`campaign + work purpose + frozen run-origin intent + business occurrence group + formal leg + canonical item + canonical execution mode`。两臂分别由partial natural `UNIQUE`保证，且`semantic_work_key`只能由数据库用带domain separator的canonical preimage生成并由trigger复算；caller传入的key/hash最多用于compare。`requirement_generation`不是绕开唯一性的键，而是同一work header下单调追加的`collection_launch_business_work_requirement` revision；header current pointer与revision双向绑定。Sampling need reopen、waiver撤销等只有对应typed need operation能在旧requirement终态后追加`generation+1`，普通planner/scheduler不能自增generation。

Work requirement状态矩阵必须由DEFERRABLE复合FK、双向pointer trigger和受控函数共同锁死：`pending`恰有一个matching waiting attempt target，run/assignment-item/start/fulfillment/termination全NULL；`in_flight`仍指同一target，并恰有该target生成的assignment item、同一Run及其prepared/dispatching/started start operation，fulfillment/termination全NULL；`fulfilled`不再有live owner，恰有同execution-target/formal-leg/item/mode/need-version的typed fulfillment receipt，历史consumed三元组要么全NULL（等待期间被其他合法结果满足）要么保持全套且相互匹配；`terminal/superseded`无live owner且分别恰有同work+requirement的typed terminal/replacement receipt，不能借另一work、另一腿、另一mode或另一need的receipt。Header current requirement必须是最高generation且非historical corruption；关闭header要求current requirement已终态且无未来revision。Child存在而parent pointer为空、pointer存在却状态不允许、三项consumed lineage只填一部分，都在commit失败。

Eligible mint事务在插Run/assignment/gen0/start的同一commit，将attempt全部work requirements按稳定ID从`pending -> in_flight`并绑定exact assignment item/Run/start operation；任何work已`in_flight/fulfilled/terminal`或owner不是该attempt时，本attempt不得mint，按typed事实进入cutover/zero-Run read-back。Consumed但start RPC尚未发送、RPC outcome unknown、Workflow active或termination尚未settled都仍是`in_flight`，不能因为pending pointer已清或scheduler换revision再建第二owner。只有matching fulfillment或完整termination/no-run settlement能推进终态；如果证明从未发送且政策批准replacement，则先写typed replacement receipt终结旧requirement，再由受控need revision追加新generation，绝不复活旧行。Commit ACK丢失按work自然键、requirement generation和全套backpointer read-back，不另造work/owner。

Launch key也必须可复算：series-backed preimage至少是`collection-launch/series-v1 + series ID + attempt generation + planner revision ID/version/epoch + business occurrence group + ordered target/work-requirement set hash`；standalone preimage至少是`collection-launch/run-now-v1 + globally unique client idempotency key + run-origin intent + immutable request contract hash`。同preimage异合同fail-loud；不能使用raw cron tick、随机UUID或可变资源健康hash充当业务身份。

Schedule/partition/planner epoch、member或target变更只能走同一个series cutover。所有cutover与tick统一按`lineage-key advisory -> control -> schedule lineage/current revision -> stable series/current schedule/current planner/current pending -> partition/current revision -> campaign/formal legs/需要的cells或execution-target rows -> work headers/requirements稳定排序 -> slots/intents(若canonical) -> old/new planner revisions/targets -> old/replacement attempts -> operation final`短事务执行；planner-only cutover也不得跳过lineage前缀。`pending_disposition=none`只允许确实无pending。若有supplemental/top-up pending，old/new target成员与work requirement成员双向anti-join，原子terminal old、清pointer、切schedule/partition/planner revision并至多创建一个replacement pending。Canonical pending则必须复用完整prebind partition/replacement协议推进全部affected slots/intents后才能cancel old并绑定new attempt。Cutover后old evaluator或old-schedule tick因持锁后current revision/attempt CAS不符只能写`stale_schedule_noop`或回滚，绝不能consume。任何revision切换若无法证明old/new work不重叠不遗漏，series保持draining/quarantined且零Run；ACK丢失按operation及revision/attempt pointers read-back。

Cutover target disposition是封闭矩阵，不能由实现者自由解释：

| disposition           | old target | new target | work requirement与证据                                                                                                                    | 原子效果                                                                                                     |
| --------------------- | ---------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `carried_forward`     | 必须       | 必须       | old/new必须是同一work header、同一requirement generation；所有fulfillment/termination evidence均NULL                                      | pending owner从old attempt target精确切到new target；不得改变need、state或创建Run                            |
| `satisfied_elsewhere` | 必须       | NULL       | exact同campaign/formal-leg/item/mode/execution-target/need fulfillment receipt必须非NULL；其他evidence NULL                               | requirement从pending变fulfilled、清live owner；old attempt可据完整集合零Run终结                              |
| `newly_required`      | NULL       | 必须       | fulfillment/termination均NULL；new requirement必须由cutover冻结的current need-event member授权，或是同work header受控追加的下一generation | 建立唯一pending owner；不能复用已fulfilled旧generation                                                       |
| `in_flight_owned`     | 必须       | NULL       | 该requirement必须已有matching assignment-item/Run/start三元组且无终态receipt；所有cutover evidence NULL                                   | 保留old target ownership，series保持draining/跟随其终态；严禁建立new target owner或replacement attempt覆盖它 |
| `superseded`          | 必须       | NULL       | exact work+requirement typed terminal/replacement receipt必须非NULL；fulfillment receipt NULL                                             | 终结旧requirement并清owner；只有政策允许的受控新generation才能在另一行`newly_required`出现                   |

Constraint trigger按每个old、新revision的完整target集合重算这一矩阵：每个old target恰有一个disposition，每个new target恰有一个允许它出现的disposition；old/new work ID、requirement ID、need version和evidence NULL/XOR必须与表中对应行相等。`carried_forward`的owner pointer切换、`satisfied_elsewhere/superseded`的终态、`in_flight_owned`的draining blocker，以及old attempt terminal/new attempt pointer都必须同commit。不得使用opaque overlap hash代替这些成员anti-join。

Blocked top-up不能在资源恢复后盲发旧题。Campaign维护由execution-target fulfillment receipt重建的单调need-projection version/count/hash；planner revision冻结该投影及每个target当时need version。Launch先无锁解析这些IDs，按完整资源锁序后在campaign/target锁域重读：canonical-main仍按冻结主计划全量target，不因supplemental先满足cell而偷删primary；top-up则要求current need projection与planner revision完全一致且每个target仍outstanding。若等待期间manual/其他Run已满足部分旧target或新增arrears，launch整笔回滚、释放资源锁，再由上述series-cutover事务生成精确replacement target set；不得在持region/account/browser锁时反向启动planner cutover。若所有top-up targets已满足，old attempt以`targets_already_satisfied` terminal receipt零Run收口；部分变化必须replacement，不能原地删item或沿用旧evaluation。新arrears进入current replacement/下一business group，old/new target disposition证明每个need恰为outstanding、carried、fulfilled或superseded之一。

Assignment header的`run_execution_item_count/run_execution_item_set_hash`必须由这组规范成员生成并做双向anti-join，ordered hash preimage至少含`ordinal + segment + business_key + canonical_execution_mode + query/payload hash`，不能只存一个无法反推出ordinal/query/mode/payload的hash。每个item携`launch_attempt_id + launch_required_member_id`复合FK到父required member；deferred trigger逐项比较formal leg、source platform/region/mode、canonical execution mode/formal mode，并要求父required-member集合与frozen assignment中`DISTINCT launch_required_member_id`双向anti-join为零、每个item只属于一个matching member。`canonical_execution_mode`是执行/额度协议使用的normal、deep_think等模式；`formal_mode`是sampling policy映射后的cell维度，两者不得混用。同一query/business key的normal与deep_think可以是两个execution item/segment，即使policy把它们映射到同一formal leg/cell也不会被错误unique拒绝；同一execution mode重复才由复合unique拒绝。Run/assignment/start-outbox创建事务先插`assignment.state=building`，再从冻结launch/run input生成全部item member，校验父member覆盖、segment/formal-leg/mode mapping、ordinal连续、immutable query引用与canonical JSON hash，最后才把assignment转`frozen`并写start outbox；父集含normal+deep而items只含normal、deep item错指normal member、额外mode、缺、多、重排或hash不符都使整笔回滚。成员在frozen后不可UPDATE/DELETE，普通scheduler/worker只有读取权限。

Workflow start input、正常`ensure_execution_request()`、pre-start terminal materializer和run closure都只能从这张成员表按ordinal构造/compare同一item envelope；不能再信任outbox payload或Activity参数携带的另一份自由列表。Start outbox只保存assignment ID、member count/hash和完整input contract hash，consumer在start授权事务重读成员并重算；Temporal history仍记录确定性payload，但DB成员是其来源和ACK-loss compare基线。这样RPC从未发、没有Activity时也能确定性建立request/item/initial operation与neutral materialization。

Assignment header同时是终止协议的**权威父级gate**，不能只靠查child表是否存在。后文termination schema建立后，以`DEFERRABLE INITIALLY DEFERRED`复合FK把`(termination_root_id, run_pub_id)`绑定到同assignment root，把`(current_assignment_terminal_receipt_id, run_pub_id)`绑定到同assignment terminal receipt；受控deferred trigger再验证pointer组合：两者皆NULL表示尚未终结且尚无root，只有root非NULL表示termination进行中，两者皆非NULL表示由该root终结，只有terminal receipt非NULL只允许`normal_without_root`。所有root创建、expected-set/obligation扩张与terminal receipt提交都先`SELECT assignment FOR UPDATE`，在同一事务写child并CAS父级pointer/version；root首次建立及每次新增scope/member obligation都单调递增`termination_obligation_epoch`，post-terminal intent不递增。Normal effect在获取父行锁后的**新statement**直接要求两个pointer均NULL及expected `assignment_state_version`，不得用事务早先的ORM缓存或child-absence扫描授权。

在线受控状态转换统一使用PostgreSQL `READ COMMITTED`短事务；若调用方采用`REPEATABLE READ/SERIALIZABLE`，任何等待父行期间发生的并发修改必须让整个事务以serialization failure回滚并从入口重试，绝不能继续使用旧snapshot。这样assignment父行更新既是互斥点也是可见性事实，不依赖“锁住不存在的child行”或特定ORM刷新行为。

Fresh v1的Run、assignment和workflow-start outbox还必须由同一次`collection_launch_evaluation(outcome=eligible_consumed)`授权；三者不能在“scheduler先建配置、Activity以后再挡”的两段空窗中产生。Launch attempt是schedule occurrence/canonical intent或run-now idempotency key的永久业务身份，并以独立`launch_attempt_required_member`作为每代evaluation不可自报的父真源：launch-plan在attempt建立时从origin-intent formal legs×verified source-mode mapping生成完整`formal_leg + platform + region + canonical execution mode`集合，header count/hash与成员双向anti-join。相同formal leg若要求normal与deep_think就必须有两行，即使sampling最终映到同cell。每个evaluation member复合指回父成员并与父集合双向anti-join；assignment item/segment也通过`launch_required_member_id`覆盖父集合，不能让本代evaluation或Run自洽地漏掉一腿。

同一snapshot持续blocked时利用`UNIQUE(attempt,snapshot_hash)`read-back同一evaluation，不因每个tick刷一条“配置”。只有region projection event、binding/session/gate/context/readiness或current quota bucket ledger version/counter等授权事实实际变化后才追加下一evaluation generation。Blocked evaluation可以commit作为独立运维证据，但必须满足`consumed_run_pub_id=NULL`，且同事务中Run/assignment/start operation/outbox新增数均为0；eligible evaluation则必须与Run、assignment、start operation/outbox同commit并把attempt置consumed。Assignment使用`(launch_eligibility_evaluation_id,launch_attempt_id)`复合FK指向同attempt evaluation；commit-time trigger再断言evaluation=`eligible_consumed`、evaluation/attempt的`consumed_run_pub_id`都严格等于该assignment的`run_pub_id`、origin intent/campaign/required-member set相同，且Run、assignment、gen0/start operation/outbox恰各一套。反向也要求每条`eligible_consumed` evaluation恰有这一套child；任何半套、跨attempt借receipt、blocked evaluation挂Run或consumed attempt再mint第二Run都整笔回滚。

Launch deadline在mint前也必须线性化。Attempt从canonical occurrence/run-now contract冻结`deadline_policy`及两臂XOR：`service_clock_on_mint`必须带不可变`service_budget_ms`且absolute cutoff为NULL，只有成功mint assignment时才从control服务时钟开始预算，launch前pause不消耗；`absolute_launch_cutoff`必须带不可变absolute business deadline且service budget为NULL，无论global pause与否都继续计时。Deadline contract hash覆盖policy、budget/cutoff与minimum-validity margin，不能在mint时再读可变policy或caller参数。Eligible mint trigger要求assignment逐值复制这一合同：service分支的`service_deadline_tick_ms = checked control service tick + frozen service_budget_ms`，absolute分支的deadline严格等于attempt，另一臂字段全NULL。取得并重读全部授权行锁后，函数**最后**调用一次`clock_timestamp()`得到`eligibility_checked_at`（不能用事务开始时冻结的`now()/CURRENT_TIMESTAMP`），并用同一时刻比较absolute cutoff、region/session/readiness/policy effective-to及所有bucket半开区间。若在取锁期间跨午夜导致current bucket集合改变，或任何预解析集合与该时刻不一致，整次回滚并从入口按新集合重试，绝不能在bucket步骤之后补锁新桶。Eligible要求所有有限resource TTL及absolute arm都覆盖同一个margin：`eligibility_checked_at + launch_min_validity_ms <= valid_until/absolute_business_deadline_at`，等号允许；差1毫秒也不足时直接走expired terminal CAS而非mint。检查后只允许对已锁行做确定性插入/CAS并立即commit，不再执行外部I/O、等待schedule锁或取得新业务锁。这里承诺的是checked-at时刻合法且有最小余量；Activity grant仍在发送前最终重检。

当`absolute_launch_cutoff`满足`eligibility_checked_at + launch_min_validity_ms > absolute_business_deadline_at`时，不再写普通blocked evaluation，而是在同一launch-key线性化下写唯一terminal receipt、把attempt置`expired`并证明Run/assignment/start descendant均为0；reason区分deadline已过与remaining window不足。旧blocked evaluation保留审计但永远不能在deadline后转eligible。Canonical-main的所有matching current primary slots同commit从`awaiting_run/replacement_pending -> launch_expired`并以terminal-slot members做双向anti-join；它们没有被fulfilled，也不自动选supplemental。后续只有现有audited prebind partition/replacement协议能追加新slot revision、建立带新deadline contract的successor intent/attempt并进入`replacement_pending`。Deadline reconciler属于effect-none控制面，即使global pause也可做这个terminal CAS；pause跨过absolute deadline后resume只能read-back expired receipt，不能mint一个一启动就过期却占掉主批次的Run。Service-clock policy则在pause后仍可正常mint，并从该次assignment冻结的service tick开始计时。

Launch terminal lineage必须双向约束：attempt的`(terminal_receipt_id,id,campaign_id,run_origin_intent_id)`以DEFERRABLE复合FK指向同attempt/campaign/intent receipt，receipt反向`launch_attempt_id UNIQUE`；constraint trigger强制`waiting_eligibility`时run/terminal pointer均NULL，`consumed`时只有matching eligible evaluation+consumed run、terminal pointer为NULL，`expired/cancelled/satisfied_without_run`分别且仅分别对应`expired/cancelled/targets_already_satisfied` receipt，consumed run及eligible-consumed evaluation均不存在，child存在当且仅当parent pointer指回。`targets_already_satisfied`只允许非canonical top-up，且必须由全部attempt targets的typed fulfillment/need receipts双向证明当前不再需要；它不能释放或改变primary slot。不可判断的安全错误只把stable series置quarantined并保留pending attempt/pointer，不虚构attempt terminal。Receipt中三个descendant count不能自报0：trigger对`CollectionRun/assignment/gen0/start operation/outbox`按attempt/origin实际anti-join，任一真实descendant都拒绝terminal commit。Terminal slot member再以复合FK绑定receipt的attempt/campaign/intent、该intent的formal-leg member、以及immutable `primary_slot(id,campaign,formal_leg)`与`slot_revision(id,slot)`；terminal事务持slot锁并断言当时current revision等于member snapshot，但**不建立历史receipt到mutable current pointer的FK**。Member集合必须等于该canonical-main intent formal legs去重后的全部当时current slots，且只有`terminal_kind=expired`能设置`launch_expired`。多execution-mode映射同formal leg只产生一个slot member；后续audited replacement可正常切current revision而不被历史FK阻断。

首版禁止裸cancel canonical-main attempt而让slot悬挂：supplemental/top-up可用cancelled receipt且affected slot count=0；canonical-main的普通时间终结只能expired→全matching slots `launch_expired`。若要在deadline前取消/隔离canonical-main，必须由完整prebind partition/replacement事务先原子推进全部old slots到successor revisions/intents，再让old attempt以`cancelled + superseding_partition_operation_id`终结且证明它已不拥有任何current slot；无法安全替代时quarantine **series**、不终结attempt。跨attempt/campaign/intent/slot/revision拼接、attempt terminal却无receipt、child存在pointer为空、伪造zero count、terminal与consumed并存、bare canonical cancel、commit ACK重试再造receipt都必须失败或read-back同一winner。

每个required formal-leg/execution-mode成员必须在规范member表有一行；header/member count/hash双向anti-join。账号候选不是事务外随手挑中的一行：每个platform/region/mode使用带current pointer/epoch的verified candidate-pool revision，revision的规范member冻结允许参与的全部正式account及operator priority。Pool更新通过append-only revision并在region→pool→identity/account统一锁序切pointer/epoch；普通scheduler不能现场加候选。Launch事务外只解析**完整候选ID集合**，事务内先锁并重读pool header/current revision，再按`selection_rank, platform_account_id`和全局资源ID序锁定、评估全部candidate；禁止`LIMIT 1`、first row、缓存winner、early exit或`SKIP LOCKED`把坏账号误当“全组不可用”。每个candidate及其平台policy要求都必须落规范化candidate/scope-requirement observation行；requirement行以`required_scope_kind/mode + expected period set`始终存在。Candidate缺binding/subject/config/policy/scope时用path-specific `resolution_outcome/reason`保存缺失事实，resolved ID/gate字段必须全NULL且bucket observation child必须为0。只有scope成功resolved时这些ID/gate字段才全部非NULL，并要求每个expected period恰有一条bucket observation：DB checked-at命中的current bucket存在时走`resolved`并冻结完整snapshot，不存在时走`bucket_missing`、bucket字段全NULL且capacity=false。这样午夜reconciler漏建1/3桶仍可合法commit为`quota_bucket_unavailable`且零Run；桶建成后bucket observation/hash变化才能重评。Candidate/requirement/period-observation count/hash与pool/platform requirement/current account policy分层重算，不能为了让blocked行可写而伪造scope/bucket ID，也不能只把缺失或可变counter藏进opaque hash。Deferred path trigger明确验证：`account_unresolved/config_unavailable/policy_unavailable/scope_missing`各自只能出现在相应父证据确实缺失的分支；scope resolved时period observation与expected set双向anti-join，任一`bucket_missing`都使candidate非eligible；candidate eligible要求所有requirement resolved、所有period bucket存在且容量可用。

`eligible_consumed`要求每个required member至少一个candidate完整eligible；winner是eligible集合按冻结selection policy的纯函数，member的selected ordinal及冗余account/binding/browser字段必须复合指回该candidate。只有**全部candidate**逐项不eligible时该member才blocked，候选为空也以明确`account_binding_missing`保存零集合证据。逐项冻结：current region derived ok+fresh及其immutable projection event；candidate的verified external identity/account binding与未过期current session；quota config gate/current policy、本mode required stable-scope集合/gate epoch/effective-to，以及每个current bucket的ID/边界/baseline/limit/reserved/debited/available/**单调ledger version**；subject global/mode governance；browser health、exact policy、current clean context和有效readiness receipt。Evaluation/member/candidate/scope/bucket五级ordered hash必须覆盖所有规范行，deferred trigger按行重算且检查`eligibility_checked_at`落入每个resolved current bucket半开区间。Quota bucket任何影响授权的变化都推进ledger version，所以quota=0或available=0形成的blocked snapshot在release、debit、adjustment、rollover或policy limit变化后必然得到新hash/new generation；pool revision、候选binding/gate变化也必然改变snapshot；事实未变的重复tick则只read-back原evaluation。某member存在A不可用、B可用时必须选择B，不能因A排序更前阻断；但一个member的eligible账号不能替另一个required member填空，豆包normal/deep_think也不能只验证其中一腿。这个launch receipt只是“创建时可行”，不预占quota/fence；Activity grant仍执行完整最终授权，所以launch后资源变化会让同一Run等待/终结，而不是绕过额度或创建第二Run。

每条evaluation member的`region_projection_event_id`都有普通FK到immutable `collection_region_health_event`，并由deferred trigger使用`IS NOT DISTINCT FROM`逐项断言其`region_id/health_epoch/applied_projection_generation/effective_state/effective_source/effective_fresh_until/policy_version`等于该event的new授权投影；不能依赖含NULL freshness时会跳过的默认`MATCH SIMPLE`复合FK。Blocked region允许event freshness为NULL；eligible member则额外要求event state=`ok`、freshness非NULL且覆盖selected-path minimum-validity margin。多个formal-leg/mode member可以合法引用同一event，不得加`UNIQUE(evaluation,event)`；实际加锁时required region ID跨member去重、稳定排序且每region只锁一次，审计snapshot仍逐member保留。

Minimum-validity只沿真正授权路径聚合：每个candidate用它自己的session/policy/readiness/bucket期限判断candidate eligibility；未选中的坏/短TTL candidate仍完整写审计/hash，但不进入整批minimum。选出每个member winner后，header的`minimum_selected_path_valid_until`只取shared control/region期限与各member **selected candidate→resolved scope→bucket**闭包的最小值，并由deferred trigger从selected ordinal重算。因而rank更高但短/坏的A不会压掉长且完整的B；winner本身任一TTL不足margin才使该member blocked。

Blocked reason也必须确定性：每版launch eligibility policy冻结一份有序reason precedence（首版按`global_not_open -> launch_contract_conflict -> region_unavailable -> region_stale -> account_binding_conflicted -> account_binding_missing -> account_session_unverified -> governance_blocked -> quota_config_unavailable -> quota_scope_unavailable -> quota_bucket_unavailable -> quota_exhausted -> browser_health_unready -> browser_context_unready`，同等级再按member ordinal、candidate rank/account ID）。事务必须对全部required members、全部候选及其全部required scopes完成评估并落行，禁止遇到第一个失败就early-return；candidate、member和evaluation header reason逐层由完整outcome集合按冻结precedence计算，precedence version/hash与全部reasons一并进入evaluation contract/snapshot hash。数据库返回顺序、query plan或candidate resolver顺序改变时，同一事实必须生成完全相同的rows、winner、header reason与hash；以后改优先级要新policy version，不能原地改义历史evaluation。`evaluation_snapshot_hash`明确**不含**每次调用都会变化的raw `eligibility_checked_at/committed_at`；它覆盖不可变policy、规范资源快照、真实expiry/bucket边界及在checked-at推导出的outcome/reason。否则同一blocked事实会每tick刷新行。跨TTL/午夜后的状态变化由outcome/reason或新current bucket ID/version进入新hash。

`expected_worker_release_id`必须指向可信部署角色预注册的immutable registry，不能只信可重复使用的build label：

```text
collection_worker_artifact_release
id, deployment_name, worker_build_id
binary_artifact_digest, config_contract_hash, task_queue_set_hash
supported_protocol_min, supported_protocol_max
state: approved / revoked
registered_by, registered_at, evidence_hash
UNIQUE(deployment_name, worker_build_id)
UNIQUE(deployment_name, binary_artifact_digest, config_contract_hash)

collection_workflow_definition_release
id, workflow_type, release_name
source_artifact_digest, temporal_sdk_version
baseline_marker_id, baseline_command_trace_hash
patch_set_hash, patch_count
worker_deployment_name, worker_build_id
supported_protocol_min, supported_protocol_max
state: draft / approved / draining / retired / revoked
registered_by, registered_at, evidence_hash
UNIQUE(workflow_type, release_name)
UNIQUE(workflow_type, source_artifact_digest, patch_set_hash)

collection_workflow_patch_registry
patch_id PRIMARY KEY
introduced_definition_release_id
change_kind, callsite_fingerprint
old_branch_command_trace_hash, new_branch_command_trace_hash
workflow_type_set_hash, replay_fixture_set_hash
state: active / deprecation_proposed / legacy_replay_only
registered_by, registered_at, evidence_hash

collection_workflow_definition_compatibility
id
from_definition_release_id, to_definition_release_id, workflow_type
compatibility_kind: replay_patched / exact_pinned / can_boundary_upgrade
history_fixture_count, history_fixture_set_hash, replay_result_hash
state: approved / revoked
approved_by, approved_at, expires_at NULLABLE, revoked_at NULLABLE, revocation_reason NULLABLE
UNIQUE(from_definition_release_id, to_definition_release_id, compatibility_kind)

collection_assignment_workflow_routing_revision
id, assignment_id, routing_revision
predecessor_routing_revision_id NULLABLE
primary_workflow_definition_release_id
workflow_versioning_behavior
expected_workflow_task_deployment, expected_workflow_task_build_id
member_count, member_set_hash, compatibility_evidence_set_hash
state: prepared / approved / current / superseded / revoked
valid_from, created_at, approved_at NULLABLE
created_by, approval_evidence_hash
UNIQUE(assignment_id, routing_revision)
partial UNIQUE(assignment_id) WHERE state='current'

collection_assignment_workflow_routing_revision_member
routing_revision_id, definition_release_id
member_kind: primary_exact / replay_compatible / can_boundary_target
compatibility_evidence_id NULLABLE
compatibility_kind, evidence_expires_at_snapshot NULLABLE
definition_release_contract_hash, evidence_contract_hash
UNIQUE(routing_revision_id, definition_release_id)
CHECK(primary_exact iff compatibility_evidence_id IS NULL)

collection_workflow_task_routing_receipt
id, assignment_id, workflow_id, workflow_run_id
workflow_routing_revision_id, workflow_routing_member_definition_release_id
workflow_definition_release_id, workflow_patch_set_hash
expected_versioning_behavior, expected_deployment, expected_build_id
actual_deployment, actual_build_id, temporal_routing_evidence_hash
proof_kind: producer_workflow_task / verified_lifetime_pin
producer_workflow_task_completed_event_id
scheduled_activity_event_id NULLABLE
first_workflow_task_event_id, last_verified_workflow_task_event_id
result: matched / mismatched / unverified
verified_at
UNIQUE(workflow_run_id, last_verified_workflow_task_event_id)
partial UNIQUE(workflow_run_id, producer_workflow_task_completed_event_id,
               scheduled_activity_event_id) WHERE scheduled_activity_event_id IS NOT NULL
```

Assignment分别以FK冻结Activity artifact release、初始Workflow definition release和initial/current routing revision，并冗余digest/config/patch-set/routing contract做compare；同一deployment/build不能指向两个artifact或两套definition语义。PINNED revision成员恰为exact primary release；unversioned-patched/AUTO_UPGRADE revision只能包含有matching `replay_patched`证据且候选build对冻结history corpus通过的approved releases，不能用“patch set是超集”自行推断。Revision header的count/hash与member/evidence集合双向anti-join，assignment current pointer以expected revision CAS推进；prepare/approve/current切换和ACK丢失均read-back同一revision，旧revision/member永久保留。

首版禁止把兼容集合**原地扩进已经active的chain generation**。扩展只能追加新routing revision并切assignment current pointer，供尚未start的generation 0或已经prepare的下一次CAN intent显式冻结；既有generation继续引用旧revision，Temporal若把它路由到只存在于新revision的release就mismatch/fail-closed。Compatibility edge的`expires_at`定义为“最后允许被新routing revision/新chain generation采用的时间”：到期不改写已经冻结的revision或历史routing receipt，但禁止新start/CAN引用并触发提前告警；若证据后来被判定错误，必须显式`revoked`，在同一受控流程pause/drain所有引用它的live generation并推进effect epoch，不能把revocation伪装成普通expiry。Patch registry的`patch_id/callsite/branch trace`append-only，批准release时CI从源代码生成manifest并双向anti-join，缺、多、改义都拒绝。Routing receipt来自Temporal实际describe/history/visibility或经验证Server API，必须指向generation冻结revision中的真实member；DB自报字段不能改变Temporal已经选择的Workflow Task worker。

Assignment与首个routing revision存在双向FK，必须显式解决首插循环，不能把pointer随意nullable后分两事务补。受控创建函数预生成assignment/revision ID，在**同一事务**插入`assignment.state=building`、revision 0与全部members，随后回填assignment initial/current pointer并转`frozen`；`(assignment_id, revision_id)`复合FK使用`DEFERRABLE INITIALLY DEFERRED`，commit-time constraint trigger验证initial=current=同assignment revision 0、恰一current、header count/hash与member/evidence anti-join为零、definition/patch/routing字段一致。任何缺项使整个事务回滚；API/worker/start consumer只接受`frozen`，building不具任何effect/start权限。Routing revision扩展同样在一笔事务先建完整prepared revision/member，验证后切旧current→superseded、新revision→current并更新assignment pointer；ACK丢失按预生成ID/read-back，绝不暴露半套revision。

所有fresh v1 Run——scheduler、run-now、canonical main、supplemental/top-up、API、CLI或reconciler——都只能由§11.3统一`evaluate_and_mint_collection_run()`在同一`eligible_consumed`事务创建；不存在第二个只按`control -> campaign`直接mint的函数、角色或trigger。该统一事务的前半段必须先完成`control -> regions -> candidate pools -> 全候选identity/account/session/quota/browser/context/buckets`锁定与完整eligibility snapshot；本段只描述它在**后半段**继续按`campaign -> formal leg -> primary slot/revision -> origin intent -> launch attempt/evaluation final -> Run/roles -> assignment/items -> gen0/start operation/outbox`消费已冻结intent并计算role。Intent CAS、eligible evaluation、Run、role assignments、protocol assignment和start outbox必须同commit；相同launch key/intent reload并compare，不同Run不能复用。任何旧`create_run()`/ORM direct insert只能服务显式隔离的protocol=0历史迁移且生产权限已撤销，绝不能成为fresh v1旁路。

Start consumer不得自行改版本、路由、deadline、sampling mapping、origin/role或persist policy。`ensure_execution_request()`只从这条DB assignment复制deadline/policy并做contract compare，不相信Activity payload提供的新值。新的execution request/reservation/result/task都比较该assignment。DB trigger/受控函数禁止workflow start后把v0原地升级v1；安全迁移是停止旧workflow，明确处置其发送事实，创建带predecessor/supersedes审计的新v1 run/workflow。`collection_dispatch_control.protocol_version`是允许创建/启动和签发新grant的最低版本：contract/enforce后，新run/start outbox/request低于该版本一律fail-closed。

“本run是否收口”和“整个采样campaign是否完成”必须是两层模型，绝不能共用一个count/hash。Run层的`run_execution_item_count/hash`只描述本run实际执行的request items（包括其mode operation），供run closure使用；它不直接等于采样分母。

采样层建立权威、版本化映射：

```text
collection_sampling_policy_version
id, policy_version UNIQUE, mapping_contract_hash
state: draft / verified / active / retired
expected_formal_leg_count, verified_by, verified_at, evidence_hash

collection_sampling_campaign
id, campaign_key UNIQUE, sampling_policy_version_id
canonical_query_count, canonical_query_set_hash
expected_cell_count, expected_cell_set_hash
expected_primary_slot_count, expected_primary_slot_set_hash
expected_execution_target_count, expected_execution_target_set_hash
current_execution_need_event_id, execution_need_projection_version
outstanding_execution_target_count
outstanding_execution_target_set_hash
launch_plan_version, launch_plan_contract_hash
state, created_at

collection_sampling_formal_leg
id, sampling_policy_version_id, formal_leg_key
canonical_model, canonical_region, formal_mode
candidate_mode_mapping_hash
UNIQUE(sampling_policy_version_id, formal_leg_key)

collection_sampling_formal_leg_source_mapping
id, sampling_policy_version_id, formal_leg_id
source_platform, source_model, source_region, source_mode
canonical_execution_mode, candidate_kind
state: verified / retired
UNIQUE(sampling_policy_version_id, source_model, source_region, source_mode,
       canonical_execution_mode, candidate_kind)
UNIQUE(id, sampling_policy_version_id, formal_leg_id, source_platform,
       source_model, source_region, source_mode, canonical_execution_mode)
UNIQUE(id, formal_leg_id, source_platform, source_region, source_mode,
       canonical_execution_mode)

collection_sampling_cell
id, campaign_id, formal_leg_id, canonical_item_key, query_hash
state: unresolved / resolved, selected_candidate_id NULLABLE, selection_version
UNIQUE(campaign_id, formal_leg_id, canonical_item_key)

collection_sampling_execution_target
id, campaign_id, cell_id, formal_leg_id, canonical_item_key
canonical_execution_mode, source_mapping_id
source_platform, source_region, source_mode
query_hash, target_contract_hash
current_need_revision_id, current_need_version, current_need_state
UNIQUE(campaign_id, formal_leg_id, canonical_item_key, canonical_execution_mode)
UNIQUE(id, campaign_id, cell_id, formal_leg_id, canonical_item_key,
       canonical_execution_mode, source_mapping_id, source_platform,
       source_region, source_mode)
composite FK(source_mapping_id, formal_leg_id, source_platform, source_region,
             source_mode, canonical_execution_mode)
  -> sampling_formal_leg_source_mapping(id, formal_leg_id, source_platform,
                                        source_region, source_mode,
                                        canonical_execution_mode)
composite DEFERRABLE FK(current_need_revision_id, id, campaign_id,
                        current_need_version, current_need_state)
  -> sampling_execution_target_need_revision(
       id, execution_target_id, campaign_id, need_version, state)

collection_sampling_execution_target_need_revision
id, execution_target_id, campaign_id, need_version
state: outstanding / fulfilled / waived
fulfillment_receipt_id NULLABLE, waiver_receipt_id NULLABLE
predecessor_need_revision_id NULLABLE
need_contract_hash, created_at, terminal_at NULLABLE
UNIQUE(execution_target_id, need_version)
UNIQUE(execution_target_id, campaign_id, need_version)
UNIQUE(id, execution_target_id, campaign_id, need_version)
UNIQUE(id, execution_target_id, campaign_id, need_version, state)
composite DEFERRABLE FK(fulfillment_receipt_id, id, execution_target_id,
                        campaign_id, need_version)
  -> sampling_execution_target_fulfillment_receipt(
       id, target_need_revision_id, execution_target_id, campaign_id, need_version)
composite DEFERRABLE FK(waiver_receipt_id, id, execution_target_id,
                        campaign_id, need_version)
  -> sampling_execution_target_waiver_receipt(
       id, target_need_revision_id, execution_target_id, campaign_id, need_version)
CHECK(outstanding iff both receipt pointers NULL;
      fulfilled iff only fulfillment pointer non-NULL;
      waived iff only waiver pointer non-NULL)

collection_sampling_execution_target_fulfillment_receipt
id, target_need_revision_id, execution_target_id, campaign_id, need_version
formal_leg_id, canonical_item_key, canonical_execution_mode, source_mapping_id
sampling_candidate_id UNIQUE, task_result_revision_id, submission_operation_id
source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot
answer_eligible, answer_degraded, need_operation_id
evidence_hash, applied_at
UNIQUE(id, execution_target_id, campaign_id, need_version)
UNIQUE(id, target_need_revision_id, execution_target_id, campaign_id, need_version)
UNIQUE(execution_target_id, need_version)
UNIQUE(id, execution_target_id, campaign_id, formal_leg_id,
       canonical_item_key, canonical_execution_mode, need_version)
composite FK(sampling_candidate_id, campaign_id, execution_target_id, need_version,
             formal_leg_id, canonical_item_key, canonical_execution_mode,
             source_mapping_id, task_result_revision_id, submission_operation_id,
             source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot,
             answer_eligible, answer_degraded)
  -> sampling_cell_candidate(id, campaign_id, execution_target_id, need_version,
                             formal_leg_id, canonical_item_key,
                             canonical_execution_mode, source_mapping_id,
                             source_task_result_revision_id_snapshot,
                             source_submission_operation_id_snapshot,
                             source_assignment_item_id_snapshot,
                             source_launch_target_item_id_snapshot,
                             answer_eligible, answer_degraded)
CHECK(answer_eligible=true AND answer_degraded=false)

collection_sampling_execution_target_waiver_receipt
id, target_need_revision_id, execution_target_id, campaign_id, need_version
formal_leg_id, canonical_item_key, canonical_execution_mode
waiver_policy_version, actor, reason, evidence_hash, need_operation_id, applied_at
UNIQUE(id, execution_target_id, campaign_id, formal_leg_id,
       canonical_item_key, canonical_execution_mode, need_version)
UNIQUE(execution_target_id, need_version)
UNIQUE(id, target_need_revision_id, execution_target_id, campaign_id, need_version)

collection_sampling_execution_need_operation
id, operation_key UNIQUE, campaign_id
operation_kind: campaign_freeze / fulfillment / waiver / reopen / migration / repair
expected_old_projection_version, expected_old_set_hash
member_count, member_set_hash, resulting_event_id NULLABLE UNIQUE
state: prepared / completed / quarantined
actor, reason, evidence_hash, created_at, completed_at NULLABLE
UNIQUE(id, campaign_id)

collection_sampling_execution_need_operation_member
need_operation_id, campaign_id, member_ordinal
execution_target_id, old_need_version, new_need_version
action: create_outstanding / fulfill / waive / reopen
fulfillment_receipt_id NULLABLE, waiver_receipt_id NULLABLE
member_contract_hash
UNIQUE(need_operation_id, member_ordinal)
UNIQUE(need_operation_id, execution_target_id)
CHECK(receipt NULL/XOR matches action)
composite FK(need_operation_id, campaign_id)
  -> sampling_execution_need_operation(id, campaign_id)
composite FK(execution_target_id, campaign_id, old_need_version)
  -> sampling_execution_target(id, campaign_id, need_version)
DEFERRABLE typed FKs bind fulfillment/waiver receipt to this same target,
  campaign and old need version; reopen requires new=old+1, other actions keep it.

collection_sampling_execution_need_event
id, campaign_id, need_projection_version, source_operation_id UNIQUE
outstanding_target_count, outstanding_target_set_hash
event_contract_hash, applied_at
UNIQUE(campaign_id, need_projection_version)
UNIQUE(id, need_projection_version)
UNIQUE(id, campaign_id, need_projection_version, outstanding_target_set_hash)
UNIQUE(id, campaign_id, need_projection_version, outstanding_target_count,
       outstanding_target_set_hash)
composite FK(source_operation_id, campaign_id)
  -> sampling_execution_need_operation(id, campaign_id)

collection_sampling_execution_need_event_member
need_event_id, campaign_id, need_projection_version, member_ordinal
execution_target_id, target_need_version, target_contract_hash
UNIQUE(need_event_id, member_ordinal)
UNIQUE(need_event_id, execution_target_id)
UNIQUE(need_event_id, campaign_id, need_projection_version,
       execution_target_id, target_need_version)
composite FK(need_event_id, campaign_id, need_projection_version)
  -> sampling_execution_need_event(id, campaign_id, need_projection_version)
composite FK(execution_target_id, campaign_id, target_need_version)
  -> sampling_execution_target(id, campaign_id, need_version)

ALTER collection_sampling_campaign ADD DEFERRABLE composite FK
  (current_execution_need_event_id, id, execution_need_projection_version,
   outstanding_execution_target_count, outstanding_execution_target_set_hash)
  -> sampling_execution_need_event(id, campaign_id, need_projection_version,
                                   outstanding_target_count,
                                   outstanding_target_set_hash)

collection_sampling_run_origin_intent
id, intent_key UNIQUE, campaign_id
producer_kind: canonical_schedule / partition_continuation / run_now / top_up /
               retry_replacement / manual_recovery
run_class: canonical_main / supplemental / top_up
schedule_lineage_key, schedule_lineage_version, occurrence_key
intended_formal_leg_count, intended_formal_leg_set_hash
role_policy_version, origin_contract_hash
predecessor_origin_intent_id NULLABLE, prebind_partition_operation_id NULLABLE
state: frozen / bound / cancelled / superseded
bound_run_pub_id NULLABLE UNIQUE
created_at, bound_at
UNIQUE(id, campaign_id, producer_kind)

collection_sampling_run_origin_intent_leg
id, run_origin_intent_id, campaign_id, formal_leg_id, leg_ordinal
UNIQUE(run_origin_intent_id, formal_leg_id)
UNIQUE(run_origin_intent_id, leg_ordinal)
UNIQUE(id, run_origin_intent_id, campaign_id, formal_leg_id)
UNIQUE(run_origin_intent_id, campaign_id, formal_leg_id)

collection_sampling_primary_slot
id, campaign_id, formal_leg_id, slot_key
current_slot_revision_id
state: awaiting_run / launch_expired / fulfilled / replacement_pending / closed
created_at
UNIQUE(campaign_id, formal_leg_id)
UNIQUE(campaign_id, slot_key)
UNIQUE(id, campaign_id, formal_leg_id)
UNIQUE(id, campaign_id, formal_leg_id, current_slot_revision_id)

collection_sampling_primary_slot_revision
id, primary_slot_id, slot_revision
predecessor_revision_id NULLABLE
authorized_origin_intent_id
expected_schedule_lineage_key, expected_schedule_lineage_version, expected_occurrence_key
role_policy_version, role_contract_hash
reason: initial_campaign_freeze / audited_replacement / prebind_partition_continuation
actor, evidence_hash, created_at
UNIQUE(primary_slot_id, slot_revision)
UNIQUE(primary_slot_id, authorized_origin_intent_id)
UNIQUE(id, primary_slot_id)

collection_sampling_prebind_intent_partition_operation
id, operation_key UNIQUE, campaign_id, predecessor_origin_intent_id UNIQUE
expected_predecessor_state, expected_predecessor_contract_hash
expected_leg_count, expected_leg_set_hash
replacement_leg_count, replacement_leg_set_hash
continuation_leg_count, continuation_leg_set_hash
replacement_origin_intent_id UNIQUE
continuation_origin_intent_id NULLABLE UNIQUE
expected_new_intent_count, expected_new_intent_set_hash
state: prepared / completed / quarantined
actor, reason, evidence_hash, created_at, completed_at NULLABLE

collection_sampling_prebind_intent_partition_member
partition_operation_id, campaign_id, formal_leg_id
predecessor_primary_slot_revision_id
successor_kind: replacement / continuation
successor_origin_intent_id, successor_primary_slot_revision_id UNIQUE
member_contract_hash
UNIQUE(partition_operation_id, formal_leg_id)

collection_sampling_prebind_intent_partition_receipt
id, partition_operation_id UNIQUE, predecessor_origin_intent_id UNIQUE
predecessor_leg_count, predecessor_leg_set_hash
replacement_leg_count, replacement_leg_set_hash
continuation_leg_count, continuation_leg_set_hash
successor_intent_count, successor_intent_set_hash
old_slot_revision_count, old_slot_revision_set_hash
new_slot_revision_count, new_slot_revision_set_hash
partition_contract_hash, applied_at

collection_run_sampling_leg_assignment
id, run_pub_id, campaign_id, formal_leg_id
role: primary / supplemental
run_origin_intent_id, primary_slot_revision_id NULLABLE
source_schedule_lineage_key, source_schedule_lineage_version, source_occurrence_key
run_class, role_policy_version, role_evidence_hash
state: frozen / superseded
UNIQUE(run_pub_id, formal_leg_id)
partial UNIQUE(campaign_id, formal_leg_id) WHERE role='primary' AND state='frozen'
CHECK(role='primary' iff primary_slot_revision_id IS NOT NULL)

collection_run_sampling_segment
id, run_sampling_leg_assignment_id, run_pub_id, segment_ordinal
source_model, source_region, source_mode, ordered_item_slice_hash
UNIQUE(run_pub_id, segment_ordinal)

collection_sampling_cell_candidate
id, command_id UNIQUE, campaign_id, cell_id, formal_leg_id, canonical_item_key
execution_target_id, need_version, source_mapping_id, canonical_execution_mode
source_run_pub_id_snapshot, source_request_item_id_snapshot
source_task_id_snapshot, source_task_result_revision_id_snapshot
source_submission_operation_id_snapshot
source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot
analytics_answer_id_snapshot, source_platform, source_region, source_mode
source_generation, content_hash
answer_eligible, answer_degraded, eligibility_policy_version
state: eligible / ineligible / invalidated
UNIQUE(cell_id, source_task_result_revision_id_snapshot)
UNIQUE(campaign_id, source_task_result_revision_id_snapshot)
UNIQUE(id, campaign_id, execution_target_id, need_version, formal_leg_id,
       canonical_item_key, canonical_execution_mode, source_mapping_id,
       source_task_result_revision_id_snapshot,
       source_submission_operation_id_snapshot,
       source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot,
       answer_eligible, answer_degraded)
composite FK(command_id, campaign_id, cell_id, formal_leg_id,
             execution_target_id, need_version, source_mapping_id,
             canonical_execution_mode, canonical_item_key,
             source_task_result_revision_id_snapshot,
             source_submission_operation_id_snapshot,
             source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot,
             answer_eligible, answer_degraded)
  -> sampling_candidate_command(id, sampling_campaign_id_snapshot,
                                intended_cell_id_snapshot, formal_leg_id_snapshot,
                                execution_target_id_snapshot, target_need_version_snapshot,
                                source_mapping_id_snapshot,
                                canonical_execution_mode_snapshot,
                                canonical_item_key, task_result_revision_id,
                                source_submission_operation_id_snapshot,
                                source_assignment_item_id_snapshot,
                                source_launch_target_item_id_snapshot,
                                answer_eligible, answer_degraded)
composite FK(execution_target_id, campaign_id, cell_id, formal_leg_id,
             canonical_item_key, canonical_execution_mode, source_mapping_id,
             source_platform, source_region, source_mode, need_version)
  -> sampling_execution_target(id, campaign_id, cell_id, formal_leg_id,
                               canonical_item_key, canonical_execution_mode,
                               source_mapping_id, source_platform, source_region,
                               source_mode, need_version)

collection_sampling_candidate_command
id, command_key UNIQUE
source_run_pub_id_snapshot, source_request_item_id_snapshot
source_submission_operation_id_snapshot, task_id, task_result_revision_id
source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot
sampling_campaign_id_snapshot, intended_cell_id_snapshot
run_sampling_leg_assignment_id_snapshot, run_sampling_segment_id_snapshot
formal_leg_id_snapshot, execution_target_id_snapshot, target_need_version_snapshot
source_mapping_id_snapshot, canonical_execution_mode_snapshot
canonical_item_key, query_hash, source_platform, source_model, source_region, source_mode
result_content_hash, sampling_policy_version, mapping_contract_hash
analytics_answer_id_snapshot NULLABLE, analytics_content_hash NULLABLE
answer_eligible NULLABLE, answer_degraded NULLABLE, analytics_contract_hash NULLABLE
state: waiting_analytics / ready / claimed / applied / ineligible / poison
claim_token, claim_generation, claim_expires_at
candidate_id NULLABLE, completion_receipt_id NULLABLE
created_at, applied_at, last_error
UNIQUE(sampling_campaign_id_snapshot, task_result_revision_id)
UNIQUE(id, sampling_campaign_id_snapshot, intended_cell_id_snapshot,
       formal_leg_id_snapshot, execution_target_id_snapshot,
       target_need_version_snapshot, source_mapping_id_snapshot,
       canonical_execution_mode_snapshot, canonical_item_key,
       task_result_revision_id, source_submission_operation_id_snapshot,
       source_assignment_item_id_snapshot, source_launch_target_item_id_snapshot,
       answer_eligible, answer_degraded)

collection_sampling_candidate_completion_receipt
candidate_id UNIQUE, cell_id, applied_at

collection_sampling_cell_lifecycle_receipt
cell_id UNIQUE, first_eligible_candidate_id UNIQUE, policy_version, applied_at

collection_sampling_cell_selection_revision
cell_id, selection_version, selected_candidate_id, predecessor_selection_version
actor, reason, evidence_hash, applied_at
UNIQUE(cell_id, selection_version)
```

Sampling command是Task域与sampling域之间**唯一跨域outbox**，必须显式避免PostgreSQL FK trigger制造隐藏锁环：Task/Revision事务插command时只对同一Task域的Task/Revision建FK；run/request-item、campaign/cell/leg/segment字段全部是不可变ID/自然键/hash snapshot，**不从command建到execution或sampling父表的普通/deferred FK**。Task自身既有的v1 provenance约束仍按§6.8执行，但sampling command不新增一条锁边。Analytics resolver同样只用唯一command CAS补immutable answer ID/hash/eligibility snapshot，不让sampling事务反向锁analytics行。Command创建后Task域不得更新/删除它；只有analytics补全和sampling两阶段consumer可按列权限推进状态。Task/Revision/analytics表冻结后由ACL禁止改写关键列，受控函数、command contract hash和持续invariant负责跨域核对。

Projector两阶段claim command后释放claim事务，再按`campaign -> formal leg -> cell -> sampling-local candidate/receipt -> command final CAS`加锁；candidate只FK到command和sampling-local campaign/cell，不直接FK Task/Run/request/revision/analytics，避免持cell时由FK trigger反向取得Task `KEY SHARE`。数据库sampling-local复合FK/CHECK必须逐项证明formal leg/source mapping/cell属于campaign冻结的同一policy，candidate/completion/lifecycle receipt的cell等于command snapshot/candidate cell；受控函数再逐项比较command中的run leg/segment/canonical item与source-mode mapping，以及Task/revision/analytics的tenant/run/query/model/region/content hash snapshot。同一campaign内一个Task result revision最多落一个cell，不能靠`UNIQUE(cell,revision)`留下跨cell重复入口。Invariant SQL必须重新join两域发现任何snapshot漂移，但修复只能走审计reconciliation，不能在sampling事务反向更新Task。

Campaign只允许`draft -> frozen -> active -> closed`。`draft -> frozen`受控事务按verified formal-leg set × frozen canonical-query set生成/校验cells，用双向anti-join证明零缺、零多，count/hash等于两组笛卡尔积（当前fixture为136×6=816），再冻结policy、query/leg/cell count/hash；frozen后禁止增删cell、换policy/query set或原地修改mapping。政策升级创建新campaign/policy lineage或显式审计迁移，不在活跃campaign偷换分母。

同一campaign freeze还必须为**每个formal leg预先建立一个权威primary slot及slot revision 0**，并绑定在任何业务Run之前已经冻结的`canonical_main` run-origin intent、schedule lineage version和occurrence key。Primary intent来自经verified campaign launch plan生成的稳定DB行，不是scheduler进程传入的自由字符串，也不是Run创建后的解释；若现有schedule表不能提供immutable lineage/version/occurrence FK，就使用上述origin-intent表作为新真源。每个origin intent都用规范化的intent-leg成员行冻结其完整formal-leg集合，成员的count/ordered hash必须与intent header一致；不能只存一个无法反查成员的hash。Campaign的formal-leg set与primary-slot set做双向anti-join，保证每leg恰一slot、零缺零多；slot不改变cell分母。

`primary_slot.current_slot_revision_id`必须以同slot复合FK指向revision；revision 0没有predecessor，revision N必须引用同slot的N-1。受控函数与deferred constraint trigger保证current revision、authorized intent、campaign/formal leg、intent-leg membership、lineage/version/occurrence、role policy/hash全链一致。Slot是永久业务身份，不因换主而被替换：campaign freeze后状态为`awaiting_run`；matching primary assignment建立后为`fulfilled`；批准replacement并切换current revision后为`replacement_pending`，新assignment建立后再到`fulfilled`；campaign终结后才`closed`。不能直接改current revision、删除旧revision，或让`fulfilled`没有且仅没有一个匹配当前revision的frozen primary assignment。上述跨表条件不能伪装成普通PostgreSQL `CHECK`；必须由复合FK、deferred constraint trigger、受控`SECURITY DEFINER`函数和撤销表直接DML权限共同保证。

Role assignment受控函数固定按`campaign -> 全部目标formal leg排序 -> primary slot/current revision排序 -> run origin intent -> run-leg assignment`加锁并**自行计算role**。Run的每个leg必须先存在于该intent的冻结成员集；仅当run绑定的origin intent恰等于其**全部成员slot**当前revision授权的intent，且campaign/leg、`run_class=canonical_main`、schedule lineage/version、occurrence、role policy/hash全部匹配时才能写primary。`canonical_main` intent采用全有或全无校验：其成员集中任何leg未匹配current slot revision，整次Run创建回滚；不能把同一个main Run悄悄标成部分primary、部分supplemental。其他run-now、top-up、补采、错误lineage或正确lineage的错误occurrence即使先到也只能按冻结policy写supplemental或被拒绝，绝不能因slot尚空就升级primary。Caller传入的`role=primary`只可做contract compare，不能决定结果。Origin intent最多绑定一个业务Run；相同intent的幂等重试reload同一Run，不允许另一个Run抢slot。

Primary失败后也不把“最先完成/样本最多”的supplemental自动扶正。替代协议必须区分old origin intent是否已经绑定：

- **已绑定Run后的单leg/子集替代**：有权限的控制面按`campaign -> 受影响formal leg/slot排序 -> bound origin intent -> old run-leg assignment`锁定，只把目标旧frozen primary assignment标superseded，为目标子集建立新的`retry_replacement + canonical_main` intent、成员与`audited_replacement` slot revision，并把目标slot置`replacement_pending`。Old origin intent和未受影响leg的assignment/slot继续保留bound/fulfilled历史事实，不能为了替一腿取消整个已绑定intent；
- **尚未绑定multi-leg intent的子集替代**：禁止直接把old intent标cancelled/superseded后只切目标slot。必须用上面的prebind partition operation，在一个短事务按`campaign -> old成员formal leg全排序 -> 全部primary slot/current revision排序 -> predecessor origin intent -> partition operation final`锁定完整集合。设old成员集为`M`、审批替代子集为非空`R`：创建一个成员恰为`R`的`retry_replacement + canonical_main` intent；若`M-R`非空，同时创建一个成员恰为`M-R`的`partition_continuation + canonical_main` intent，它继承old的canonical schedule lineage/version/occurrence、role policy并冻结predecessor/partition contract。对`R`中每个slot追加`audited_replacement` revision，对`M-R`中每个slot追加`prebind_partition_continuation` revision，全部切current pointer并置`replacement_pending`，最后才把old frozen intent置superseded并写partition receipt。若`R=M`则continuation必须为NULL且所有slot都指replacement；
- Deferred constraint trigger对`M = R ⊎ (M-R)`做成员行级双向anti-join：两组不相交、并集零缺零多；每个successor intent的header count/hash恰等于映射给它的成员，每个old member slot恰推进一版并指向其successor，old intent/slot revision/新intent/partition receipt全链同campaign/operation。任何current slot指向cancelled/superseded intent、old成员未映射、同leg映射两次、continuation继承错误lineage/occurrence、或operation只改一部分slot都使整笔commit失败；
- Original schedule bind与partition使用完全相同的`campaign -> all member legs -> all slots -> old intent`前缀：bind先赢则prebind partition因old已bound而回滚，调用方只能转已绑定替代流程；partition先赢则old bind看到superseded/current revision漂移而回滚，只能分别消费新replacement/continuation intent。Operation任一killpoint/commit ACK丢失按operation key、old/new成员及slot revision set hash read-back，不重发第二套intent。

每个successor Run仍对自己的冻结成员子集全有或全无：其余legs可以随后由continuation Run合法成为primary，不会因替了一腿永久留在`awaiting_run`。Slot revision、partition receipt和历史assignment不可删除/改写，普通run-now/top-up不能创建replacement/partition revision。UI/历史记录的“主批次”只读每个slot current revision及其matching frozen assignment；slot为`awaiting_run/replacement_pending`时显示“主计划未到/失败待替代”，`launch_expired`显示“主计划启动期限已过，等待审计替代”，不能回退到第一条Run，也不能把指向失效intent的slot伪装成等待中。`launch_expired`只由matching launch terminal receipt设置，不能直接改回awaiting/fulfilled；恢复必须追加audited replacement/partition revision。

权限也属于正确性合同：只有campaign launch-plan控制角色能在freeze事务创建initial canonical-main intent、intent-leg成员、slot和revision 0；只有独立replacement/partition控制角色能携审批证据追加replacement或prebind-partition revisions/intents/receipt；通用scheduler/run-now/top-up角色只能创建明确的supplemental/top-up intent或消费已经授权给它的intent，不能创建/修改canonical-main/partition intent、slot、revision、partition member/receipt或role assignment；worker、projector和UI全部只读这些身份字段。所有表撤销普通`INSERT/UPDATE/DELETE`，生产路径只执行上述窄受控函数。这样“改一个producer_kind字符串”或伪造caller role也不能重新引入主批次竞态。

Cell稳定身份是`campaign + policy-versioned formal_leg_key(model/region/formal-mode) + canonical query/item key`。Primary唯一性域是`campaign + formal_leg`，不是“整个campaign只有一个primary”，也不是按第一个创建/完成run猜。当前已知回归合同必须固定验证`136 queries × 6 formal legs = 816 cells`。豆包北京/上海的normal补采与deep_think候选按当前verified policy映射到既有formal leg/cell；只有policy明确标记为genuine dual-mode时才拆成两个formal legs/cells。不得直接用原始mode字符串判断，mapping变更必须新policy version、迁移/审计，不能静默扩分母。

如果一批含多个执行mode，fresh v1 workflow按冻结manifest拆确定性segment。Role落在唯一`run + formal leg` assignment上；同run的normal/deep等多个segment可引用同一leg assignment，不能出现“一段primary、一段supplemental”。Pre-marker history保持原命令序列，只做离线回放/已完成查询兼容。Run-item resolution只表示这次run已物化/抑制；它覆盖wall/neutral/aborted/suppress，绝不能直接推进采样。Task/Revision事务在同一commit写immutable sampling-candidate command，先释放Run/Task锁；analytics答案晚到时把同一command从waiting_analytics唤醒，workflow与后台projector只消费这一真源，ACK丢失按command/candidate/receipt唯一键read-back，不能靠Task commit后的best-effort回调。

只有绑定verified-answer revision且analytics answer满足当前真实合同`eligible=true AND degraded=false`的候选才是eligible。独立sampling projector不锁Task/Run，只读immutable revision/analytics eligibility，按campaign/formal-leg/cell固定序加锁：每个eligible candidate以completion receipt推进`completed_samples`一次，同cell可以因补采累计多份合格答案；只有该cell首条lifecycle receipt把`observed_cells`从0推进到1。UI采样进度使用`observed_cells / expected_cells`，不是completed samples、Task数或run resolved数。

Supplemental/top-up、normal/deep候选乱序到达、重复Task或后续selection换版只更新同cell候选/选择；可增加同cell的completed samples，但不扩分母、不重复observed cell。普通ineligible/degraded/suppressed/wall只关闭run item，不把campaign cell伪装observed；显式审计invalidating/reopen政策另行处理，不能删除lifecycle receipt或普通负delta掩盖历史。

Workflow-start consumer在调用Temporal前重新读取/锁定assignment与control最低版本，以确定性workflow ID构造input/memo/search attributes并写/恢复start intent。Temporal start成功或ACK未知时按workflow ID查询read-back并比较input contract hash，随后写下方显式`collection_workflow_start_receipt`：它必须同时绑定`start_operation_id + assignment_id + generation_zero_id`，并冻结protocol/queue、expected Activity worker release/deployment/build/artifact/config、routing revision/member set、Workflow definition/patch/versioning/expected Workflow Task deployment+build、workflow/run ID、input hash与started event；相同ID不同hash fail-loud。Generation 0只能冻结调用start前仍current且approved、member/evidence anti-join为零的routing revision，DB now晚于任一将用于新绑定的compatibility edge expiry时拒绝。若采用PINNED，start RPC必须显式携冻结versioning override并从Temporal read-back证明actual pin；采用unversioned-patched/AUTO_UPGRADE则不能假装已pin，必须依赖patch兼容。Receipt/ack丢失不能重复启动不同workflow。Temporal可能在start RPC返回/receipt commit之前调度首个Activity：Activity若看到matching frozen assignment+start intent但receipt尚缺，只能在任何request/grant/attach前返回明确retryable`workflow_start_receipt_pending`；start consumer/reconciler按deterministic workflow ID从Temporal read-back补receipt后再执行。只有hash/version/workflow ID不匹配才non-retryable。每个bootstrap/execution Activity仍必须在任何相应DB写、浏览器或外部副作用前从DB按run读取assignment并通过poller gate；start receipt不能代替Activity最终gate，Activity gate也不能代替Workflow Task replay/routing证明。

首次start与hard request之间必须有独立、可恢复的发送边界，不能把“start receipt尚未提交”解释为“Temporal一定没有启动”。Expand schema至少加入：

```text
collection_workflow_start_operation
id, operation_key UNIQUE, assignment_id UNIQUE, run_pub_id UNIQUE
workflow_id UNIQUE, input_contract_hash, routing_contract_hash
generation_zero_id UNIQUE
state: prepared / claimed / rpc_dispatching / outcome_unknown / started /
       cancelled_before_send / no_run_closure_pending / completed / quarantined
completion_kind NULLABLE: started / no_run
claim_token, claim_generation, claim_expires_at
current_rpc_attempt_id NULLABLE, start_receipt_id NULLABLE
created_at, completed_at, last_error
UNIQUE(id, assignment_id)
UNIQUE(id, assignment_id, generation_zero_id, run_pub_id, workflow_id,
       input_contract_hash, routing_contract_hash)

collection_workflow_start_rpc_attempt
id, start_operation_id UNIQUE, assignment_id, temporal_request_id UUID UNIQUE, rpc_request_hash
generation_zero_id, run_pub_id
state: prepared / dispatching / forwarded / outcome_unknown /
       confirmed_not_forwarded / resolved_started / quarantined
dispatch_journal_record_id UNIQUE NULLABLE
workflow_id_reuse_policy_snapshot, workflow_id_conflict_policy_snapshot
namespace_id_snapshot, namespace_retention_snapshot, retry_safety_deadline
temporal_workflow_id, input_contract_hash, routing_contract_hash
observed_temporal_run_id NULLABLE
observed_started_event_ref NULLABLE, observed_history_hash NULLABLE
created_at, resolved_at NULLABLE
UNIQUE(id, start_operation_id, assignment_id)
UNIQUE(id, start_operation_id, assignment_id, generation_zero_id, run_pub_id,
       temporal_workflow_id, input_contract_hash, routing_contract_hash)
composite FK(start_operation_id, assignment_id)
  -> start_operation(id, assignment_id)
composite FK(start_operation_id, assignment_id, generation_zero_id, run_pub_id,
             temporal_workflow_id, input_contract_hash, routing_contract_hash)
  -> start_operation(id, assignment_id, generation_zero_id, run_pub_id,
                     workflow_id, input_contract_hash, routing_contract_hash)

collection_workflow_start_receipt
id, start_operation_id UNIQUE, assignment_id UNIQUE, run_pub_id UNIQUE
generation_zero_id UNIQUE, start_rpc_attempt_id UNIQUE
protocol_version, task_queue
expected_activity_worker_release_id, expected_activity_worker_deployment
expected_activity_worker_build_id, expected_activity_worker_artifact_digest
expected_config_contract_hash
workflow_routing_revision_id, workflow_routing_member_set_hash
workflow_definition_release_id, workflow_patch_set_hash, versioning_behavior
expected_workflow_task_deployment, expected_workflow_task_build_id
workflow_id UNIQUE, workflow_run_id UNIQUE
input_contract_hash, routing_contract_hash
temporal_request_id UUID, rpc_request_hash
temporal_started_event_ref, applied_at
UNIQUE(id, start_operation_id, assignment_id)
UNIQUE(id, start_operation_id, assignment_id, generation_zero_id, run_pub_id,
       workflow_id, input_contract_hash, routing_contract_hash)
composite FK(start_operation_id, assignment_id)
  -> start_operation(id, assignment_id)
composite FK(start_operation_id, assignment_id, generation_zero_id, run_pub_id,
             workflow_id, input_contract_hash, routing_contract_hash)
  -> start_operation(id, assignment_id, generation_zero_id, run_pub_id,
                     workflow_id, input_contract_hash, routing_contract_hash)
composite FK(start_rpc_attempt_id, start_operation_id, assignment_id,
             generation_zero_id, run_pub_id, workflow_id,
             input_contract_hash, routing_contract_hash)
  -> start_rpc_attempt(id, start_operation_id, assignment_id,
                       generation_zero_id, run_pub_id, temporal_workflow_id,
                       input_contract_hash, routing_contract_hash)

collection_workflow_no_run_terminal_receipt
id, assignment_id UNIQUE, start_operation_id UNIQUE, hard_termination_request_id UNIQUE
generation_zero_id UNIQUE, cancelled_rpc_attempt_id NULLABLE
expected_item_count, expected_item_set_hash
terminal_manifest_count, terminal_manifest_set_hash
materialization_command_count, materialization_command_set_hash
proof_kind: never_issued / confirmed_not_forwarded
contract_hash, applied_at
UNIQUE(id, assignment_id)
composite FK(start_operation_id, assignment_id)
  -> start_operation(id, assignment_id)
composite FK(generation_zero_id, assignment_id)
  -> chain_generation(id, assignment_id)
composite FK(cancelled_rpc_attempt_id, start_operation_id, assignment_id)
  -> start_rpc_attempt(id, start_operation_id, assignment_id)
ALTER after root creation: composite FK(hard_termination_request_id, assignment_id)
  -> hard_termination_request(id, assignment_id)
```

所有表建立后再给start operation补三条`DEFERRABLE`复合FK：`(generation_zero_id,assignment_id) -> chain_generation(id,assignment_id)`、`(current_rpc_attempt_id,id,assignment_id) -> start_rpc_attempt(id,start_operation_id,assignment_id)`、`(start_receipt_id,id,assignment_id,generation_zero_id,run_pub_id,workflow_id,input_contract_hash,routing_contract_hash) -> start_receipt(id,start_operation_id,assignment_id,generation_zero_id,run_pub_id,workflow_id,input_contract_hash,routing_contract_hash)`；start receipt和RPC attempt自己的generation也必须指向同assignment gen0。Commit-time constraint trigger对gen0逐项断言`chain_generation=0 AND transition_kind=initial_start`，并断言operation/attempt/receipt的assignment、gen0、run、workflow、input/routing contract完全相等；attempt的`temporal_request_id/rpc_request_hash`还必须与receipt相等，receipt的`workflow_run_id/started_event`必须等于该attempt经可信read-back确认的actual run/event。不能只证明“属于同assignment”，也不能让同assignment后续generation、另一workflow或另一hash借用首次start证明。

同一个deferred trigger还必须执行双向state/backpointer矩阵，而不是只验证非NULL FK：存在RPC attempt当且仅当`start_operation.current_rpc_attempt_id`指回那一条唯一attempt；`rpc_dispatching/outcome_unknown`必须恰有该attempt且无start receipt；`started`以及`completed+completion_kind=started`必须恰有`resolved_started` attempt和matching receipt，operation与receipt双向指回；pre-send以及`cancelled_before_send/no_run_closure_pending/completed+completion_kind=no_run`不得有start receipt。No-run receipt的`never_issued`要求完全没有RPC attempt；`confirmed_not_forwarded`要求cancelled attempt非NULL、恰为operation唯一current attempt、属于同start/assignment/gen0/run/workflow/hash，并且immutable terminal state与dispatch journal都证明confirmed-not-forwarded。两种都要求matching termination root、gen0、start operation和完整neutral materialization。任何“child已存在但parent pointer为空”、parent状态声称started却缺receipt、started/no-run child并存或各列分别UNIQUE却跨lineage拼接的提交都必须在commit失败。

Run/assignment/start-outbox创建事务同时预建state=`intent`的generation 0及`prepared` start operation；两者完全effect-none。Start consumer先claim-only并提交，apply再按`control -> assignment -> generation 0 -> live hard request -> start operation final claim CAS`复核；只有无hard request时才创建唯一RPC attempt并提交`rpc_dispatching`发送授权，之后才可在无DB锁时调用Temporal。`rpc_dispatching`是保守边界：除非可信发送journal给出`confirmed_not_forwarded`，否则进程在真正调用前崩溃也按outcome unknown处理。

这个DB→gRPC空窗不能靠“永不重发”制造永久卡死。唯一RPC attempt必须冻结一个由operation确定性派生且永久复用的Temporal `request_id`、完全相同的序列化start envelope/hash，并显式设置`WorkflowIDReusePolicy.REJECT_DUPLICATE`与`WorkflowIDConflictPolicy.FAIL`（禁止依赖SDK默认值，也禁止`USE_EXISTING`掩盖同workflow ID合同冲突）。恢复时允许再次发送**同一logical attempt**，但不得创建第二attempt、换request ID/workflow ID/input/memo/search attributes/routing override或政策；server dedupe response、`WorkflowAlreadyStarted`和describe/history都只能作为候选证据，必须逐项核对actual run的WorkflowExecutionStarted cause/type/input hash、assignment/routing contract和唯一run ID后才写receipt。若目标Temporal Server/SDK实际语义不能证明同request ID重放在该namespace/retention窗口内绝不会创建第二run，必须改用有fsync accepted/confirmed-not-forwarded记录的专用start gateway；两者都未验证时保持全局paused，不能上线。

`retry_safety_deadline`取实测server request-dedup保证、namespace retention/archival可查询窗口和业务恢复上限的最小值。期限内按同request ID有界重试并read-back；期限外、retention已删除、archival不可查或NOT_FOUND语义不确定时只能`quarantined/waiting_start_resolution`并告警，不得再次Start。一次“当前查不到workflow”不是`confirmed_not_forwarded`，不能据此取消intent、另起workflow ID或把hard/cancel请求判完成。

Pause不能让这个安全窗口悄悄耗尽。已经在pause线性化点前进入`rpc_dispatching/outcome_unknown`的唯一logical attempt获得严格的`start_resolution_only` cleanup ceiling：`pause_requested/paused/draining`期间专用reconciler仍可describe/history/archival，并可在`retry_safety_deadline`内以**同一个request ID与完全相同envelope**做上面的幂等重放；它可能补出一个effect-none `bootstrap_pending/closing` run，但control/assignment/termination gate保证bootstrap、request、grant、attach和submit仍为0。它绝不能领取新的prepared start operation、创建第二logical attempt或改变routing。Pause quiescence snapshot必须列出每个unresolved start operation、request ID hash、状态、安全期限和resolution owner；`paused`只表示没有effect permission，不得把这些行伪装成已取消。Resume对每个assignment先要求start已matching bound/terminal，或保持该assignment quarantined/termination pending；不能因全局resume越过一个已过安全期限的unknown start。

Hard request与start consumer都以assignment排他/条件锁作为线性化点，并覆盖三个互斥边界：

1. Start operation仍`prepared/claimed`且没有已签发RPC attempt，或唯一attempt有可信`confirmed_not_forwarded` journal receipt：hard request同一事务把start operation置`cancelled_before_send/no_run_closure_pending`、generation 0 `intent -> rejected`，冻结全量waiting item的neutral terminal/materialization集合；物理target与Temporal RPC集合必须为空。只有双向anti-join、terminal manifest和materialization command全部齐全后，才写`no_run_terminal_receipt`并把request/assignment终结。任何后到start claim/RPC发送授权因expected state/assignment termination epoch失败。
2. 唯一RPC attempt已`dispatching/forwarded/outcome_unknown`，但start receipt或actual run尚未绑定：termination request进入`waiting_start_resolution`并立即成为assignment级normal-effect gate；start consumer不得创建新logical attempt，只能在上述安全窗口内重放exact request ID/envelope并做describe/history/archival read-back。发现matching run时，在assignment锁内原子补start receipt、绑定gen0 actual run并直接走下一条hard-closing分支；hash/cause冲突则quarantine。没有可信confirmed-not-forwarded或matching terminal history时永远不能退回“未启动”，短暂visibility absence不算证据。
3. Matching actual run已由start receipt绑定且generation 0=`bootstrap_pending`：hard request在同一`assignment -> generation -> hard request -> closing operation/header`事务冻结完整physical/item/captcha expected sets，创建`kind=hard_terminate`唯一owner，`bootstrap_pending -> closing`、effect epoch+1，并把request置`closure_owned`；绝不先转active。若首个bootstrap先赢assignment锁变active，hard request立即走普通`active -> closing`；若hard request先赢，迟到bootstrap只能read-back matching closing/start事实并保持effect-none。

Start RPC ACK未知、start receipt补写和termination request状态变化都追加事件并按operation/request key整组read-back；禁止删除gen0、清空RPC attempt、换request/workflow ID或把`waiting_start_resolution`人工改成completed。这里的“无run完成”是有item/materialization收口证据的正式终态，不是把outbox标failed。

Continue-As-New会合法改变Temporal run ID，而Reset/Retry也会产生新run ID，不能只比较workflow ID。建立受权链：

```text
collection_workflow_chain_generation
id, assignment_id, workflow_id
chain_generation
temporal_run_id NULLABLE
predecessor_generation_id NULLABLE
continued_from_temporal_run_id NULLABLE
transition_kind: initial_start / continue_as_new
input_contract_hash, continuation_manifest_hash
intent_version, intent_nonce_hash NULLABLE
workflow_definition_release_id, workflow_patch_set_hash
workflow_versioning_behavior, compatible_definition_release_set_hash
workflow_routing_revision_id, workflow_routing_member_set_hash
expected_workflow_task_deployment, expected_workflow_task_build_id
effect_authorization_epoch
closing_operation_id NULLABLE, closing_kind NULLABLE, closing_contract_hash NULLABLE
state: intent / bootstrap_pending / active / closing / continued / completed / rejected
created_by_activity_id, created_at, bound_at
UNIQUE(assignment_id, chain_generation)
UNIQUE(workflow_id, temporal_run_id) WHERE temporal_run_id IS NOT NULL
partial UNIQUE(assignment_id) WHERE state IN ('bootstrap_pending', 'active', 'closing')
UNIQUE(id, assignment_id)

collection_workflow_chain_closing_operation
id, operation_key UNIQUE, assignment_id, chain_generation_id, actual_temporal_run_id
kind: continue_as_new / waiter_continue_as_new / normal_return / cancel / hard_terminate / watcher_recovery
owner_activity_id NULLABLE, requested_by, expected_chain_effect_epoch, closing_effect_epoch
input_contract_hash, continuation_or_terminal_manifest_hash
state: prepared / draining / intent_ready / rpc_pending / temporal_terminal / completed / aborted / superseded / quarantined
next_chain_intent_id NULLABLE, waiter_transfer_id NULLABLE
supersedes_closing_operation_id NULLABLE, superseded_by_closing_operation_id NULLABLE
temporal_command_or_event_ref NULLABLE, claim_token, claim_generation, claim_expires_at
created_at, updated_at, completed_at
partial UNIQUE(chain_generation_id) WHERE state NOT IN ('completed','aborted','superseded')
UNIQUE(id, kind)
UNIQUE(id, assignment_id)

collection_workflow_chain_bootstrap_receipt
id, bootstrap_key UNIQUE, assignment_id
bootstrap_kind: initial_start / continue_as_new
predecessor_generation_id NULLABLE, successor_generation_id UNIQUE
closing_operation_id NULLABLE, actual_successor_temporal_run_id UNIQUE
intent_version, intent_nonce_hash, input_contract_hash, continuation_manifest_hash
workflow_routing_revision_id, workflow_routing_member_definition_release_id
workflow_routing_member_set_hash, workflow_task_routing_receipt_id
workflow_definition_release_id, workflow_patch_set_hash
actual_workflow_task_deployment, actual_workflow_task_build_id
temporal_started_event_ref, temporal_start_cause
bootstrap_activity_task_scheduled_event_id
producer_workflow_task_completed_event_id
old_effect_authorization_epoch, new_effect_authorization_epoch, applied_at
CHECK(initial_start has no predecessor/closing operation and start cause is normal start)
CHECK(continue_as_new has predecessor/closing operation and matching CAN cause)

collection_workflow_chain_closing_supersede_receipt
id, supersede_key UNIQUE, assignment_id, chain_generation_id
predecessor_closing_operation_id UNIQUE
successor_watcher_closing_operation_id UNIQUE
successor_kind: watcher_recovery
evidence_kind: immutable_non_can_terminal_no_successor
observed_actual_temporal_run_id
observed_terminal_event_ref, observed_terminal_cause
temporal_history_boundary_event_ref, temporal_history_hash, successor_inventory_hash
rejected_next_chain_intent_id NULLABLE, rejected_waiter_transfer_id NULLABLE
old_chain_effect_epoch, new_chain_effect_epoch
physical_target_set_hash, item_closure_set_hash
actor, reason, applied_at
CHECK(successor_kind = 'watcher_recovery')
CHECK(evidence_kind = 'immutable_non_can_terminal_no_successor')
composite FK(successor_watcher_closing_operation_id, successor_kind) -> closing_operation(id, kind)

collection_workflow_termination_ingress
id, ingress_key UNIQUE, assignment_id
request_kind: administrative_hard_terminate / user_cancel / deadline_expiry /
              policy_terminal / out_of_band_recovery
requested_closure_strength: cooperative / hard / watcher_hard
requested_customer_disposition, requested_terminal_policy_version
requested_contract_hash, actor, reason, evidence_hash
state: prepared / resolved
resolution_kind: root_alias / post_terminal NULLABLE
resolved_assignment_terminal_receipt_id NULLABLE
created_at, resolved_at NULLABLE
UNIQUE(id, assignment_id)
CHECK(
  (state=prepared AND resolution_kind IS NULL AND resolved_assignment_terminal_receipt_id IS NULL)
  OR (state=resolved AND resolution_kind=root_alias)
  OR (state=resolved AND resolution_kind=post_terminal
      AND resolved_assignment_terminal_receipt_id IS NOT NULL)
)

collection_workflow_hard_termination_request
id, root_key UNIQUE, assignment_id UNIQUE
effective_request_kind: administrative_hard_terminate / user_cancel / deadline_expiry /
                        policy_terminal / out_of_band_recovery
closure_strength: cooperative / hard / watcher_hard
effective_customer_disposition, terminal_reason_winner_intent_id
effective_terminal_policy_version, effective_terminal_contract_hash
target_kind: unstarted_assignment / chain_generation
initial_target_chain_generation_id NULLABLE, initial_actual_temporal_run_id NULLABLE
current_target_chain_generation_id NULLABLE, current_actual_temporal_run_id NULLABLE
observed_closing_operation_id NULLABLE
state: requested / no_run_closure_pending / waiting_start_resolution /
       closure_owned / waiting_can_resolution / rpc_pending / following_successor /
       terminal_recovery_pending / satisfied_by_existing_closure / completed / quarantined
request_effect_epoch, expected_assignment_contract_hash
expected_physical_target_count, expected_physical_target_set_hash
expected_item_settlement_count, expected_item_settlement_set_hash
temporal_rpc_request_hash NULLABLE, temporal_last_event_ref NULLABLE
owned_hard_closing_operation_id NULLABLE, satisfying_closing_operation_id NULLABLE
successor_chain_generation_id NULLABLE
requested_by, reason, created_at, completed_at, last_error
UNIQUE(id, assignment_id)
CHECK(unstarted_assignment has NULL generation/run and no Temporal RPC target)
CHECK(chain_generation has non-NULL generation; actual run may be NULL only while waiting_start_resolution)

collection_workflow_termination_request_intent
id, intent_key UNIQUE, hard_termination_request_id, assignment_id
request_kind: administrative_hard_terminate / user_cancel / deadline_expiry /
              policy_terminal / out_of_band_recovery
requested_closure_strength: cooperative / hard / watcher_hard
requested_customer_disposition, terminal_policy_version, terminal_contract_hash
actor, reason, evidence_hash, created_at
UNIQUE(hard_termination_request_id, id)
composite FK(hard_termination_request_id, assignment_id)
  -> hard_termination_request(id, assignment_id)

collection_workflow_termination_request_alias
id, termination_ingress_id UNIQUE, assignment_id
hard_termination_request_id, request_intent_id NULLABLE
requested_contract_hash, result_receipt_id NULLABLE, created_at
composite FK(termination_ingress_id, assignment_id)
composite FK(hard_termination_request_id, assignment_id)
composite FK(hard_termination_request_id, request_intent_id)
  -> termination_request_intent(hard_termination_request_id, id)
composite FK(result_receipt_id, assignment_id)
  -> assignment_terminal_receipt(id, assignment_id)

collection_workflow_assignment_terminal_receipt
id, assignment_id UNIQUE
terminalization_path: normal_without_root / termination_no_run / termination_chain
hard_termination_request_id NULLABLE
final_chain_generation_id NULLABLE, satisfying_closing_operation_id NULLABLE
no_run_terminal_receipt_id NULLABLE
temporal_terminal_event_ref NULLABLE, temporal_terminal_cause NULLABLE
expected_item_count, expected_item_set_hash
terminal_item_count, terminal_item_set_hash
materialization_count, materialization_set_hash
terminal_customer_disposition, terminal_contract_hash
assignment_termination_obligation_epoch, applied_at
UNIQUE(id, assignment_id)
CHECK(normal_without_root has NULL request/no-run, non-NULL final generation/closing/Temporal terminal)
CHECK(termination_no_run has non-NULL request/no-run, NULL final generation/closing/Temporal terminal)
CHECK(termination_chain has non-NULL request/final generation/closing/Temporal terminal and NULL no-run)
composite FK(hard_termination_request_id, assignment_id)
  -> hard_termination_request(id, assignment_id)
composite FK(final_chain_generation_id, assignment_id)
  -> chain_generation(id, assignment_id)
composite FK(satisfying_closing_operation_id, assignment_id)
  -> chain_closing_operation(id, assignment_id)
composite FK(no_run_terminal_receipt_id, assignment_id)
  -> no_run_terminal_receipt(id, assignment_id)

collection_workflow_post_terminal_intent_receipt
id, termination_ingress_id UNIQUE, intent_key UNIQUE
assignment_id, assignment_terminal_receipt_id
request_kind: administrative_hard_terminate / user_cancel / deadline_expiry /
              policy_terminal / out_of_band_recovery
requested_closure_strength: cooperative / hard / watcher_hard
requested_customer_disposition, requested_terminal_policy_version
requested_contract_hash, actor, reason, evidence_hash
observed_actual_temporal_run_id NULLABLE, observed_temporal_event_ref NULLABLE
existing_terminal_customer_disposition, existing_terminal_contract_hash
created_at
UNIQUE(assignment_id, intent_key)
composite FK(termination_ingress_id, assignment_id)
composite FK(assignment_terminal_receipt_id, assignment_id)

ALTER collection_workflow_termination_ingress ADD DEFERRABLE composite FK
  (resolved_assignment_terminal_receipt_id, assignment_id)
  -> assignment_terminal_receipt(id, assignment_id)

collection_workflow_termination_escalation_receipt
id, hard_termination_request_id, assignment_id, source_intent_id UNIQUE
old_request_effect_epoch, new_request_effect_epoch
old_closure_strength, new_closure_strength
old_terminal_reason_winner_intent_id, new_terminal_reason_winner_intent_id
old_intent_count, old_intent_set_hash, new_intent_count, new_intent_set_hash
old_physical_target_count, old_physical_target_set_hash
new_physical_target_count, new_physical_target_set_hash
old_item_target_count, old_item_target_set_hash
new_item_target_count, new_item_target_set_hash
join_policy_version, join_contract_hash, applied_at
UNIQUE(hard_termination_request_id, new_request_effect_epoch)
composite FK(hard_termination_request_id, assignment_id)
  -> hard_termination_request(id, assignment_id)
composite FK(hard_termination_request_id, source_intent_id)
  -> termination_request_intent(hard_termination_request_id, id)

collection_workflow_termination_work_claim
id, hard_termination_request_id, work_kind
claim_generation, claim_token, claim_expires_at
expected_request_state, expected_request_effect_epoch
state: active / expired / completed / superseded
claimed_by, claimed_at, completed_at NULLABLE
UNIQUE(hard_termination_request_id, work_kind, claim_generation)
partial UNIQUE(hard_termination_request_id, work_kind) WHERE state='active'

collection_workflow_hard_termination_abort_transfer_receipt
id, hard_termination_request_id UNIQUE
predecessor_can_closing_operation_id UNIQUE, successor_hard_closing_operation_id UNIQUE
actual_temporal_run_id, workflow_activity_id, activity_attempt
next_chain_intent_id NULLABLE, waiter_transfer_id NULLABLE
intent_version, intent_nonce_hash, workflow_abort_contract_hash
old_chain_effect_epoch, new_chain_effect_epoch
physical_target_set_hash, item_settlement_set_hash
applied_at

collection_workflow_hard_termination_successor_bind_receipt
id, bind_key UNIQUE, hard_termination_request_id
predecessor_generation_id, successor_generation_id UNIQUE
actual_successor_temporal_run_id UNIQUE, successor_hard_closing_operation_id UNIQUE
bind_actor_kind: bootstrap_activity / termination_reconciler
continued_as_new_event_ref, temporal_started_event_ref
intent_version, intent_nonce_hash, input_contract_hash, continuation_manifest_hash
physical_target_set_hash, item_settlement_set_hash
applied_at

collection_workflow_hard_termination_rpc_attempt
id, hard_termination_request_id, rpc_attempt_ordinal
target_chain_generation_id, target_actual_temporal_run_id
rpc_request_hash, expected_request_effect_epoch
state: prepared / rpc_pending / non_can_terminal_observed / continued_as_new_observed / outcome_unknown / completed
temporal_event_ref NULLABLE, terminal_or_continue_cause NULLABLE
successor_actual_temporal_run_id NULLABLE
history_boundary_event_ref NULLABLE, history_hash NULLABLE
created_at, completed_at
UNIQUE(hard_termination_request_id, rpc_attempt_ordinal)
UNIQUE(hard_termination_request_id, target_actual_temporal_run_id)

collection_workflow_hard_termination_request_event
id, hard_termination_request_id, event_sequence
event_kind, old_state NULLABLE, new_state
old_target_chain_generation_id NULLABLE, new_target_chain_generation_id NULLABLE
old_actual_temporal_run_id NULLABLE, new_actual_temporal_run_id NULLABLE
source_rpc_attempt_id NULLABLE, source_closing_operation_id NULLABLE
contract_hash, actor, applied_at
UNIQUE(hard_termination_request_id, event_sequence)

collection_hard_terminate_closure
closing_operation_id PRIMARY KEY, closing_kind: hard_terminate / watcher_recovery
overall_state: prepared / in_progress / completed / quarantined
effect_state: pending / revoked
physical_state: pending / barrier_pending / physically_fenced / quarantined
settlement_state: pending / settling / effects_settled / poison
temporal_state: not_requested / rpc_pending / temporal_terminal / outcome_unknown
expected_physical_target_count, expected_physical_target_set_hash
expected_item_settlement_count, expected_item_settlement_set_hash
physical_isolation_receipt_set_hash NULLABLE
item_settlement_receipt_set_hash NULLABLE
temporal_rpc_request_hash NULLABLE, temporal_terminal_event_ref NULLABLE
quiescence_snapshot_hash NULLABLE, completed_at NULLABLE
CHECK(closing_kind IN ('hard_terminate','watcher_recovery'))
composite FK(closing_operation_id, closing_kind) -> closing_operation(id, kind)

collection_hard_terminate_physical_target
id, closing_operation_id, instance_key
expected_holder_session_id, expected_lease_id, expected_fencing_token, expected_browser_boot_id
expected_worker_runtime_scope_id, expected_worker_scope_epoch
isolation_kind: clean_release / gateway_barrier / execution_scope_exit / worker_scope_kill_restart
clean_release_receipt_id NULLABLE
gateway_barrier_receipt_id NULLABLE
execution_scope_exit_receipt_id NULLABLE
worker_scope_recovery_operation_id NULLABLE
worker_scope_recovery_member_id NULLABLE
worker_scope_physical_isolation_receipt_id NULLABLE
state: pending / isolated / ambiguous / quarantined
isolation_receipt_hash NULLABLE, isolated_at NULLABLE
UNIQUE(closing_operation_id, instance_key, expected_lease_id, expected_fencing_token)
CHECK(isolated state has exactly the receipt required by isolation_kind)

collection_hard_terminate_item_settlement
id, closing_operation_id, request_item_id, submission_operation_id
dispatch_permit_id NULLABLE, terminal_disposition, terminal_receipt_id
result_staging_id, governance_outbox_id
UNIQUE(closing_operation_id, request_item_id, submission_operation_id)
```

`collection_workflow_termination_ingress`是API key、deadline/policy内部命令和watcher确定性key的**唯一全局幂等命名空间**；root alias与post-terminal receipt都不能自己再发明一套key。API key使用全局唯一、不含密钥的UUID/规范字符串；系统命令使用带类型前缀的`system/<kind>/<assignment>/<policy-or-evidence-hash>`，避免不同来源碰撞。入口先取得§7.1 step 0的key advisory xact lock并查ingress：已有行必须逐项比较assignment、kind、strength、disposition、policy与contract hash，相同则按`resolution_kind`read-back唯一child/result，不同则fail-loud；没有行才继续锁assignment，并在**同一事务**插入prepared ingress、建立root alias或post-terminal receipt child、CAS为resolved。Deferred trigger要求每个resolved ingress按kind恰有一个同assignment child且另一类为0；prepared行不允许跨commit对外可见。Root仍处理中时ingress的terminal receipt projection可为NULL并返回202，root完成后只允许CAS NULL→matching assignment receipt；post-terminal分支从首次resolved起就必须指向既有receipt。这样同一个key绝不可能一会儿是live root命令、一会儿又是post-terminal审计，跨assignment重放也不能产生副作用。

所有termination lineage都必须由DDL复合FK锁死同assignment/root，而不是靠应用先查：intent→`(root,assignment)`，alias→同ingress/assignment与同root/assignment；alias若带`request_intent_id`还必须以`(root,intent)`指向该root的intent，NULL只允许“完全相同合同的纯retry/read-back”，不能触发join或升级。Escalation的source intent必须属于同root；ingress/alias的terminal result、assignment terminal receipt的root/final generation/closing/no-run也都必须复合指向同assignment。Path-specific deferred trigger再验证closing属于final generation、no-run属于该root/start/gen0，以及receipt冻结的customer disposition/contract等于root winner。任何A assignment intent/root/receipt挂到B assignment都在commit被数据库拒绝。

表名`hard_termination_request`为兼容既有设计保留，但它在assignment尚未active时也是**统一的assignment termination gate**：对仍未终态的assignment，用户取消、service/absolute deadline和政策终结都必须创建带准确`request_kind/customer disposition/terminal policy`的同类durable request，不能等待一个可能永远不会执行的Workflow cancel handler。它们与管理员hard terminate共享上面的pre-start/no-run、start ACK unknown resolution和`bootstrap_pending -> closing` CAS；一旦actual run已active，允许patched workflow以matching normal/cancel cooperative closure满足请求，但assignment gate始终存在，超时/worker消失则升级到同一hard/watcher closure，不另造一条请求。无API请求却观察到immutable out-of-band非CAN终态时，watcher在assignment仍未提交terminal receipt的前提下，也必须在同一assignment锁事务先以确定ingress key创建/恢复`request_kind=out_of_band_recovery`的header，再创建或接管watcher owner；不存在“没有termination request的watcher closure”。若assignment terminal receipt已经存在，它只把同一ingress解析成post-terminal observation receipt，不反向制造watcher closure。这样“用户取消”不会被记成管理员强杀，底层又不会在gen0边界留下未纳管run。

一个assignment在**尚未提交assignment terminal receipt**时至多建立一个termination root；一旦建立，该root永久保留，completed后也继续占`UNIQUE(assignment_id)`。它不是要求每个正常完成的assignment事后补一条root。Root已存在时，新的API幂等键只能追加intent/alias并read-back同一terminal result，绝不能新建第二root、重开generation或换customer事实。并发user-cancel/deadline/admin-hard/out-of-band watcher先在assignment→chain→assignment-terminal-receipt锁序下判定是否仍可创建/读取root，再以`intent_key`追加immutable intent；受控join函数按冻结policy确定customer terminal-reason winner，并把执行机制的`closure_strength`取单调最大值`cooperative < hard < watcher_hard`。管理员hard升级一个已存在的user cancel只加强物理/Temporal收口，默认仍保留customer disposition=`cancelled_by_user`；若政策允许原因优先级变化，必须由版本化join policy确定，caller不能覆盖。

每次升级都递增request effect epoch、追加escalation receipt，并把physical/item/captcha expected set做规范并集；count只能不减，成员不能删除或换identity。若cooperative closure已经覆盖并终结全部required sets可`satisfied_by_existing_closure`，否则升级者建立/追随hard owner补足差集。相同intent key不同合同fail-loud；不同key相同或更弱意图成为alias/审计，不回退strength/epoch；root completed后更强意图也只能返回“assignment already terminal”及原receipt，不能改写历史结果。Header current projection、intent/alias/escalation receipts和normalized member集合必须双向anti-join可重建。

正常终态先提交、此前从未存在root时，随后第一次到达的user cancel/deadline/admin hard/policy request或watcher observation属于**post-terminal intent**，不是新的执行命令。入口在持有assignment排他锁并读到父行terminal pointer与immutable assignment-terminal receipt后，把已经由统一ingress key锁定的命令解析成`collection_workflow_post_terminal_intent_receipt`，返回原terminal receipt；同ingress key不同requested contract fail-loud。该行只保留“终态后有人提出了什么”及可选Temporal观察证据，不创建termination root/closing owner/RPC attempt/work claim，不扩大physical/item/captcha集合，不递增effect/obligation epoch，不重开scope blocker，也不得改变customer disposition。Root已经存在且assignment terminal receipt已提交时仍把ingress解析为既有root alias/read-back分支，不另写一个冒充执行意图的post-terminal root。

这里不能依赖`SELECT ... FOR UPDATE`锁住一条尚不存在的terminal receipt/root；真正的predicate fence是所有相关函数**先锁同一assignment父行并以该行两个权威pointer做判断**。正常terminalizer固定执行`assignment -> actual/gen0 chain -> assignment terminal receipt -> termination root`：若父行receipt/root pointer均为空，才可在同一commit写`normal_without_root` receipt并设置terminal pointer/version；若root pointer先存在，则不得写rootless normal receipt，必须由matching cooperative/no-run/hard分支满足root后写对应receipt。Termination/watcher入口执行相同前缀：若terminal pointer先存在且root pointer为空，只解析统一ingress为post-terminal intent；若root pointer先存在，正常terminalizer必须看见并收敛它。因assignment行更新把absence检查与结果发布串行化，竞态只有“正常receipt先赢，late request纯审计”或“request root先赢，terminalizer按root终结”两种结果，不存在正常receipt与新root分别提交的裂脑；等待者遇RR/SERIALIZABLE旧snapshot只能整体serialization-retry，不能从缓存继续。

该request header是§7.1 step 3b的**授权gate**，不是step 20才锁的generic work operation。所有首次创建、重试、bootstrap、normal effect、terminal materializer和item settlement统一按`control -> assignment -> actual/gen0 chain -> assignment terminal receipt -> termination request header -> 后续资源`读取/锁定；normal effect无需锁不存在的receipt行来获得互斥，但必须持assignment父行条件锁并验证其terminal pointer为空、root pointer为空。首次prepare在assignment锁保护下先检查terminal pointer；只有仍为空时才插入唯一header并在同commit设置父行root pointer/version，之后才扫描并锁scope/browser/item，未提交header不会被并发路径漏看。Worker领取权拆到上面的`termination_work_claim`：claim-only事务只锁claim行，apply按完整业务前缀取得request header及资源后，最后才锁claim行做token/phase CAS。禁止把claim字段留在request header然后某些路径“operation-final”、另一些路径“gate-first”，也禁止resource/item→request反向锁。

Completed termination request必须满足按`target_kind`互斥的两套证明，不能一律要求closing owner：

- `unstarted_assignment`：恰有一个matching no-run terminal receipt；gen0=`rejected`，start operation=`completed/cancelled_before_send`，没有actual run/start receipt/live RPC或任何closing owner；physical target count=0；全量item neutral terminal manifest和materialization/suppression command与冻结集合双向anti-join为零；
- `chain_generation`：没有no-run receipt；恰有一个current satisfying hard/watcher或被严格证明覆盖全部required sets的existing cooperative closing，actual run/terminal event、physical/captcha/item settlement及四轴closure全部满足合同。

受控完成函数与deferred trigger执行上述XOR；任一request同时有no-run receipt和closing owner、两者都没有、unstarted却出现Temporal run/physical target，或chain target缺satisfying closure都拒绝。Assignment terminal receipt再以`termination_no_run/termination_chain`路径引用这一个winner，避免scope blocker把“尚未启动”和“已启动后终止”混为一谈。`normal_without_root`与任何root FK互斥；post-terminal intent只能复合FK到已经提交的同assignment receipt，不能作为终结证明。

Pre-start或bootstrap-before-execution终结时可能还没有Activity创建`collection_execution_request/items/submission operations`，因此不能要求一个不存在的父对象自行物化。实现专用、privileged、effect-none的`ensure_terminal_execution_request_from_assignment(termination_request_id)`：它按`control -> assignment -> gen0/current closing -> termination request -> normalized assignment-item members -> deterministic execution request/items/initial operations -> terminal-materialization operation final CAS`取锁，只从assignment冻结的规范item成员、segment/mode映射、terminalization/task-persistence policy生成确定ID与内容；header count/hash只用于验证，绝不能反推成员。它先建立`request.state=building`、全量ordinal item和每item generation-0 `not_started` submission operation，做assignment-item↔request-item↔operation逐项compare、count/hash双向anti-join及deferred constraint验证后才原子转`committed`；随后普通cleanup allowlist逐item写`never_granted/neutral` terminal manifest和persist/suppress command。该函数不得读取/选择账号、建立reservation、写quota effect、获取browser/fence、签permit或调用Temporal；任何已dispatch/accepted/unknown事实都会拒绝neutral路径。

它与正常`ensure_execution_request()`用assignment锁和同一request/item deterministic unique key竞争：正常Activity先赢则termination复用已存在完整集合，termination gate先赢则normal Activity不能再建effect，terminal materializer创建/恢复同一集合。每个building/commit/item-terminal killpoint与ACK丢失都按同request/operation/hash read-back；半套building永不被terminalizer计入completed，reconciler只能补足同一冻结集合或quarantine。No-run receipt及bootstrap-pending watcher/hard closure都必须引用该物化结果，不能仅凭assignment header hash伪造“items已处理”。

Hard-termination request还必须有规范化`collection_workflow_hard_termination_request_physical_target`与`..._item_settlement_target`成员表，字段与最终hard-closure target/item表使用同一identity snapshot、count/hash和ordered-set规则。Request创建时冻结集合；它后来在同代获得hard owner、被合法CAN successor继承，或由终态watcher接管时，最终closure必须逐项adopt该集合并做双向anti-join，不能重新扫描一个更小集合。Cleanup期间新产生的发送结果只能单调补充settlement证据，不能从expected集合删项。

Physical target的`isolation_kind`不能由caller随便选择：只有与hard request线性化竞态中先完成的normal release具有exact holder/token/boot、gateway close/barrier或可信execution-scope exit、context clean/reset和无raw bypass证据时才可用`clean_release`；只有已通过raw-port isolation验收且该holder所有连接都在gateway registry中时可用`gateway_barrier`；只有独立per-attempt scope的PID/starttime/cgroup/socket exit均可证明且旧scope不能重连raw端口时可用`execution_scope_exit`；共享线程池或任一raw路径不确定时必须用`worker_scope_kill_restart`并纳入完整blast-radius operation。该类型只有引用matching scope operation、该target对应的冻结member，以及§6.9 append-only scope physical-isolation receipt时才能转`isolated`；**只有operation ID、child状态或“kill已请求”都不是证据**。Barrier前已转发命令仍按unknown/tainted结算，物理隔离不等于confirmed-not-sent。

Supersede receipt的跨表一致性不能写成引用别表状态的普通`CHECK`，因为PostgreSQL `CHECK`不安全支持这种约束。实现时用上面的kind复合FK固定successor只能是watcher，再由deferred constraint trigger和唯一`SECURITY DEFINER` owner-transfer函数验证：old/new属于同assignment、generation和actual run；old在同commit变为`superseded`并双向链接new；new是唯一live owner且其kind/contract与chain FK一致；chain effect epoch恰从receipt.old递增到receipt.new；被记录的next intent/waiter transfer已在同commit拒绝或冻结；hard-closure及physical/item expected-set count/hash与receipt一致。撤销普通主体对closing、receipt、chain owner FK和closure header的直接DML。Hard-closure header则用同表`closing_kind` CHECK加`(closing_operation_id, closing_kind)`复合FK实现类型约束，不能依赖应用先查kind后插入留下TOCTOU窗口。

Audited supersede只有一种证据：actual run已经以immutable**非Continue-As-New** cause终态，terminal event/cause非NULL且history已经封口，deterministic successor inventory与DB intent/bootstrap双向anti-join为零；successor只能是`watcher_recovery`。当前history“尚无CAN command”永远不属于这种证据，因为Workflow Task可能已经拿到prepare结果、正在返回CAN command。不能让hard takeover用一个短暂absence snapshot冒充最终无successor。

CHECK/deferrable constraint trigger要求generation 0没有predecessor；generation N>0恰好引用同assignment的N-1，`continued_from_temporal_run_id`等于predecessor绑定的run ID；intent只能以expected state/version/nonce CAS绑定一次，任何时刻同assignment最多一个`bootstrap_pending/active/closing` generation。`active -> closing`或`bootstrap_pending -> closing`必须在**同一事务**创建deterministic closing operation、递增chain effect authorization epoch，并把chain的operation/kind/contract FK全部填齐；不存在“裸closing但不知道谁拥有”的窗口。Abort恢复active也只能再次递增epoch、绝不回退；gen0 bootstrap_pending则绝不能经abort绕到active。因此closing前的Activity receipt永久过期。另建append-only transition event记录每次prepare/abort/bind/reject及old/new state、closing operation、intent/effect epoch/hash、actual Temporal run ID和actor。

Retry首先按operation key read-back：看到matching closing operation就恢复其phase，不能再创建另一个。CAN、normal/cancel、hard terminate和watcher竞争时，只有持assignment锁完成合法`active -> closing`或gen0 `bootstrap_pending -> closing`的winner拥有后续drain/intent/RPC/completion权；loser不能冒领。Hard请求撞上既有cooperative owner时只创建上面的durable hard-termination request并冻结required sets，绝不凭history absence改owner；后续只走同workflow pre-command abort transfer、Temporal真实non-CAN终态后的watcher recovery，或真实CAN successor继承三条路径。命令/ACK不明时保持原owner closing和hard request pending/quarantined。

Watcher遇到既有owner分三类处理，不能把`normal/cancel/CAN` operation原地改kind，也不能给不满足CHECK的operation硬插hard-closure header：

- 既有kind已经是`hard_terminate/watcher_recovery`：只可按expected claim generation接管过期claim，继续同一四轴closure；
- 既有kind是`continue_as_new/waiter_continue_as_new/normal_return/cancel`，且Temporal history不可变地证明actual predecessor已以**非Continue-As-New** cause终态、没有matching successor/bootstrap，未来不可能再发CAN command：走唯一的audited supersede事务。该事务按§7.1 hard-request/watcher prepare锁序把旧operation置`superseded`、拒绝/冻结未发出的next intent和waiter transfer、创建kind=`watcher_recovery`的新operation与hard-closure/physical/item expected sets、双向链接old/new并写supersede receipt，同时把chain owner FK/contract切到新operation并再次递增effect epoch。旧operation保留全部kind/history，不能删除；partial unique在同一commit从旧owner移交给新owner；
- Temporal terminal cause是Continue-As-New、matching successor/intent可能存在，或command/ACK/successor inventory任一不明：禁止supersede。真实successor按bootstrap协议接管；在predecessor既定drain/physical release不完整时阻断successor normal effect并quarantine。不能用“当前还没看到successor”终结整条chain。

Supersede evidence必须包含actual run的terminal event/cause、完整history hash、deterministic successor inventory和DB intent/bootstrap anti-join；普通超时或owner失联不够。证据冲突保持原closing/quarantined并告警。只有同一已授权workflow在发CAN API前完成durable abort Activity，才能把旧intent置rejected；外部reconciler无论history当前是否缺command都不能作此声明。普通abort恢复active时，以`rejected -> intent`重置同一下一代行必须递增`intent_version`、生成新nonce/hash；旧CAN input因version/nonce不匹配永远不能bind。不得因唯一`(assignment,generation)`冲突另跳一个generation、删除rejected行或丢失旧attempt审计。

Initial start receipt只把generation 0绑定actual run并置`bootstrap_pending`，从assignment逐项复制该代冻结的Workflow definition release、patch-set、versioning behavior、**routing revision ID/member-set hash**和expected Workflow Task deployment/build；它不开放任何effect，且`bootstrap_pending`已占据assignment唯一live-generation约束。由首个Workflow Task确定性调度的`bootstrap_gen0` Activity只有no-effect ceiling：它验证`WorkflowExecutionStarted`、自身`ActivityTaskScheduled.event_id`及其producer `WorkflowTaskCompleted.event_id`的actual routing receipt属于冻结revision/member，再在`assignment -> generation -> routing revision/member -> live hard request`事务写initial bootstrap receipt并把`bootstrap_pending -> active`、effect epoch递增；有live hard request时直接进入matching hard-closing，绝无active窗口。Commit ACK丢失只read-back同receipt。Generation 0不能在start后追随assignment current revision或“当前最新版”漂移。

`bootstrap_pending`本身是live、effect-none、必须可终结的正式状态，不是暂存标志。若actual gen0在bootstrap receipt前已经以immutable非CAN cause终态且history封口，watcher在assignment锁下先创建/恢复canonical `out_of_band_recovery` termination-request header，再创建无predecessor-closing-owner的`watcher_recovery` operation/header和完整expected sets，原子`bootstrap_pending -> closing`、epoch+1；这不是supersede，因为此前没有closing owner，但watcher closure仍始终绑定统一termination request。若已有live termination request，则只能由该request按其kind建立/绑定hard/watcher owner，不能再建第二request/owner。若Temporal仍running而只是bootstrap worker死亡/不可路由，保持pending并由compatible worker重试bootstrap，或通过正常durable termination request终止；不能凭Activity超时或history暂时没有bootstrap receipt创建watcher owner。Start outcome仍unknown时先走确定性workflow-ID resolution，不能把未绑定intent冒充Temporal terminal。

每次Continue-As-New前，patched workflow必须先调用无副作用`prepare_collection_continue_as_new` Activity：第一事务按`control -> assignment -> chain generation`以expected actual run/activity和deterministic operation key创建`kind=continue_as_new` closing operation，并把current`active -> closing`/operation FK/epoch一起提交；这是切代线性化点。Prepare commit后Activity completion ACK丢失时，下一attempt看到matching closing operation/transition event只恢复同一phase并返回同一结果，不因chain已closing而报冲突。所有`ensure/grant/activate/begin_submission/captcha adopt/resume`在assignment后都以共享/条件锁读取actual chain row且只接受active，所以bootstrap pending或closing的旧Activity/timeout僵尸不能新建资源。

随后matching operation的Activity/reconciler排空或结算全部live grant/fence/handoff，普通CAN还必须无live waiter；history-budget parked CAN只允许§6.3.1那一条durable transferring waiter，确认cursor/result/transfer durable，再以closing operation CAS写唯一next intent和input/manifest hash。该CAS还必须冻结successor generation的`workflow_routing_revision_id/member_set_hash/workflow_definition_release_id/workflow_patch_set_hash/workflow_versioning_behavior/expected Workflow Task deployment+build`，并把这组字段计入next-intent contract hash；revision必须是assignment current approved revision，成员/evidence双向anti-join为零，目标release属于member，所有用于这次**新generation绑定**的compatibility edge在DB now仍未过期。默认unique-patch路径只选择已证明能replay predecessor及全部live/resettable历史的compatible release，不能因CAN边界自动跳到最新build；只有存在已批准`can_boundary_upgrade` compatibility receipt，且隔离实验已证明当前Server+SDK能把真实successor路由到目标release时，才允许显式升级。Predecessor workflow只能把DB返回的intent ID/hash、冻结definition/routing contract及可选waiter-transfer ID/hash原样带入CAN input，不能自行读取“current deployment”或改目标release。

新Temporal run的首个patched bootstrap Activity在任何副作用前通过Temporal describe/history验证真实continued-from关系、input hash和Temporal实际Workflow Task routing，并锁`assignment -> predecessor/successor generation -> frozen routing revision/member -> live hard-termination request`。它对revision header/member/evidence做count/hash双向anti-join，要求actual deployment/build唯一映射到successor冻结revision的member及definition release，patch-set/versioning behavior逐项匹配，再写指向该member的bootstrap/routing receipt；actual release不在member、revision/hash错位、mismatch或unverified只能把generation保持`intent/closing`并quarantine/告警，绝不能因为assignment current后来扩展而开放effect。没有hard request时，它才在**同一事务**把predecessor`closing -> continued`、next`intent -> active`、actual run ID及deterministic bootstrap receipt一起提交；有transfer时也原子接管waiter routing。Activity的DB poller gate只能验证Activity执行者，不能替代这条Workflow Task replay/routing证明。

若存在matching live hard-termination request，bootstrap绝不能先把successor开放为active：同一事务把predecessor置continued、bind successor actual run、创建successor的`kind=hard_terminate` closing operation/header并adopt request冻结的physical/item sets、把successor intent直接置`closing`、递增其effect epoch、写bootstrap receipt并把hard request置`closure_owned`。Successor从未获得normal窗口；waiter只可转cancelled/closure-owned，不能恢复派发。Hard request与bootstrap都先锁assignment，所以竞态只有两个合法顺序：bootstrap先提交active，则随后hard request按普通active→hard-closing建立owner；hard request先提交，则bootstrap直接bind为hard-closing。任何normal effect函数也必须在assignment锁后验证不存在live hard request，防止漏实现的bootstrap分支放行。

若DB commit后Activity completion前崩溃，下一bootstrap attempt先read-back：successor已active或已由matching request直接closing、predecessor已continued且receipt/history/input逐项matching时，只读返回相同成功结果，不再写transition或任何effect；缺receipt或任一hash/run/cause冲突才fail-loud。其后每个execution Activity的normal权限只接受与当前Activity info run ID/epoch完全匹配的active generation且无live hard request。Workflow Retry、Reset或旧Cron tick没有matching next intent/continued-from hash，即使workflow ID相同也fail-closed并触发审计；不得把它们伪装成CAN。

Closing/next intent不得因超时或“当前history尚未看到CAN command/run”自动reopen；prepare Activity完成到下一Workflow Task发CAN之间天然存在合法空窗。只有同一已授权workflow在发CAN命令**之前**显式调用`abort_collection_continue_as_new`，以expected closing operation/current run/intent version+nonce/hash CAS把intent rejected、operation aborted并恢复current active+新effect epoch，workflow代码随后不得再使用该intent。外部reconciler只能在先terminate/cancel predecessor并确认其已terminal、无successor后把intent rejected并走closure/显式replacement，通常不再复活旧run。ACK/状态不明保持closing及原operation owner并告警，不能靠absence推断未来不会CAN。

Live hard request存在时，同一patched workflow可在调用Temporal Continue-As-New API**之前**走专用`abort_continue_as_new_into_hard_termination()` Activity。它不是外部history推断：受控函数验证实际workflow run/activity、matching CAN operation、intent version/nonce、hard request和冻结worker release/patch contract，在同一事务把next intent/waiter transfer拒绝、旧CAN operation置`aborted`、创建同generation `hard_terminate` owner/header并adopt request expected sets、保持chain为closing且再次递增epoch，写唯一abort-transfer receipt。Activity ACK丢失只read-back该receipt；workflow收到确定性`hard_termination_owned`结果后必须走不调用CAN的patched分支。未验证该patch marker/build或caller不是actual workflow时禁用此优化，改走下面的Temporal真实排序路径。外部API、watcher和reconciler绝不能调用该函数或伪造workflow abort receipt。

正常return、cancel和terminate也必须封死最后一代，不能让Temporal已经终态而DB chain仍为active。Patched workflow在正常return/cancel handler/final closure之前调用`prepare_collection_workflow_terminal`：按`control -> assignment -> actual chain`以expected run/activity、terminal contract和deterministic operation key，在同一事务创建对应kind closing operation并把`active -> closing`；从该线性化点起禁止新effect，只允许有界cleanup。Prepare commit/Activity ACK丢失按matching operation read-back同一结果；看到另一kind/contract的closing不能冒领。排空grant/fence/handoff/waiter并durable补齐所有item/materialization/closure事实后才允许workflow结束。

Hard Terminate不能依赖workflow handler，但也不能覆盖一个已经赢得`active -> closing`的cooperative owner。所有管理/API/watcher入口先用`termination ingress key(step 0) -> control -> assignment -> actual/gen0 chain -> assignment terminal receipt(step 3a) -> termination request header(step 3b)`共享前缀判定：若terminal receipt已经存在，则按上面的existing-root alias或rootless post-terminal intent路径只读返回；只有receipt不存在时才创建/恢复durable termination request，并继续按`scopes/browsers/health/context/fences/items -> work claim final CAS`冻结完整physical/item expected-set manifest。Assignment+terminal/root gate是bootstrap/normal effect与终结的线性化前缀；任何路径都不得先锁resource/item再回头锁terminal receipt/request：

- assignment已terminal：root存在则追加intent/alias并read-back原root/assignment receipt；root不存在则追加post-terminal intent receipt。两者都不建立owner、RPC、work claim、scope blocker或资源集合；

- generation 0仍为`intent`且start RPC可证明未签发/confirmed-not-forwarded：走上面的no-run closure，不能创建Temporal RPC或伪造hard chain owner；
- generation 0仍为`intent`但start RPC已进入dispatching/forwarded/outcome-unknown：request置`waiting_start_resolution`并阻断normal，先按确定性workflow ID补齐started/terminal事实；找到matching run后直接绑定为hard-closing，不经过active；
- current generation为`bootstrap_pending`：同一事务创建`kind=hard_terminate` closing operation/header、`bootstrap_pending -> closing`、chain effect epoch递增并把request置`closure_owned`；
- current generation仍active：同一事务创建`kind=hard_terminate` closing operation/header、`active -> closing`、chain effect epoch递增并把request置`closure_owned`；
- current owner已经是hard/watcher：request绑定既有owner并read-back，不创建第二个；
- current owner是CAN/normal/cancel：request置`waiting_can_resolution`，保留原owner，已有closing epoch已经撤销normal effect；request本身再作为assignment级终止门，任何successor/bootstrap/normal函数都必须看见它。绝不能因为history当前没CAN而改kind、reject intent或写supersede receipt。

受控代理只有在hard request、冻结set和effect revoke均durable后才能对**准确actual run ID**发Temporal Terminate，并以同一request hash/read-back处理ACK丢失。它与in-flight Workflow Task的服务端排序只产生三类事实：

1. 同workflow在发CAN前完成上面的abort-to-hard transfer，request直接获得同代hard owner；
2. Temporal actual run以非CAN cause终态，且history封口/无successor证据齐全：watcher audited-supersede旧cooperative owner为`watcher_recovery`，adopt request expected sets并把request置`terminal_recovery_pending/closure_owned`；
3. Continue-As-New先赢：不得把successor当孤儿。Request置`following_successor`；matching bootstrap在同一事务把新generation直接bind为hard-closing并adopt request sets。若successor没有worker可运行bootstrap，privileged termination reconciler可凭predecessor immutable Continued-As-New event、successor started event、authorized intent version/nonce/input hash做完全相同的**closing-only bind**并写successor-bind receipt；它绝不能bind active或调用effect函数。两者按assignment/intent锁竞争一个winner。若bootstrap在hard request之前已提交active，request在assignment锁后立即走普通active→hard-closing。直到owner建立前successor没有normal权限。

每次Temporal RPC都必须先写唯一rpc-attempt行并冻结当时的target generation/actual run；request的current target变化与append-only transition event同commit。RPC在事务外只使用该行的准确run ID，ACK丢失按该run的history/describe恢复，不能把旧attempt原地改指successor。若观察到Continue-As-New，旧attempt以continued事实终结，bootstrap receipt或上述closing-only successor-bind receipt确认successor后才为新actual run追加下一ordinal attempt；若观察到non-CAN terminal则不得再创建successor attempt。这样“重试hard request”不会把一次不明RPC同时打到predecessor和猜测的successor，也不依赖业务worker在线才能追杀合法successor。

若existing normal/cancel closure已经用可验证terminal、上面定义的exact clean-release/physical隔离和完整settlement覆盖request全部required sets，可把request标`satisfied_by_existing_closure -> completed`；证据少一项就由watcher hard recovery补齐。RPC/describe/history/DB ACK不明时request保持waiting/following/quarantined，API持续202；不得通过重发到另一个run、猜无successor或删除request解卡。Effect revoke之后，每个冻结target仍必须取得matching exact clean-release receipt、gateway forwarder barrier ACK、可信per-attempt execution-scope exit，或完整worker-scope physical-isolation receipt，physical axis才能进入`physically_fenced`。仅“已经发送barrier命令”、holder自报ACK、Temporal Activity取消或DB gate均不是物理证据；raw/CDP旧socket仍可能写时必须保持closing/quarantined。

Temporal Cancel/Terminate RPC可以在DB effect revoke后提前发出以加速协作停止，也可以按策略等到physical fence后再发，因此physical、settlement、temporal必须是三个**独立单调轴**，不能塞进一个假线性phase。RPC成功、Workflow已terminated或ACK丢失都不能推进physical/settlement轴。Expected item set覆盖该run/chain冻结集合中的每个尚未闭合request item：dispatching/forwarded/accepted按真实边界保守写accepted/unknown、staging/governance/额度settlement receipt；有submit permit但可证明未发才released；waiting/never-granted tail按冻结cancel/terminalization policy写neutral terminal manifest与materialization/suppression command，不伪造quota effect。Closure还必须另冻结该chain所有captcha effect permit及continuation owner集合：issued按gate线性化撤销，dispatching逐条取得forwarded/confirmed-not-forwarded/unknown receipt或匹配物理隔离，不能因它不属于quota item就漏掉。双向anti-join证明physical target、item settlement/closure、captcha permit/owner集合零缺零多后，settlement axis才进入`effects_settled`。Operation只有同时满足`effect_state=revoked + physical_state=physically_fenced + settlement_state=effects_settled + temporal_state=temporal_terminal`才可在一个CAS中写quiescence snapshot、`overall_state=completed`并把chain `closing -> completed`。任一轴的ACK/commit未知只read-back该轴，不回退或代替另一轴；在此之前API持续返回`202 closing`，不得发布客户终态、把受影响browser写成DB `free`、完成child recovery或授权新holder。这里**允许且要求**privileged recovery在`quarantined/recovering`内执行旧holder kill、browser restart、新boot commit、context reset、gateway sync和supervisor physical seal；否则会形成“hard完成等free、free又等hard完成”的闭包环。上述物理动作不能开放normal credential/raw端口，也不能把fence标free/regrant；只有append-only physical-isolation receipt先证明旧writer已不可写，hard四轴完成后才允许logical release。RPC ACK丢失按同operation和Temporal describe/history read-back，绝不能再次直接terminate另一run。

若CAN/normal closing已赢，terminate请求严格走durable hard-request的abort/Temporal排序/follow-successor协议，不能覆盖chain owner。普通用户、scheduler、worker和运维脚本的`TerminateWorkflowExecution`权限必须撤销，只允许受控代理针对hard request当前精确actual run执行这条序列。Namespace管理员越权操作、Temporal系统故障或进程消失这类真正out-of-band终态由高频watcher兜底：chain为`bootstrap_pending/active`且actual run已有immutable non-CAN terminal时，分别按“pending无旧owner直接建watcher”或“active→watcher closing”原子收口；已有hard owner时只接管其过期claim；已有cooperative CAN/normal/cancel owner时，只有实际run已经non-CAN terminal且封口/no-successor证据齐全才走audited watcher supersede，证据不足就保持原owner closing/quarantined，绝不原地改kind或绕过hard-closure CHECK。Start仍是outcome-unknown时必须先resolve，不能把Temporal暂时查无结果当out-of-band terminal。新watcher operation才建立/接管同等physical/item manifest并quarantine仍有写能力的holder。窗口内任何可能已转发的submit保守unknown；这只是灾难恢复，不是正常入口。

普通return/cancel closure consumer在同一actual run已到匹配终态且其既定资源/settlement清单完整后，才CAS`closing -> completed`并保存terminal event ref/hash；hard terminate/watcher recovery额外强制满足上面的physical target与item settlement receipt集合，绝不能仅凭Temporal终态完成。Worker死亡来不及prepare时，若Temporal已非CAN终态则由watcher把`bootstrap_pending/active`先原子closing再排空/物理隔离；若Temporal仍运行则等待合法Activity retry或显式hard request，不凭worker失联猜终态。Temporal read-back、termination ACK、barrier/kill receipt或DB commit任一不明时保持`intent/bootstrap_pending/closing/quarantined`。任何completed/continued/rejected generation都不能恢复effect权限。这样已放弃的Activity或`to_thread`僵尸即使仍携原run ID，也既过不了DB active gate，也会在closure完成前被gateway或supervisor物理隔离。

Worker路由也必须有“实际领取者”证据，不能只在assignment/start receipt里复制expected值。新增受控登记：

```text
collection_worker_boot_receipt
id, worker_process_instance_uuid UNIQUE, worker_identity, worker_release_id
worker_runtime_scope_id, worker_runtime_scope_epoch
deployment_name, worker_build_id, binary_artifact_digest, config_contract_hash
task_queue_set_hash, supported_protocol_min, supported_protocol_max
node_identity, process_start_identity, attestation_key_version, attestation_receipt_hash
state: active / draining / revoked / stopped
registered_at, last_heartbeat_at, revoked_at, stopped_at

collection_activity_poller_receipt
id, protocol_assignment_id, worker_boot_receipt_id
workflow_id, workflow_run_id, activity_id, activity_attempt
actual_task_queue, actual_worker_identity, actual_deployment, actual_build_id
activity_task_token_hash, assignment_contract_hash
workflow_chain_generation
activity_task_scheduled_event_id
producer_workflow_task_completed_event_id
workflow_task_routing_receipt_id
routing_proof_kind: event_link / verified_lifetime_pin
workflow_routing_revision_id, workflow_routing_member_definition_release_id
routing_receipt_result_snapshot: matched
observed_control_effect_authorization_epoch
observed_chain_effect_authorization_epoch
observed_worker_scope_effect_authorization_epoch
capability_ceiling: bootstrap_gen0 / bootstrap_continue / bootstrap_readback_only / execution
gate_result: matched / rejected
created_at
UNIQUE(workflow_run_id, activity_id, activity_attempt)
composite FK(workflow_task_routing_receipt_id, workflow_run_id,
             workflow_routing_revision_id, workflow_routing_member_definition_release_id,
             routing_receipt_result_snapshot='matched')
CHECK(event_link implies scheduled event and producer WFT event equal the routing receipt)
CHECK(verified_lifetime_pin implies a non-expired, exact-run lifetime pin receipt)
```

可信部署registrar在worker开始poll生产queue前，用每个deployment/build独立的短期DB身份和artifact-release FK登记immutable artifact/config/queue/protocol attestation；boot字段必须与release逐项相等，并绑定§6.9稳定runtime scope、current unit InvocationID/worker boot和scope epoch。该scope只有在supervisor startup reconciliation ready且state=open时才允许poll normal v1 queue；普通worker不能自报或修改deployment/build/digest/scope，旧build DB role在enforce前撤销登记v1/写effect的权限。Worker heartbeat只证明该boot尚未被控制面撤销，不替代grant/fence heartbeat。Activity interceptor从**本进程boot receipt和Temporal实际Activity info/history**写/恢复poller receipt，逐项比较assignment期望release/queue/deployment/build/digest/config及supported protocol，并冻结chain generation、当前control/chain/worker-scope effect authorization epoch；对任何effect Activity还必须把它的`ActivityTaskScheduled`事件关联到实际producer `WorkflowTaskCompleted`，引用matching generation routing revision/member receipt。若经隔离实验确认Temporal Server+SDK提供真实run-lifetime pin，也可引用覆盖整个run的lifetime-pin receipt；仅payload、Activity poller build、DB自报deployment或事后visibility不够。无法可靠关联event且没有verified lifetime pin时，v1只能使用实测build-specific Workflow Task queue/pin并fail-closed，不能选择AUTO_UPGRADE后放宽。随后按调用的受控DB函数声明以下不可互换的capability：

- `bootstrap_gen0` ceiling只接受start receipt已绑定actual run且generation=`bootstrap_pending`、matching input/routing revision的首个无副作用Activity；它验证自身scheduled event的producer Workflow Task routing，写initial bootstrap receipt并按hard-request竞态原子进入active或hard-closing。若matching hard request/watcher已把gen0转closing，迟到attempt只能登记`bootstrap_readback_only`并读取start/closing transition，不能补一张active bootstrap receipt。它禁止request/grant/fence/attach/submit，不能与execution互换；
- `bootstrap_continue` ceiling的**写路径**只接受尚为intent、matching intent version/nonce/hash/closing operation、且Temporal history/describe证明真实continued-from predecessor的successor actual run；它按固定锁序读取assignment级live hard request。无request时唯一允许bind predecessor closing→continued、intent→active和bootstrap receipt；有request时唯一允许bind predecessor continued、intent→hard-closing、创建matching hard owner/header/adopt sets、更新request和bootstrap receipt。两种都禁止request/grant/fence/attach/submit，Bootstrap Activity绑定后立即返回；
- Interceptor遇到successor已经active或已由hard request直接closing时不能一律拒绝：只有actual run/input/intent/predecessor closing operation、hard-request snapshot和deterministic bootstrap receipt逐项匹配，新的Activity attempt才可登记`bootstrap_readback_only` ceiling。该DB role/function只能读取并返回同一receipt/result，禁止再次transition或调用任何request/grant/fence/effect函数；状态已变但receipt缺失/冲突仍fail-loud。这样bind事务commit后、Activity completion ACK前崩溃可在interceptor gate层恢复，而不是永远卡住；
- `execution` ceiling在每次受控DB函数调用时结合**当前**chain/control/worker runtime scope、assignment live hard request、receipt冻结epoch和producer-WFT routing proof派生`normal`或`cleanup_only`，不是在Activity开始时永久获得effect。Normal要求actual run仍为active、没有live hard request、control open、实际worker runtime scope仍open且boot/InvocationID匹配，poller receipt的scheduled event→producer WFT→routing revision/member链或verified lifetime pin完整，且receipt generation与control/chain/worker-scope三级effect epoch逐项等于当前值、尚无downgrade receipt；错WFT build即使Activity worker本身正确也不能获得normal。同一Activity attempt在active→closing、hard request、pause、worker-scope drain/emergency、routing revoke或任一epoch mismatch后自动单调降级为cleanup_only，仍可settle/terminalize、为已dispatching/accepted题做有界capture/staging、cleanup heartbeat、detach/quarantine、materialize和closure，但不能新建普通request/grant/submit permit、adopt新owner或触发新send。Abort恢复active、pause后resume、worker scope重新开放或reconfigure都只递增epoch，旧receipt永远不能重获normal；必须由新Activity attempt登记新epoch。不存在cleanup→normal的caller CAS；
- 后台reconciler不伪造Activity receipt，使用独立expected closure/recovery operation claim，只能调用同一cleanup allowlist。

调用方不能仅把参数改成cleanup绕过限制：数据库角色分离，cleanup函数根据closing/epoch变更前已存在的operation/reservation/permit事实和expected version校验可操作对象。必须建立append-only `collection_activity_capability_downgrade_receipt(poller_receipt_id UNIQUE, observed/current control epoch, observed/current chain epoch, observed/current worker-scope epoch, reason, first_cleanup_function, applied_at)`；首个cleanup调用插入/恢复它，normal函数同时要求该receipt不存在，保证权限永久单调。Matched receipt缺失、过期、被撤销或ACK未知时，在任何相应写或外部I/O前fail-closed/read-back。若当前Temporal SDK不能可信暴露某项actual identity，必须通过受控worker启动注入的不可伪造boot receipt/SDK history或visibility read-back补齐并先做验证实验，不能把payload/env自报当成实际poller证据。相同Activity attempt不同boot/release/hash/capability ceiling必须fail-loud；ACK丢失按唯一键read-back，不得另写一条“matched”。

Enforce 切换必须在单一事务以 `FOR UPDATE` 锁同一 control行，复核 v0非终态run/workflow=0、v0 workflow-start outbox的 pending/claimed=0，再提高最低协议版本并写审计。这样并发v0 producer要么先提交并被enforce复核发现、使切换回滚，要么等切换后被DB拒绝。仅在应用层“先查0再改开关”存在TOCTOU，禁止采用。

既有 run 必须在 enforce 前全部拥有显式 v0 assignment；fresh v1 workflow/input/result 都携带 version=1 并逐层 compare。v1 worker缺 provenance字段是协议违规，不得因为字段为空而降级为 legacy。Completed v0 task允许 operation/capture FK为空并保留 legacy provenance。

### 6.1.2 `collection_region` 与 relay health observation

地域行保存的是“该地域relay出口当前是否可用于admission”的共享投影，不是账号、平台或browser的互斥锁。多个账号/平台的grant只短暂取得同一region行的`FOR SHARE`，因此可以并发；只有health apply、manual override或rebind这类短写事务需要`FOR UPDATE`。HTTP probe绝不能在持行锁期间执行。

扩展权威projection并建立append-only attempt/claim/receipt/event。这里必须把“自动健康事实”和“最终准入结论”拆成两个投影；probe函数永远不能直接写最终准入状态：

```text
collection_region
id, region_gb UNIQUE
configured_state: enabled / disabled
auto_state: ok / degraded / open / half_open / disabled
effective_state: ok / degraded / open / half_open / disabled / manual_blocked
effective_source: automatic / configured_disabled / force_blocked
health_policy_version, health_policy_hash
next_probe_generation, terminal_attempt_high_watermark, applied_projection_generation
auto_projection_version, health_epoch, admission_barrier_epoch, config_epoch, manual_override_epoch
auto_success_streak, auto_soft_failure_streak
auto_fresh_until NULLABLE, effective_fresh_until NULLABLE
breaker_opened_at NULLABLE, half_open_after NULLABLE
active_half_open_probe_generation NULLABLE
manual_override_kind: force_blocked / none
manual_override_until NULLABLE
version, last_projection_event_id NULLABLE, updated_at

collection_region_probe_attempt
id, attempt_key UNIQUE, region_id, probe_generation
producer_kind: scheduled / manual / startup_reconcile / half_open / diagnostic
observation_class: admission / diagnostic_only
expected_admission_barrier_epoch, observed_health_epoch_for_audit
expected_config_epoch, expected_manual_override_epoch
health_policy_version, health_policy_hash
probe_contract_hash, endpoint_set_hash
state: prepared / dispatched / completed / timed_out / cancelled
deadline_at, current_claim_generation, current_claim_token_hash NULLABLE
current_claim_expires_at NULLABLE, terminal_receipt_id NULLABLE
prepared_at, dispatched_at, completed_at
UNIQUE(region_id, probe_generation)

collection_region_probe_claim
id, probe_attempt_id, claim_generation
claim_token_hash, claimant_worker_boot_id, claim_contract_hash
state: active / expired / completed / superseded
claimed_at, expires_at, terminal_at NULLABLE
UNIQUE(probe_attempt_id, claim_generation)
UNIQUE(probe_attempt_id, claim_token_hash)

collection_region_probe_receipt
id, probe_attempt_id, region_id, probe_generation, claim_generation
claim_token_hash, observation_class
outcome: success / soft_failure / hard_failure / timeout / invalid_evidence
evidence_hash, observed_endpoint_set_hash
latency_ms NULLABLE, response_contract_hash NULLABLE
apply_result: applied / stale_observation_noop / stale_claim_noop /
              expired_claim_noop / manual_blocked_noop / override_epoch_noop /
              config_epoch_noop / admission_barrier_epoch_noop / policy_epoch_noop /
              diagnostic_noop / contract_rejected / half_open_inconclusive_closed
projection_action: none / observation_applied / half_open_fail_closed
old_auto_state, new_auto_state, old_effective_state, new_effective_state
old_auto_projection_version, new_auto_projection_version
old_health_epoch, new_health_epoch
old_admission_barrier_epoch, new_admission_barrier_epoch
is_attempt_terminal_winner
old_terminal_attempt_high_watermark, new_terminal_attempt_high_watermark
old_applied_projection_generation, new_applied_projection_generation
applied_at
UNIQUE(probe_attempt_id, claim_generation)
UNIQUE(probe_attempt_id) WHERE is_attempt_terminal_winner
UNIQUE(probe_attempt_id) WHERE projection_action <> 'none'
CHECK(projection_action = 'none' OR is_attempt_terminal_winner)
CHECK((apply_result = 'applied') = (projection_action = 'observation_applied'))
CHECK((apply_result = 'half_open_inconclusive_closed') =
      (projection_action = 'half_open_fail_closed'))
CHECK(apply_result NOT IN ('stale_claim_noop', 'expired_claim_noop')
      OR NOT is_attempt_terminal_winner)

collection_region_health_override
id, override_key UNIQUE, region_id, override_epoch
kind: force_blocked / authorize_half_open_probe / clear
expected_region_version, reason, evidence_hash, actor
effective_from, expires_at NULLABLE, applied_at
UNIQUE(region_id, override_epoch)

collection_region_health_event
id, event_key UNIQUE, region_id
source_kind: probe_receipt / manual_override / half_open_authorization /
             override_expiry / config_change / policy_change / migration_baseline
source_id, source_evidence_hash
old_auto_state, new_auto_state, old_effective_state, new_effective_state
old_effective_fresh_until, new_effective_fresh_until
old_health_policy_version, new_health_policy_version
old_auto_projection_version, new_auto_projection_version
old_health_epoch, new_health_epoch
old_admission_barrier_epoch, new_admission_barrier_epoch
old_applied_projection_generation, new_applied_projection_generation
contract_hash, applied_at
UNIQUE(id, region_id, new_health_epoch, new_applied_projection_generation,
       new_effective_fresh_until, new_health_policy_version)
CHECK(source_kind <> migration_baseline OR
      (source_id IS NOT NULL AND source_evidence_hash IS NOT NULL))
```

`collection_region_health_event`是immutable projection revision。每次non-none probe projection以及config/manual override/half-open authorization/expiry/policy/migration-baseline导致的投影变化，都必须在更新region current行的同一事务插event并把`last_projection_event_id`指向它；普通terminal no-op不创建伪projection revision。它只镜像auto/effective/freshness/policy/health epoch/applied projection等**授权投影**，故意不含审计用`terminal_attempt_high_watermark`：invalid/diagnostic/barrier no-op推进terminal high-water时，last projection event仍合法指向上一份可授权投影。Reservation只可复合FK到该immutable event的`region/new_health_epoch/new_applied_projection_generation/new_effective_fresh_until/new_health_policy_version`，**不得**把历史snapshot FK直接指向会持续UPDATE的`collection_region` current行，也不得CASCADE改写历史grant。Grant事务仍锁current region并验证它与last event的这些projection new值一致后才复制snapshot，不比较terminal high-water；begin-submission再按current行执行下文的相等/单调/fresh gate。这样后续probe或terminal no-op都不会被历史reservation FK阻断，也不会因审计高水位前进误停grant。

`effective_state/effective_source/effective_fresh_until`不是第三套可任意维护的数据。它们只能由受控函数按下面的纯规则在同一region行事务内生成，并用constraint trigger复算校验：

```text
configured_state = disabled
  => effective_state = disabled, effective_source = configured_disabled,
     effective_fresh_until = NULL

configured_state = enabled AND manual_override_kind = force_blocked
  => effective_state = manual_blocked, effective_source = force_blocked,
     effective_fresh_until = NULL

otherwise
  => effective_state = auto_state, effective_source = automatic,
     effective_fresh_until = auto_fresh_until only when auto_state = ok,
     otherwise NULL
```

因此自动probe无论拿到多新的generation或多健康的结果，都没有一条SQL路径可以越过活动的`force_blocked`把`effective_state`写成`ok`。普通probe、scheduler、worker和operator角色都撤销表级UPDATE，只能执行各自最小权限函数；只有override函数能改变manual projection，且系统不存在“手工force ok”。

探测协议固定为prepare、claim、HTTP、apply四段：

1. `prepare_region_probe()`短事务先锁region，以`next_probe_generation + 1`分配单调generation，冻结**admission-barrier**/config/manual-override epoch、policy version/hash和endpoint-set hash；当时health epoch只作审计，不作为completion no-op条件。插入deterministic attempt后提交；相同attempt key重试只read-back，hash不同fail-loud。Configured disabled或`force_blocked`期间普通scheduled/startup/half-open admission probe不允许prepare；privileged diagnostic可以prepare，但`observation_class=diagnostic_only`，永远不能影响准入；
2. poller以`attempt expected state/version`短事务claim或在旧claim过期后reclaim：锁`region -> attempt -> current claim`，递增`current_claim_generation`，生成高熵token并只存hash，冻结claimant worker boot和contract后提交。只有current claim能在事务外执行HTTP/relay探测。Token不得进入普通日志、Temporal payload或指标label；
3. worker在事务外探测。结果提交必须携带`attempt_id + claim_generation + raw claim token + evidence/contract hash`。apply在任何投影写入前验证token hash、claim contract、worker boot、attempt仍可完成、`claim_generation=current_claim_generation`且`DB now < current_claim_expires_at`。旧claim在reclaim CAS之后才返回时写`stale_claim_noop`；尚未reclaim但已经过期时写`expired_claim_noop`。这两种receipt按`(attempt, claim generation)`幂等保存，但`is_attempt_terminal_winner=false`：**不得**占用attempt terminal receipt、关闭attempt或改变terminal-attempt/applied-projection generation、streak、freshness、epoch；
4. 当前且未过期claim的completion事务按`region -> attempt -> claim -> receipt/event`锁序执行，并把“attempt是否结束”与“health projection是否应用”正交处理。它先比较冻结的admission-barrier/config/manual-override epoch、policy version/hash与current；任一管理barrier漂移分别写`admission_barrier_epoch_noop/config_epoch_noop/override_epoch_noop/policy_epoch_noop`。**自动probe导致的health epoch变化不是barrier**：更高generation必须在current auto state/streak上应用，不能因为较低generation刚改变health epoch就把更新鲜的hard failure no-op。它无论最终是`applied`，还是合法的上述barrier no-op、`stale_observation_noop/manual_blocked_noop/diagnostic_noop/contract_rejected/half_open_inconclusive_closed`，都以`is_attempt_terminal_winner=true`唯一CAS`terminal_receipt_id`、关闭attempt并终结current claim；同attempt另一个current completion只能read-back。所有terminal winner（含diagnostic/invalid/barrier no-op）只把审计用`terminal_attempt_high_watermark=max(current, probe_generation)`；只有`projection_action=observation_applied/half_open_fail_closed`才允许改变auto/effective投影并推进`applied_projection_generation=max(current, probe_generation)`，普通no-op必须`projection_action=none`且**没有有效观测supersession权**。后到的admission observation只有在`probe_generation <= applied_projection_generation`时才是`stale_observation_noop`；较新invalid/contract/barrier/diagnostic no-op绝不能仅凭已经关单压掉较低generation的valid hard failure。Receipt commit ACK丢失按`(attempt, claim generation)`read-back；同键不同outcome/evidence hash fail-loud。

Region行始终满足`0 <= applied_projection_generation <= terminal_attempt_high_watermark <= next_probe_generation`。每个terminal winner在同一region行锁事务冻结old/new terminal high-water并单调推进；每个非none projection action同时推进applied-projection high-water，`contract_rejected/invalid_evidence/diagnostic/barrier no-op`只推进terminal high-water而不推进applied。Probe terminal receipt逐项镜像terminal high-water old/new，non-none projection receipt与immutable health event镜像applied/health授权投影；health event不复制terminal high-water。Reconciler分别从terminal receipts和projection events复算两域，不能用一个孤立数字代替region CAS；业务stale与grant只能读取applied projection，禁止读取terminal high-water。

Attempt总deadline到达时，reconciler先以expected current claim generation做CAS，使旧claim永久失效，再取得新的专用timeout claim generation并写terminal-winner timeout receipt；不能删除attempt或让一个已经过期的HTTP worker回写。若普通worker的有效completion先线性化，timeout CAS为0；若timeout先赢，普通completion只能`stale_claim_noop`。Diagnostic、override竞态、contract rejected及stale observation都因已有terminal winner而不会被timeout反复reclaim。允许probe generation有洞，后续更高generation不等待缺失序号；只有更高generation已经产生非none projection action时，更低admission generation结果才`stale_observation_noop`。更高generation仅关单/no-op时，较低valid结果仍须在当前投影上apply或因其自己的management barrier明确no-op。

#### 精确breaker/hysteresis状态机

Health policy必须是immutable、verified且在启用前给出`soft_failures_to_degraded >= 2`、`soft_failures_to_open > soft_failures_to_degraded`、`successes_to_recover >= 2`、fresh TTL、open cooldown、endpoint集合和`hard_failure`分类；缺字段或未verified时region保持disabled。`hard_failure`只允许身份/出口地域不符、认证污染、明确协议拒绝等已枚举安全事件，不能把普通网络抖动任意升级。状态转换只使用被唯一apply的admission observation；任意success先把soft-failure streak清0，任意soft/hard failure或timeout先把success streak清0，不能沿用旧状态的“连续成功”。精确定义如下：

| 当前`auto_state` | Applied observation                                            | automatic projection变化                                                                                                                                                                                                | collection admission           |
| ---------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `disabled`       | 任意probe                                                      | 不变化；只能verified policy/config受控切到`degraded`并把两个streak=0                                                                                                                                                    | 0                              |
| `ok`             | `success`                                                      | failure streak=0；success streak+1并封顶；`auto_fresh_until=max(old, DB now+TTL)`；不增health epoch                                                                                                                     | 仅fresh window内允许           |
| `ok`             | 第1至`soft_failures_to_degraded-1`个连续`soft_failure/timeout` | success streak=0、failure streak+1，保持`ok`，**不延长**最后success freshness                                                                                                                                           | 仅原fresh window尚未过期时允许 |
| `ok`             | 达到`soft_failures_to_degraded`                                | success streak=0；转`degraded`、freshness=NULL、health epoch+1                                                                                                                                                          | 0                              |
| `degraded`       | `soft_failure/timeout`且累计未到open阈值                       | success streak=0、failure streak+1，保持`degraded`                                                                                                                                                                      | 0                              |
| `degraded`       | 累计达到`soft_failures_to_open`                                | success streak=0；转`open`，记录opened/cooldown、health epoch+1                                                                                                                                                         | 0                              |
| `degraded`       | success第1至`successes_to_recover-1`次                         | failure streak=0、success streak+1，仍`degraded`且freshness=NULL                                                                                                                                                        | 0                              |
| `degraded`       | success达到`successes_to_recover`                              | 转`ok`、设置freshness、health epoch+1                                                                                                                                                                                   | fresh后允许                    |
| `open`           | cooldown未到的普通结果                                         | 不允许生成普通admission attempt；迟到结果stale/no-op                                                                                                                                                                    | 0                              |
| `open`           | cooldown consumer或privileged authorization                    | 两个streak=0；CAS转`half_open`并在同一事务分配唯一`active_half_open_probe_generation`；health epoch+1                                                                                                                   | 0                              |
| `half_open`      | matching唯一probe failure/timeout                              | success streak=0、failure streak=1；转`open`、重置cooldown、清active generation、health epoch+1                                                                                                                         | 0                              |
| `half_open`      | matching唯一probe success                                      | failure streak=0；转`degraded`、success streak=1、清active generation、health epoch+1；仍需后续独立success达到恢复阈值                                                                                                  | 0                              |
| 任意非disabled   | `hard_failure`                                                 | success streak=0、failure streak至少置open阈值；立即转`open`、freshness=NULL、记录cooldown、health epoch+1                                                                                                              | 0                              |
| 非half-open      | `invalid_evidence`                                             | terminal winner只关闭attempt并推进审计用`terminal_attempt_high_watermark`，不推进`applied_projection_generation`，不改streak/state/freshness；因此它不能压掉随后到达的较低generation合法观测，已有freshness只会自然到期 | 由原状态与TTL决定              |
| `half_open`      | `invalid_evidence`或completion contract无法验证                | receipt=`half_open_inconclusive_closed`、projection action=`half_open_fail_closed`；success streak=0、转`open`、重置cooldown、清active generation、health epoch+1                                                       | 0                              |

上表故意规定：单次普通soft failure不会立即全地域停采，但也不会续命旧健康事实；第二次（或policy规定的阈值次）才降级。任何hard failure、TTL过期、degraded/open/half-open/disabled/manual_blocked都立即fail-closed。Policy切换一律递增health/manual相关版本、把两个streak=0、清freshness并回`degraded`重新验证，不能沿用旧阈值下的streak。

Configured enable/disable也有独立单调`config_epoch`，只能由privileged受控函数改变，并且**绝不清除或覆盖manual override**。Disable事务递增config、admission-barrier与health epoch，把configured/auto基线置disabled、两个streak=0、清auto freshness和active half-open generation；effective始终由纯函数重算为configured-disabled，manual force-block ledger仍原样保留。Enable事务同样递增这三个epoch，只把configured置enabled、auto基线置degraded、两个streak=0且无auto freshness，再由纯函数派生effective：仍有force-block时必须是`manual_blocked`，只有manual kind=none时才是`degraded`，两者都需新generation恢复。Policy切换使用相同barrier语义、保留manual override并递增admission-barrier、health epoch和policy version。只有matching clear/expiry operation能改变manual kind；`force-block -> disable -> enable`或`disabled期间force-block -> enable`均不得出现瞬时ok/degraded越过manual block。任何在config/policy切换前prepare或HTTP已返回的attempt都以`config_epoch_noop/policy_epoch_noop`terminal winner关单，auto/effective delta=0；不能让旧healthy跨`disable -> enable`恢复地域。

Manual override使用独立单调epoch并拥有最高优先级：

- `force_blocked`在region行锁内递增override、admission-barrier与health epoch，设置manual block、清effective freshness并写event；即使原本已不可授权也仍增health epoch，以永久失效所有在途grant snapshot。它冻结但不伪改auto state/streak，clear/expiry再显式把两个streak归0；
- force block生效前已dispatch的attempt，或极端竞态中已取HTTP结果但尚未apply的attempt，只能写`override_epoch_noop/manual_blocked_noop`，不更新**auto或effective** state/streak/freshness。Block期间diagnostic结果同样只审计；
- `clear`和force-block expiry都递增override/admission-barrier/health epoch、把manual kind置none，并把automatic projection重置为`degraded`、两个streak=0、freshness=NULL、active half-open generation=NULL。历史健康事实留在event/receipt中但不重新授权；必须由clear后新分配、冻结新override epoch的generation取得足够连续success才能回ok；
- `authorize_half_open_probe`不是force-ok，也不是持久manual状态。无论由cooldown自动consumer还是privileged operator触发，都必须先创建/恢复同一类durable `collection_region_health_override(kind=authorize_half_open_probe)` operation，不能让自动路径裸UPDATE region。它只在无force block、configured enabled且auto open时，以一个事务完成override与admission-barrier/health epoch递增、`open -> half_open`、唯一probe generation/attempt分配、`source_kind=half_open_authorization` immutable health event插入和`last_projection_event_id`切换；operation source/evidence区分system cooldown与operator，二者状态机完全相同。重复命令按override key read-back；另一个half-open attempt CAS为0。任一commit killpoint整笔回滚，不能留下half-open无active attempt或active attempt无projection event。

`force_blocked`还必须在同一事务清空`active_half_open_probe_generation`；相应在途attempt随后以manual-blocked terminal winner关单。任何terminal winner若引用matching active half-open generation，都必须让active字段在该事务结束时为NULL：success/failure按状态表转换，invalid/contract走`half_open_fail_closed`，manual/override路径要求override事务已清空；若出现理论上不应发生的stale-observation terminal winner仍匹配active generation，则fail-closed转open并P0告警，不能留下half-open悬挂。Diagnostic attempt从约束上不得被写入active half-open generation。

`auto_projection_version`在automatic state/streak/freshness的每次有效改变时递增；`health_epoch`只在线性化地撤销或重新授予collection admission、manual/policy barrier时递增，供grant失效；`admission_barrier_epoch`只由config/manual/policy等管理边界推进，供probe attempt失效，**自动health转换不得推进它**。Success-to-success只推进applied projection/auto projection并单调延长freshness，避免健康巡检无故打断在途grant。Grant冻结`region_id/health_epoch/applied_projection_generation/effective_fresh_until/policy_version`到reservation；admission要求derived `effective_state=ok AND DB now < effective_fresh_until`。`begin_submission`再次短暂共享锁region，要求current health epoch/policy仍等于grant snapshot、derived effective仍为ok、`current_applied_projection_generation >= frozen_region_applied_projection_generation`、current effective freshness不早于冻结值且DB now仍在current fresh window。Applied generation只作单调审计/防回退，**不得要求相等**：一次benign success-to-success refresh可让generation增加、freshness延长而不撤销旧grant；若current generation反而小于冻结值则是投影回退/P0，整次发送fail-closed。Health epoch变化、policy漂移、effective非ok或freshness缩短/过期使尚未发送item安全release/defer，已经dispatching/accepted只cleanup/settle而不重发。这样更高的**有效投影**总能在当前自动投影上应用，同时region健康翻转对grant仍有线性化效果，同地域不同账号、平台、browser也不会因一条长锁串行。

### 6.2 `collection_account_quota_bucket`

不要只给 account 加 `reserved_today`，也不要把 `quota_scope_key='mode:deep_think'` 当作自由字符串真源。先建立权威 policy/scope registry：

```text
collection_platform_quota_scope_requirement
platform, canonical_mode, platform_policy_version
scope_kind: account / mode
required, scope_kind_rank
UNIQUE(platform, canonical_mode, platform_policy_version, scope_kind)

collection_account_quota_policy
id, quota_subject_id, platform_policy_version, account_policy_version
state: draft / verified / active / retired
effective_from, effective_to
expected_scope_count, expected_scope_set_hash
calendar_cutover_contract_hash NULLABLE
verified_by, verified_at, evidence
UNIQUE(quota_subject_id, account_policy_version)

collection_quota_config_gate
quota_subject_id PRIMARY KEY
state: open / draining / blocked
gate_epoch, current_policy_id, current_policy_version
current_cutover_operation_id NULLABLE
blocked_reason NULLABLE, updated_at
UNIQUE(quota_subject_id, current_policy_id, current_policy_version, gate_epoch)

collection_account_quota_scope
id, quota_subject_id
scope_kind, canonical_mode NULLABLE
scope_kind_rank
state: active / retired
grant_state: open / blocked / unverified
scope_gate_epoch, grant_blocked_reason NULLABLE, last_scope_gate_receipt_id NULLABLE
created_at, retired_at NULLABLE
UNIQUE(id, quota_subject_id)

collection_account_quota_policy_scope_revision
id, quota_policy_id, quota_scope_id, quota_subject_id
scope_kind, canonical_mode NULLABLE, scope_kind_rank
calendar_policy_version, timezone, reset_rule
day_limit, week_limit, year_limit   # NULL 明确表示 unlimited，不是漏配置
calendar_contract_hash, state, effective_from, effective_to
UNIQUE(quota_policy_id, quota_scope_id)
UNIQUE(id, quota_policy_id, quota_scope_id, quota_subject_id)

collection_quota_policy_cutover_operation
id, operation_key UNIQUE, quota_subject_id
old_policy_id, new_policy_id, expected_quota_config_gate_epoch
scope_count, scope_set_hash, overlapping_bucket_count, overlapping_bucket_set_hash
kind: same_calendar / calendar_boundary / conservative_overlap_transfer
state: prepared / draining / transfer_ready / applied / rejected / quarantined
claim_token, claim_generation, claim_expires_at
evidence_hash, applied_at NULLABLE

collection_quota_policy_cutover_bucket_transfer
cutover_operation_id, old_bucket_id, new_bucket_id
old_scope_id, new_scope_id, period
reserved_transfer, debited_baseline, success_baseline, captured_baseline, unknown_baseline
source_interval, target_interval, overlap_contract_hash, transfer_effect_group_id
UNIQUE(cutover_operation_id, old_bucket_id, new_bucket_id)

collection_quota_scope_gate_recovery_operation
id, operation_key UNIQUE, quota_subject_id, quota_scope_id
kind: calendar_rollover / verified_reconciliation / manual_baseline_approval
expected_scope_gate_epoch, expected_blocked_reason
current_policy_id, policy_scope_revision_id
expected_bucket_count, expected_bucket_set_hash
state: prepared / claimed / applied / rejected / quarantined
claim_token, claim_generation, claim_expires_at
evidence_hash, applied_at NULLABLE

collection_quota_scope_gate_recovery_receipt
id, recovery_operation_id UNIQUE, quota_subject_id, quota_scope_id
old_scope_gate_epoch, new_scope_gate_epoch
old_grant_state, new_grant_state, old_blocked_reason
policy_id, policy_scope_revision_id
bucket_count, bucket_set_hash, exposure_set_hash
evidence_kind, evidence_hash, applied_at
```

这里的`quota_subject_id`必须引用§6.10全局唯一且verified的`collection_external_platform_identity.id`，不是调用方可选的account行。`collection_account_quota_scope`是跨policy revision永久稳定的额度身份：account scope对同subject恰一行，mode scope对同subject+canonical mode恰一行；普通policy换版、账号重建、换browser/region都不能新建另一套余额。使用PG15+ `UNIQUE NULLS NOT DISTINCT(quota_subject_id, scope_kind, canonical_mode)`，或两个partial unique：`UNIQUE(quota_subject_id) WHERE scope_kind='account'`与`UNIQUE(quota_subject_id, canonical_mode) WHERE scope_kind='mode'`，并以CHECK保证account mode为NULL、mode非NULL。Policy scope revision只描述该稳定scope在某版的limit/calendar合同；每个active policy对平台+本次mode requirement与稳定scope及revision做双向anti-join，零缺失、零多余且count/hash一致才允许grant。`collection_account_quota_policy`另加`UNIQUE(quota_subject_id) WHERE state='active'`。

Policy activate不是简单retire/insert，且verified policy revision本身immutable。Subject级`collection_quota_config_gate`只表达“整份配置是否处于可解析的一致版本”，不承载某个mode的容量超限；具体授权门在每个稳定scope的`grant_state/scope_gate_epoch`及当前bucket baseline/blocker上。Prepare先按`quota subject -> account -> quota config gate -> old/new policy -> stable scopes -> overlapping buckets -> cutover operation`建立durable cutover，以expected epoch CAS `open -> draining`并`gate_epoch+1`，立即阻断所有新grant；所有旧reservation因冻结epoch不再相等，只能在已dispatch事实范围内settle，未dispatch的必须release。排空所有live `reserved_unactivated/active` reservation和未消费permit后才能apply；已dispatch item仍按旧冻结policy向**原稳定bucket**结算。

若新旧calendar contract相同，apply复用当前稳定scope/bucket的全部reserved/debited/success/captured/unknown事实，只更新config gate current policy pointer和bucket limit snapshot；额度下调至当前exposure以下时只把**受影响稳定scope/current bucket**置blocked并推进scope gate epoch，account scope阻断全部mode，`mode:deep_think` scope只阻断deep_think，不能把subject config gate留在blocked从而误停normal。若timezone/reset/period边界改变，首选把生效时间排到所有旧bucket共同边界且当时无live exposure；确需重叠切换时必须使用`conservative_overlap_transfer`，以规范化old/new bucket集合、count/hash双向anti-join和唯一transfer effect把每个重叠区间的exposure保守带入新bucket，不能只复制success下界，也不能让旧bucket late adjustment失联。无法一一映射、scope集合整体不完整或transfer ACK不明时config gate保持`blocked/draining`，只能完成原cutover或持人工核准证据继续；不允许新bucket从0开始授权。最终apply原子retire旧policy、activate新policy、更新bucket合同与gate current pointer，完整配置`draining -> open`时**再次**`gate_epoch+1`并把operation applied；旧grant不能ABA。ACK丢失只read-back同gate/cutover/effect/scope集合。这样日内v1已debit=5切v2后仍从同一exposure计算，而不是获得第二套额度。

Scope容量/基线恢复使用独立typed两阶段operation，不能直接清字符串。Prepare/claim冻结expected scope epoch/reason/current policy-scope revision和DB-now对应day/week/year bucket集合；apply按`quota subject -> account -> quota config gate -> current policy -> stable scope -> current buckets固定序 -> recovery receipt/effect -> operation final claim CAS`加锁，要求config gate open、无该scope的live reservation/permit、bucket baseline全部verified、`reserved+debited <= finite limit`且reason/version未漂移，随后写receipt并把该scope `blocked/unverified -> open`、`scope_gate_epoch+1`。Calendar reconciler即使零业务事件也在新day/week/year边界触发`calendar_rollover`；`policy_limit_below_exposure`可在新current bucket满足条件后自动恢复，人工adjustment必须有唯一ledger/evidence。Overlap transfer/unverified mapping不能由普通rollover越权清除，只能完成原cutover或`manual_baseline_approval`。ACK丢失按同operation/receipt read-back。每次grant只锁并冻结本mode required scope的gate集合，所以normal与deep_think的下调、block和恢复互不覆盖。

目标 bucket 表按 quota scope × period × bucket 存事实：

| 字段                                              | 语义                                                                                                                             |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `id/pub_id`                                       | 内外身份                                                                                                                         |
| `quota_subject_id`                                | 权威额度主体，引用全局external identity，参与composite FK且`ON DELETE RESTRICT`                                                  |
| `platform_account_id`                             | 可选的首次/最近binding审计快照，不参与额度唯一域或授权分片                                                                       |
| `quota_scope_id`                                  | 指向跨policy稳定scope，是bucket唯一域；策略换版不得更换它                                                                        |
| `active_policy_scope_revision_id/quota_policy_id` | 当前limit/calendar合同投影与审计；不参与创建另一套余额                                                                           |
| `period`                                          | `day / week / year`                                                                                                              |
| `quota_scope_key`                                 | 只读/generated display key，不参与授权判断                                                                                       |
| `bucket_key`                                      | 规范时间桶键                                                                                                                     |
| `starts_at/ends_at`                               | 以账号额度时区计算的边界                                                                                                         |
| `quota_limit_snapshot`                            | 创建/最近配置时的额度快照，NULL 表示不限                                                                                         |
| `reserved_units`                                  | 已授权但尚未进入 dispatching 的单位                                                                                              |
| `debited_units`                                   | 已进入可能发送边界的单位                                                                                                         |
| `success_units`                                   | 成功采集单位，属于 debited 子集                                                                                                  |
| `captured_units`                                  | 已接受且答案 payload 已 durable、可成为成功样本的单位；不计 neutral/wall/unknown manifest                                        |
| `unknown_units`                                   | 发送结果未知单位，属于 debited 子集                                                                                              |
| `baseline_state/source/verified_at`               | 当前桶起点是否可信及证据来源                                                                                                     |
| `grant_blocked_reason/blocked_at`                 | unverified 或管理员下调导致当前 exposure 超限时的显式门禁                                                                        |
| `ledger_version`                                  | 每次影响授权快照的limit、baseline/blocker、reserved或debited变化都在同一行锁事务严格`+1`；只增不减，供launch snapshot恢复与防ABA |
| `created_at/updated_at`                           | DB 时间                                                                                                                          |

数据库约束：

- `UNIQUE(quota_scope_id, period, bucket_key)`；同一calendar合同内bucket key规范唯一，calendar cutover按上述边界/transfer协议处理；
- `UNIQUE(id, quota_subject_id, quota_scope_id, period)`，供 item link 做 composite FK；
- period `CHECK`；
- 所有 count 非负；
- `success_units <= captured_units <= debited_units`；
- `success_units <= debited_units`；
- `unknown_units <= debited_units`。
- `captured_units + unknown_units <= debited_units`；
- `success_units + unknown_units <= debited_units`。
- `quota_limit_snapshot IS NULL OR quota_limit_snapshot >= 0`；
- `ledger_version >= 0`；所有会改变`quota_limit_snapshot/baseline_state/grant_blocked_reason/reserved_units/debited_units`或current-policy-scope投影的受控函数，都必须把它与effect/receipt在同一事务按expected old version推进一次；success/captured若不影响available仍写各自effect但不要求推进该authorization version；
- `starts_at < ends_at`，bucket 采用半开区间 `[starts_at, ends_at)`。

不要直接添加 `reserved + debited <= quota_limit` 的静态 CHECK，因为管理员下调 quota 或迁移前历史超额会让行无法维护。新授权必须使用锁内条件更新/复核，确保有限额度不会因新的 grant 继续增加超额。

当前 `used_today/week/year` 的既有语义是“成功结果已持久化数”，不要静默改成平台消耗数。迁移策略：

- 旧 `used_*` 只能作为历史成功下界，不能证明当前平台请求额度还剩多少；
- 若 quota 表示平台请求预算，当前有限 bucket 必须由可信平台 probe、人工核准或保守填满到 limit；无法核准时 `baseline_state=unverified` 且 grant=0，直到 bucket reset 或人工签字；
- 迁移可把有对应成功任务证据的旧 `used_*` 写为 success/captured 下界，并为满足守恒把 debit 至少设为同一下界；这只是 `debit >= legacy success` 的保守下界，不代表余额已核准；
- 已核准 bucket 才能按平台/人工证据把 debit baseline 调整到可信值并置 verified；
- 之后 `debited_units` 表示保守平台消耗，`success_units` 表示成功结果；
- 旧 `used_*` 暂时作为 success projection 兼容 UI/API；
- 新的“剩余额度”读取 bucket 的 `debited + reserved`，不再读取 success projection；
- 最终由 reconciliation 验证 projection，不能让两个事实源独立写入。

`success_units` 明确定义为 `CollectionTask` 成功持久化并由唯一 task-success receipt 确认，不是“adapter 进程内已经看到答案”。Adapter capture 由 item `captured`/staging 表达；task persist 同事务写 task-success command，consumer 再幂等更新 success projection，绝不影响 debit 安全。

Task-success command 必须携带 item 在 dispatch 时冻结的全部适用 scope × day/week/year bucket ID。即使 consumer 到午夜后才执行，也只能更新这些历史 bucket，不能用消费时的 `now()` 重新选择“当前桶”。兼容字段 `used_today/week/year` 只能由 projector 在 account 行锁内读取当下账号 scope 三个 bucket 后执行绝对值 `SET`；禁止“跨日延迟事件再 `+= 1`”，也禁止旧 lazy reset 和新 projector 同时写。Mode success projection 遵守同一规则。

Task-success consumer 在 account 锁内先确定两组 bucket 的并集：item 冻结的历史 bucket set，以及按 DB now 得到的当下 account/mode projection bucket。按统一 scope/period/ID顺序锁整个并集，再插入 effect group/receipt：历史 set `success +1`；兼容 `used_*` 根据当前 set 做绝对 SET。Receipt/effect/outbox 同一 commit，不能先 receipt 后再锁 bucket。

午夜后即使完全没有 success event，也不能让旧 `used_today` 停在昨天。权威 API/UI 一律按 DB now 直接派生 current bucket；另建 quota-calendar rollover/reconciler 预建新桶并在 account CAS 下绝对 SET兼容字段，同时保存 `used_day_bucket_key/used_week_bucket_key/used_year_bucket_key`。旧 lazy reset writer 必须关闭，但无事件 rollover 服务必须常驻并受监控。

Mode-specific quota不得继续依赖整份JSON读改写：数值额度来自verified mode quota scope bucket；平台账号级治理状态同样不能挂可撤销的account header。建立：

```text
collection_quota_subject_governance_state
quota_subject_id PRIMARY KEY
admission_state: enabled / muted / rate_limited / hard_blocked / conflicted
wall_kind NULLABLE, blocked_until NULLABLE
success_streak, failure_streak
gate_version, gate_epoch, gate_evidence_hash NULLABLE
projection_version, last_governance_gate_effect_id NULLABLE, updated_at

collection_quota_subject_mode_state
quota_subject_id, canonical_mode
state: enabled / muted / quota_blocked / wall_blocked / hard_blocked
blocked_until NULLABLE, reason, success_streak, failure_streak
gate_version, gate_epoch, gate_evidence_hash NULLABLE
projection_version, last_governance_gate_effect_id NULLABLE, updated_at
UNIQUE(quota_subject_id, canonical_mode)

collection_binding_session_state
binding_revision_id PRIMARY KEY
current_session_revision_id, current_session_version
state: verified / captcha_required / session_expired / credential_tainted / retired
gate_epoch, projection_version
verified_until NULLABLE, last_gate_effect_id NULLABLE
updated_at
UNIQUE(binding_revision_id, current_session_revision_id, current_session_version)

collection_binding_session_revision                  # append-only事实
id, binding_revision_id, session_version
predecessor_session_revision_id NULLABLE
state, gate_epoch, evidence_kind, evidence_hash
verified_at NULLABLE, verified_until NULLABLE, actor
source_governance_gate_effect_id NULLABLE, created_at
UNIQUE(binding_revision_id, session_version)
UNIQUE(id, binding_revision_id, session_version, state, gate_epoch, evidence_hash)
```

Admission从policy requirement推导subject全局scope和当前mode scope，并在`quota subject -> account -> binding session`锁序下同时检查subject全局/mode治理gate与current binding revision的session gate；调用方不能自行传scope字符串。每个进入`verified/draining`的binding revision必须恰有一条session header，其current pointer复合FK指向该binding自己的append-only session revision；缺行、指针错链、state非`verified`、evidence缺失或`verified_until`已过期时，新grant=0。Grant冻结session revision ID/version/gate epoch/state/evidence hash/verified-until，begin-submission再次比较current pointer与冻结snapshot；任何captcha/session-expired/credential-tainted flip都追加新revision、递增session version和gate epoch，使已授权但未发送item立即停止并只进入cleanup/专用assist。

Wall、平台rate limit、muted和外部账号hard error的terminal gate effect都冻结并更新`quota_subject_id`，revoke/recreate account、换region/browser或新binding revision不能清除这些gate/streak。只有平台证据明确证明是某一browser session/captcha credential局部事实的状态才推进binding session；即使换绑，也不得无证据把subject级wall误降为session级。`captcha_required/session_expired/credential_tainted -> verified`只能由专用login/captcha-assist verifier提交可验证的新会话证据，并追加新session revision+gate epoch；普通probe、scheduler、worker或账号rebind不能直接写verified。Captcha assist使用独立assist capability，可在matching `captcha_required` revision下操作，但不获得normal submit权限；恢复后原grant也不复活，必须按新session snapshot重新授权。Account上的legacy mode JSON/`muted`/error streak仅由subject projector绝对SET并带subject/version key，普通路径不读它授权、不允许独立写。

时间桶必须由一个集中 `QuotaCalendar` 计算，禁止各模块各自实现：

- 明确账号/平台额度时区，默认 Asia/Shanghai；
- day 使用配置的 reset 点；
- week/year 的边界规则固定并测试；
- 全部比较使用 DB `clock_timestamp()` 或事务内 DB time；
- 首版禁止reservation跨bucket、会话验证期限或policy/scope revision有效期：`expires_at/max_deadline_at` clamp到全部适用scope × day/week/year `ends_at`、active quota policy及每个required policy-scope revision的`effective_to`、current binding-session `verified_until`（非NULL时）及其他授权deadline的最早值，heartbeat/activation/permit不得越界；这些纯时间边界到点即使没有任何writer推进revision也立即失效；
- `begin_submission` 用 DB now 重算并验证全部关联 bucket key，任一变化就释放尚未发送 item、返回 `quota_bucket_rolled_over_pre_submit`，coordinator 在同一 request 下使用下一 grant generation 于当前 bucket 重新授权；绝不把边界后的发送记进旧 bucket。

为使 bucket 可重放、可对账，effect 必须同时表达“一次逻辑转换”和“该转换对多个 bucket 的逐行 delta”，不能只放一个含糊的 `effect_key`：

```text
collection_account_quota_effect_group
effect_group_key UNIQUE
source_kind: reservation_item / capture_attempt / baseline / manual_adjustment / reconciliation
reservation_item_id NULLABLE
capture_attempt_id NULLABLE
baseline_verification_id NULLABLE
manual_adjustment_id NULLABLE
reconciliation_id NULLABLE
transition_kind
expected_bucket_set_hash, expected_bucket_count
created_at, actor, evidence

collection_account_quota_effect
effect_group_id
bucket_id, quota_scope_id, period
delta_reserved, delta_debited, delta_captured, delta_success, delta_unknown
created_at
UNIQUE(effect_group_id, bucket_id)
```

Group 用 `source_kind` 与五个 source FK 的 XOR CHECK 保证恰有一个来源；只有 `source_kind=reservation_item` 才要求 item-link composite FK，`capture_attempt` 必须经其 operation 回指原 reservation item 的冻结 bucket set，baseline/manual/reconciliation 不得伪造 reservation item。Item transition group 的 expected count/hash 必须恰等于冻结 bucket set；受控 DB function/单条 CTE 在同一事务完成 group winner、全组 row insert、双向 anti-join、item/operation/capture receipt CAS 和全部 counter update，集合不完整则整体回滚。迁移/人工核准写独立 baseline/manual source，因此 bucket 的全部 counter 仍可重建。只有唯一 group winner 可以改 counter。Reconciler 从 effect 重算 bucket，而不是猜当前 item 状态。Unknown 后续证实 accepted 时写独立 adjustment group：`unknown -1`、debit 不变；证实未发送时 `unknown -1, debit -1`。原 terminal item不改写。

同一 submission operation 最多允许一次 `captured +1` promotion。用 operation 上的 nullable `capture_promotion_effect_group_id` 加唯一 FK/受控函数，或独立 `collection_capture_promotion_receipt(submission_operation_id UNIQUE, capture_attempt_id, effect_group_id UNIQUE, content_hash, applied_at)` 实现；normal capture 和 late capture 竞争时只有一个 winner。后续更高质量答案可以更新“当前选中结果”projection，但不得再次增加 captured 或 success。

`delta_captured` 只在合格答案 payload 已 verified 时增加；为 wall、unknown、aborted 保存 terminal manifest 不等于 quota `captured_units + 1`。

### 6.3 `collection_execution_request`

把稳定逻辑请求与短生命周期物理 lease 分开。Request 表至少包含：

```text
request_key_hash UNIQUE
request_contract_hash
protocol_assignment_id, protocol_version
workflow_id, workflow_run_id, activity_id, chunk_ordinal
state: waiting / active / completed / permanently_rejected
current_reservation_id
next_grant_generation
execution_state_version, effect_version
last_transient_reason
next_retry_domain: control_epoch / service_tick / absolute_db_time / resource_change
next_retry_service_tick_ms NULLABLE, next_retry_at NULLABLE, observed_control_epoch NULLABLE
deadline_policy: service_clock / absolute
service_deadline_tick_ms NULLABLE
absolute_business_deadline_at NULLABLE
terminalization_policy_version, terminalization_policy_hash
task_persistence_policy, task_persistence_policy_hash
cancel_requested_at NULLABLE, cancel_reason NULLABLE, cancel_actor NULLABLE
created_at, updated_at
```

规则：

- 永久 rejection 只允许非法输入、合同漂移、协议不支持等确定性错误；
- pause、fence busy、`SKIP LOCKED` 无候选、region 暂时不健康、当前 quota 为 0 都是 transient decision；可以追加 attempt event，但不得把同 request 永久 terminalize；
- 同 request 在 pause→resume、busy→free、bucket reset 后可以重新评估并成功；
- workflow 遇到 pause 必须 park/wait，不能把等待题持久化为 `account_quota_unreserved` 永久失败。
- deadline policy、已经计算好的service deadline tick、terminalization policy和cancel fact在request首次建立时从可信run/protocol assignment复制；全部判断使用DB时间/同事务读取的control服务时钟。默认`service_clock`只有control=open时逼近同一个run-level tick；管理性pause/drain/emergency不会消耗预算。只有业务明确要求不可延长的客户/法律截止时间才使用`absolute_business_deadline_at`，并以独立`absolute_deadline_elapsed_while_paused`原因诚实终结。
- Activity retry、pause/resume、continue-as-new和captcha continuation不得重算预算或移动deadline tick。Retry target必须带显式domain：pause等control epoch，service deadline比较service tick，bucket reset等外部时间才使用absolute DB time；不能用一个跨长pause的普通wall-clock timer提前terminalize。Cancel signal只写durable cancel intent；patched closure/finalize Activity或reconciler以expected request version CAS terminalize waiting items。Workflow被terminate、worker丢失或signal ACK丢失也不能丢掉这条关闭事实。
- 只有 DB 中的 deadline 已到、cancel 已生效或被冻结的 terminalization policy 明确作出最终决定，才能把 waiting item 转 `terminal_ungranted`。每个 request 只允许一次 terminalization winner，重复 consumer 恢复同一 manifest。
- `task_persistence_policy`与hash从assignment原样复制并进入request contract。Activity input里的legacy `persist_results`只能与之compare，不能覆盖；同run所有segment一致。
- request 是 retry 间稳定的协调身份，不是物理 lease。每次真正分配资源时在 request 行锁内递增 `grant_generation`，并以 partial unique 保证同 request 最多一个 live reservation；旧 generation 只能恢复事实，不能复活。
- 一个 Activity 输入 segment 的全部题在同一 request 下有稳定 request item。Partial grant 完成后，从数据库选择最小 waiting ordinal，以同 request 的下一 grant generation 授权连续前缀；不得新造 child request，也不得使用进程内“本 attempt 已完成多少题”作为游标。

为整个请求中的每个输入题建立独立 `collection_execution_request_item`，不要让“没有获得 quota grant”意味着“数据库里没有身份”：

```text
id, request_id, ordinal
tenant_pub_id, run_pub_id, business_key
platform, adapter, region_gb, mode, query_hash
operation_base_key_hash
current_submission_operation_id, next_submission_generation
state: waiting / active / terminal_ungranted / terminal
provenance_state: building / committed
current_reservation_item_id
terminal_manifest_staging_id NULLABLE
latest_verified_result_staging_id NULLABLE, result_projection_version
created_at, updated_at
UNIQUE(request_id, ordinal)
UNIQUE(request_id, business_key, mode)
UNIQUE(operation_base_key_hash)
```

Request item 是输入顺序和等长恢复真源；reservation item 才表示某个 grant generation 的实际 quota/send 授权。Transient deferred 只保持 waiting。达到明确 deadline/cancel 后，未获 grant 的 request item 才能 terminal_ungranted，并写 verified manifest。

Request item与初代submission operation也有双向首插循环。物理上`current_submission_operation_id`允许在`provenance_state=building`时NULL；`ensure_execution_request()`同一事务按ordinal插request item(building)→插`submission_generation=0` operation→CAS current operation、`next_submission_generation=1`并转committed。用DEFERRABLE复合FK/constraint trigger在COMMIT验证所有v1 committed item的current operation确实反向属于同item、contract一致，且`current.submission_generation = next_submission_generation - 1`；下游只读取committed。Confirmed-not-sent/人工批准新operation必须在同一request-item行锁事务插入准确下一代、CAS current和next generation，不能两边独立更新。任一失败整体回滚，不留下无operation的可见item。

`operation_base_key_hash` 由 `tenant + business run + canonical business_key + platform + adapter + region + mode` 规范编码后计算；它不含 workflow/activity/attempt。任何 canonicalization 版本必须进入 contract hash，避免升级后把同一题误当新题。账号/浏览器可以按政策重选，但 platform/adapter/region/mode/query contract 不可跨 grant 漂移。

### 6.3.1 Durable deferred waiter

Parked workflow没有browser holder，不能依赖只发给active lease的pause signal。建立：

```text
collection_execution_waiter
id, waiter_key UNIQUE
execution_request_id
origin_workflow_id, origin_workflow_run_id, origin_activity_id
protocol_assignment_id
resume_generation, resume_slice_hash, next_ordinal
routing_generation, current_workflow_id, current_workflow_run_id
reason
observed_control_epoch
not_before_domain: control_epoch / service_tick / absolute_db_time / resource_change
not_before_service_tick_ms NULLABLE, not_before_at NULLABLE
state: registered / signal_pending / ready / claimed / transferring / transferred / consumed / cancelled
wakeup_command_id NULLABLE, wakeup_epoch NULLABLE
claim_token, claim_expires_at
claimant_workflow_id, claimant_workflow_run_id, claimant_activity_id NULLABLE
claim_request_state_version NULLABLE, claim_effect_version NULLABLE
claim_next_grant_generation NULLABLE, claim_current_reservation_id NULLABLE
claim_fence_snapshot_kind: no_expected_browser / expected_browser NULLABLE
claim_expected_browser_id NULLABLE, claim_fencing_token NULLABLE, claim_browser_boot_id NULLABLE
claim_continuation_id NULLABLE, claim_continuation_version NULLABLE
consumed_reservation_id NULLABLE, consumed_grant_generation NULLABLE
closure_receipt_id NULLABLE
signal_receipt_hash NULLABLE
created_at, updated_at
UNIQUE(execution_request_id, resume_generation, routing_generation)
partial UNIQUE(execution_request_id) WHERE state IN ('registered','signal_pending','ready','claimed','transferring')

collection_execution_waiter_transfer
id, transfer_key UNIQUE, execution_request_id, predecessor_waiter_id
predecessor_assignment_id, predecessor_chain_generation, predecessor_workflow_run_id
next_chain_intent_id, next_chain_intent_version, next_chain_intent_nonce_hash
waiter_resume_generation, predecessor_routing_generation
resume_slice_hash, next_ordinal, reason, initial_readiness_snapshot, not_before_snapshot_hash
readiness_version, latest_readiness_state, latest_wakeup_epoch NULLABLE, latest_wakeup_command_id NULLABLE
request_state_version, effect_version, control_epoch, continuation_version NULLABLE
state: prepared / bound / aborted / quarantined
successor_waiter_id NULLABLE, successor_workflow_run_id NULLABLE
contract_hash, created_at, bound_at
UNIQUE(predecessor_waiter_id, predecessor_routing_generation)
UNIQUE(next_chain_intent_id)
```

Activity遇到pause/busy/quota reset等deferred时，必须先durable finalize/release local资源，并在返回typed outcome前用request/version CAS创建下一`resume_generation` waiter，冻结原始workflow/activity identity、剩余slice/ordinal hash和not-before domain；response ACK丢失时retry恢复同一waiter。`resume(open, epoch+1)`与control更新同一事务写`control_epoch_changed` outbox；consumer按两阶段claim查找`observed_control_epoch < new_epoch`的waiter，CAS `registered/signal_pending -> ready`、写唯一wakeup command，并向准确workflow/run发送携`waiter_id+resume_generation+epoch`的幂等signal，ACK/重投由workflow handler按最大generation去重，receipt落库。其他domain由resource event或reconciler在DB条件真正满足时CAS ready。Reconciler负责漏发/claim过期。

Patched workflow以“durable signal condition + 有界watchdog timer”竞速；signal丢失时watchdog仍会唤醒，但两者都只携原execution request ID、waiter ID/generation/routing generation和slice hash重新schedule Activity做DB重评，绝不直接创建grant或terminalize。Watchdog使用由workflow稳定hash导出的deterministic jitter和有界指数退避；pause/resource长期不恢复时不能高频制造Activity/history event。为每个workflow冻结history-event soft/hard budget并定期从Temporal info/describe取得可信近似；达到soft threshold必须走下面的waiter-aware Continue-As-New，hard threshold前保留足够命令余量，不能继续schedule无限watchdog。

Resume Activity第一步调用`claim_execution_resume()`，只允许当前routing generation的`ready`（或在同事务证实not-before已满足的registered）CAS为`claimed`，写实际claimant workflow/run/activity+随机token/expiry，并冻结request state/effect version、next grant generation、current reservation、预期browser fence token/boot或明确no-expected-browser、以及continuation version。每个reservation/effect/continuation关联变更必须在同事务单调bump request版本，不能靠“现在有没有行”或时间戳猜测。`ensure_execution_request()`只接受原始Activity identity，或与本次实际Temporal info完全匹配的有效claimed waiter；仅知道request ID不能推进。Grant成功在同一事务把waiter consumed并绑定准确reservation/generation；再次deferred则清理旧claim并创建下一resume generation。Claim commit ACK未知先按token read-back。

Claim-expiry reconciler只有在上述每个冻结version/ID/token/boot仍逐项相等、没有consumed reservation/closure receipt时才可CAS `claimed -> ready`；这样claim前已有历史reservation/effect不会被误判为新变化，而claim后的grant commit ACK丢失一定被version/reservation捕获。任何request effect、admin fence token/boot、continuation owner/version或cancel/closure变化都不得回ready：能确定业务closure时转cancelled，否则进入对应quarantine/人工恢复，绝不能让第二worker并行推进。状态CHECK要求claimed恰有claimant/token/expiry和完整snapshot，非claimed状态清空易误用的claim credential；consumed必须有matching reservation/generation，cancelled必须有closure receipt。Service-clock deadline不能靠wall timer直接判死，watchdog只触发DB service tick检查。Cancel/terminate由closure reconciler把waiter cancelled并按request policy终结。

History budget触发时允许“parked CAN”这一唯一例外：必须无live grant/fence/captcha handoff/claimed waiter，且恰有一个registered/signal_pending/ready generic waiter。`prepare_waiter_continue_as_new()`在同一`control -> assignment -> active chain -> request -> waiter`事务创建`kind=waiter_continue_as_new` closing operation，把chain active→closing、waiter当前routing generation→`transferring`，冻结readiness/not-before/request/effect/continuation snapshot，并创建唯一waiter-transfer和next chain intent；旧wakeup command/signal因routing generation失效。Prepare ACK丢失按closing operation+transfer read-back。CAN input携transfer ID/hash。

Transfer窗口的wakeup不能丢：control/resource consumer先按request解析current route；若waiter=transferring，就以expected readiness version把transfer的latest readiness/epoch/command单调推进，不向旧run授权claim。Successor `bootstrap_continue`验证真实continued-from与intent后，在同一bind/receipt事务读取**最新**readiness version，把旧waiter→transferred、创建或绑定保持同一execution request/resume generation/slice/deadline/readiness的新waiter并把routing generation+1/current workflow run改为successor；与并发wakeup的CAS只有一个顺序，事件若后提交就跟随successor current route重试。旧run的signal/claim永远rowcount=0，successor对ACK丢失read-back同transfer/bootstrap receipt。若workflow在发CAN命令前显式abort，必须用matching closing operation/intent/transfer version同时恢复chain active和旧waiter的新routing generation，并带回transfer最新readiness；CAN/ACK不明保持closing+transferring，不能凭absence恢复。这样多日全停可跨任意多个CAN而不丢等待题或撞Temporal history limit。

### 6.4 `collection_submission_operation`

把“允许向外部平台提交一次”的身份从物理 grant 中独立出来：

```text
id, pub_id, request_item_id
tenant_pub_id, run_pub_id, business_key
platform, adapter, region_gb, mode
submission_generation
operation_key_hash UNIQUE
query_hash, contract_hash
state: not_started / dispatching / accepted / terminal_not_sent / terminal_consumed / terminal_unknown / terminal_ungranted
current_reservation_item_id
dispatch_permit_id, dispatch_control_epoch
dispatch_governance_transition_policy_id NULLABLE
dispatch_governance_policy_version NULLABLE
dispatch_governance_classification_set_hash NULLABLE
dispatch_governance_action_set_hash NULLABLE
dispatch_browser_id NULLABLE, dispatch_browser_boot_id NULLABLE
dispatch_browser_context_generation_id NULLABLE
dispatch_browser_health_policy_id NULLABLE, dispatch_browser_health_policy_version NULLABLE
dispatch_browser_health_policy_contract_hash NULLABLE
dispatch_browser_health_classification_set_hash NULLABLE
dispatch_browser_health_action_set_hash NULLABLE
terminal_manifest_staging_id NULLABLE
latest_verified_result_staging_id NULLABLE
capture_promotion_effect_group_id NULLABLE
created_at, dispatching_at, accepted_at, terminal_at
```

约束和语义：

- `UNIQUE(tenant_pub_id, run_pub_id, business_key, platform, adapter, region_gb, mode, submission_generation)`；
- operation/request item/reservation 的 platform/adapter/region/mode/query hash 通过 composite FK 或受控函数逐项 compare；账号重选不能改变这些字段；
- 同一 request item 最多一个 nonterminal operation；
- pre-submit 的账号重选、bucket rollover、lease activation 失败只新建 grant generation，继续引用同一个 `not_started` operation，不增加 submission generation；
- `begin_submission()` 同事务锁 operation 并 CAS `not_started -> dispatching`。这才是跨 reservation/Temporal attempt 的发送唯一门；
- 同一个CAS从reservation复制并冻结dispatch-time governance transition policy ID/version/classification/action-set hash，以及browser/boot/context和browser-health policy ID/version/contract/classification/action-set hash；`not_started`时这些字段必须NULL，`dispatching/accepted/terminal_*`必须非NULL且不可改。Terminal governance/browser-health effect只能用operation冻结版及其复合FK，policy后来retire/cutover也不能重解释已经发送的outcome；
- `dispatching + confirmed_not_sent` 把本 operation 永久记 `terminal_not_sent`。若策略允许再试，另建 `submission_generation+1`；
- 若策略允许自动创建下一 submission generation，旧 `terminal_not_sent` operation 只产生 append-only 发送审计/资源结算；request item 仍为 waiting/active，并在同一事务把 current operation CAS 指向新一代。此时禁止生成 customer-facing `CollectionTask` 或题级最终 manifest，避免唯一 task/link 抢占后续成功代际。只有 deadline/cancel/最终策略决定不再发送时，才为 request item 写唯一 neutral terminal manifest/task；
- accepted/unknown operation 永远不能自动创建下一 submission generation。人工批准必须写 actor/reason/evidence 和独立审计；
- terminal facts 不覆写。Unknown 对账使用 adjustment/evidence，不把原 operation 伪装成未发生。

### 6.5 `collection_account_quota_reservation`

Reservation header 建议字段：

```text
id, pub_id
request_id, request_contract_hash, grant_generation
predecessor_reservation_id, generation_reason
protocol_version
tenant_pub_id, run_pub_id, workflow_id, workflow_run_id, activity_id
segment_key, chunk_ordinal
platform_account_id, quota_subject_id, browser_id
adapter, region_gb, mode
region_id, region_projection_event_id, region_health_epoch
region_applied_projection_generation, region_effective_fresh_until, region_health_policy_version
account_binding_revision_id, account_binding_version
binding_session_revision_id, binding_session_version, binding_session_gate_epoch
binding_session_state_snapshot, binding_session_evidence_hash, binding_session_verified_until NULLABLE
stable_platform_subject_hash, identity_alias_id
canonical_identity_scheme, canonicalizer_version, canonical_identity_alias_hash
quota_policy_id, quota_policy_version
quota_config_gate_epoch, quota_config_gate_state_snapshot
governance_transition_policy_id, governance_transition_policy_version
governance_event_classification_set_hash, governance_transition_action_set_hash
subject_governance_gate_version, subject_governance_gate_epoch
subject_governance_state_snapshot, subject_governance_evidence_hash
subject_mode_gate_version, subject_mode_gate_epoch
subject_mode_state_snapshot, subject_mode_evidence_hash
expected_scope_set_hash, expected_scope_count
expected_scope_gate_set_hash
requested_slots, granted_slots
state
reservation_token, state_version
lease_id, fencing_token, browser_boot_id
browser_context_generation_id, browser_health_gate_version, browser_health_gate_epoch
browser_health_policy_id, browser_health_policy_version, browser_health_policy_contract_hash
browser_health_classification_set_hash, browser_health_action_set_hash
browser_readiness_receipt_id, browser_readiness_validity_kind, browser_readiness_fresh_until NULLABLE
worker_runtime_scope_id, worker_scope_effect_epoch
holder_id, holder_session_id
reserved_at, activated_at, heartbeat_at, expires_at, max_deadline_at
terminal_at, terminal_reason
created_at, updated_at
```

必要约束：

- `UNIQUE(request_id, grant_generation)`，并由 request 表唯一保存 `request_key_hash`；
- 相同 request key 的 `request_contract_hash` 不同必须报 `execution_grant_payload_drift`；
- `UNIQUE(run_pub_id, segment_key, chunk_ordinal, grant_generation)`；
- partial unique：每个 request 只有一个 live reservation；
- partial unique：每个`platform_account_id`和每个`quota_subject_id`在live states中分别最多一条；后者防止换绑/重复管理行绕过账号级串行；
- requested/granted 非负且 `granted <= requested`；
- state `CHECK`；
- account/browser 使用 `ON DELETE RESTRICT`，历史授权不可级联删除。
- header中的account binding revision/version、quota subject、stable subject hash、identity alias及其scheme/canonicalizer/hash必须来自锁定的verified正式binding revision，并以composite FK证明同一snapshot；account header current pointer与grant revision不匹配时只能让旧grantcleanup/在尚未发送且已安全终结后重新选择，不得原地改写历史header。
- header中的binding session revision/version/gate epoch/state/evidence hash必须以composite FK指向该binding revision的append-only session revision，且授权时state=`verified`、未过期；session header current pointer在begin-submission前发生任何变化都撤销未发送effect。旧session revision保留历史FK，不能为迁就live reservation原地改写。
- header中的quota config gate必须来自同一quota subject，授权时state=`open`且current policy ID/version与header一致；activation、heartbeat和begin-submission逐项比较gate epoch/state/current policy。Cutover prepare与final open都会递增epoch，旧reservation不能在draining→open后复活。
- header/item/fence冻结同一worker runtime scope ID与effect epoch；live normal授权必须与holder boot receipt的current scope/InvocationID/boot逐项一致。Scope gate关闭后只能cleanup/settle/quarantine，不能原地刷新到新epoch。

`reservation_token` 是内部一致性/CAS 身份，不是单独的安全授权：任何 mutation 仍必须同时校验 request contract、holder、lease_id、fencing token、state version 和数据库角色。Token 不进入普通日志或客户接口。Activity 内部取得 grant，原则上不把 bearer token 写进 Temporal workflow history。

推荐 header 状态：

```text
reserved_unactivated -> active -> finalizing -> terminal
```

Header 只表达 quota/item 生命周期，不混入 browser quarantine。`terminal_reason` 可以是 completed、all_released、expired、cancelled 等；只有全部 granted items terminal 且所有 reserved effect 已归零时才能 terminal。Mixed batch 可能同时包含 consumed/released/unknown，不能把整个 header 简单标成 released。

同一 reservation terminal 后不能“复活”。账号重选、pre-submit bucket rollover、activation 失败等从未进入 dispatching 的情况只创建新的 `grant_generation`，继续引用原 `not_started` submission operation。`dispatching + confirmed_not_sent` 会终结原 operation，若策略允许再试则显式创建 `submission_generation + 1`；accepted/unknown 后绝不自动创建新的 submission generation，真正重发只能由人工/业务显式批准并保留旧事实。

### 6.6 `collection_account_quota_reservation_item`

每个 granted 题必须有一行，不要只在 header 上维护聚合数：

```text
id, pub_id, reservation_id, request_item_id, submission_operation_id
run_pub_id, business_key, ordinal, submission_generation
operation_key_hash_snapshot, request_hash
state, submission_disposition
outcome_class, quota_success_eligible
quota_bucket_set_hash
grant_lease_id, grant_fencing_token, grant_browser_boot_id
grant_worker_runtime_scope_id, grant_worker_scope_effect_epoch
dispatch_lease_id, dispatch_fencing_token, dispatch_browser_boot_id, dispatch_holder_session_id
dispatch_worker_runtime_scope_id, dispatch_worker_scope_effect_epoch
query_hash, mode
last_stage, stage_version
remote_conversation_ref, remote_request_ref
capture_staging_ref, capture_hash
task_pub_id, outcome_hash
submit_permit_id, submit_permit_expires_at, submit_permit_consumed_at
dispatch_control_epoch, permit_holder_session_id
prepared_at, dispatching_at, accepted_at, captured_at, settled_at
created_at, updated_at
```

建议 item 状态：

```text
reserved
  -> preparing
  -> dispatching
  -> accepted
  -> captured
  -> settled_consumed

reserved/preparing -> settled_released
dispatching        -> settled_released（仅原 holder 能证明 submit primitive 未调用）
dispatching        -> settled_unknown（是否接受未知）
accepted           -> settled_consumed（capture 失败也不再是发送未知）
captured           -> settled_consumed
```

`submission_disposition` 必须独立表达：

```text
not_attempted
confirmed_not_sent
accepted
unknown
```

禁止从 `status/error_type/wall_type` 推测是否已经发送。例如 `wall_quota` 既可能是发送前 banner，也可能是发送后平台返回；只有 adapter 在真实发送边界观察到的 disposition 才是额度结算真源。

一题可能同时受账号全局额度和 mode 额度约束，因此不能只在 item 上放固定三个 bucket FK。新增规范化关联：

```text
collection_reservation_item_quota_bucket
reservation_item_id, reservation_id
quota_subject_id, platform_account_id_snapshot
quota_policy_id, quota_policy_scope_revision_id, quota_scope_id
quota_scope_gate_epoch, quota_scope_grant_state_snapshot
quota_bucket_id
quota_bucket_baseline_state_snapshot, quota_bucket_block_version
period
created_at
UNIQUE(reservation_item_id, quota_bucket_id)
UNIQUE(reservation_item_id, quota_scope_id, period)
```

Grant时从quota subject的open config gate/current verified policy registry一次性生成`expected stable quota_scope_id × {day,week,year}`，做双向anti-join并冻结scope count/hash、各required scope的grant state/epoch集合hash及bucket baseline/block version；每个required scope和bucket都必须open/verified且无blocker。Reserve/debit/release/success/unknown effect必须覆盖同一集合。Link使用composite FK同时证明：reservation item属于header、header的`quota_subject_id`等于link subject、stable scope属于同subject、policy-scope revision属于header冻结policy且指向同一stable scope、bucket的scope/subject/period等于link；`platform_account_id_snapshot`只能等于header account且不参与授权唯一域。缺任一适用scope、混入别subject/mode scope、quota config/scope/bucket gate漂移或集合hash变化都fail-closed。Policy cutover后历史item继续引用旧policy revision和原stable bucket结算，不能改指新revision或新建零余额scope。

必要约束：

- `UNIQUE(reservation_id, ordinal)`；
- `UNIQUE(reservation_id, business_key, submission_generation)`；
- 同一 request item 同时最多一个 nonterminal reservation item；新的 grant generation 必须引用已 terminal 且明确未发送的 predecessor；
- operation identity 由 `collection_submission_operation.operation_key_hash` 全局唯一；reservation item 只保存 FK/不可变 hash snapshot；
- stage/version 使用条件 CAS，不能任意回退；
- terminal 状态必须有 `settled_at`；
- dispatching/accepted 必须冻结不可变 dispatch lease/token/boot/holder；capture 不能覆写发送 provenance；
- `task_pub_id` 如存在则唯一关联一个 item。
- dispatching 必须带一次性 permit、签发时 control epoch、准确 holder session 和短 expiry；permit 只能由同一 holder 消费一次，过期、epoch/token/boot 不匹配时 effectful submit 必须被拒绝。

唯一结算映射：

- `reserved/preparing + not_attempted`：reserved -1，reservation item released；submission operation 保持 not_started，可由下一 grant generation复用；
- `dispatching + confirmed_not_sent`：debit -1，reservation item released，operation=`terminal_not_sent`；此边只允许同一仍存活 holder 在一次性 permit 上证明 submit primitive 尚未调用，Activity retry、sweeper 或只看到 DB 行的进程不得推断；
- `dispatching + unknown`：debit 不变、unknown +1，operation=`terminal_unknown`；
- `accepted + refusal/wall/captcha/capture failure`：debit 不变、consumed，unknown 不增加，operation=`terminal_consumed`；
- validated result 成功且相应 success receipt 生效：success +1。

Operation、reservation item、request item projection、完整 bucket-set effects 和 outbox 必须在同一短事务条件更新；不能先 terminal operation 再另事务扣 bucket。Request header 的 completed/next cursor 可在同事务按统一锁序更新，或由幂等 projection 重建，但不能作为发送真源。

`submission_accepted_capture_unknown` 表示“发送已确认、捕获结果未知”，额度上仍是 consumed，不是 unknown-debit。`captured` 只表示 staging durable；是否是成功样本由 `outcome_class/quota_success_eligible` 和 task-success receipt 决定。用 CHECK 约束 state、disposition、时间戳和 eligibility 的合法组合。

### 6.7 Durable capture staging

仅有 submission ledger 仍不足以解决“已经抓到答案，但 Activity 返回 ACK 丢失”。目标方案固定使用仓库现有 MinIO/CAS 保存完整结果 payload 和恢复所需证据；数据库 staging 行只保存确定性 object key、content hash、schema/adapter version 和状态。必须定义：

```text
collection_capture_staging
id, pub_id
submission_operation_id FK ON DELETE RESTRICT
operation_key_hash_snapshot
request_item_id FK ON DELETE RESTRICT
reservation_item_id NULLABLE FK ON DELETE RESTRICT
capture_attempt_id NULLABLE FK ON DELETE RESTRICT
capture_generation
result_kind: terminal_manifest / verified_answer / evidence_bundle
object_key UNIQUE
content_hash, content_length, media_type
schema_version, adapter_version, contract_hash
required_asset_manifest_hash
state: pending / verified / corrupt / tombstoned
verified_at, retention_until, created_at, updated_at
UNIQUE(submission_operation_id, capture_generation, result_kind)
UNIQUE(capture_attempt_id, result_kind) WHERE capture_attempt_id IS NOT NULL
UNIQUE(id, submission_operation_id, request_item_id)
```

`reservation_item_id` 仅对真正获 grant 的发送事实存在；未 grant tail 仍通过 request item + operation key 保存 neutral manifest。Staging 是 append-only 多版本事实，不是“一 operation 一行”的可覆盖缓存：例如 generation 0 可以先保存 unknown terminal manifest，generation 1 再保存 captcha late verified answer；两行必须同时存在。`pending` 不是可恢复成功；只有 object 与全部 required assets 经读回/hash 校验后的 `verified` 才能驱动 result projection 或 terminal manifest 恢复。`corrupt` 必须告警并 fail-closed。Tombstone/GC 只能在 retention、task/legal hold 和 backup 条件全部满足后执行，不能删除 request/reservation item 审计事实。

发送额度生命周期与答案捕获生命周期正交。新增：

```text
collection_capture_attempt
id, pub_id, submission_operation_id, request_item_id
reservation_item_id NULLABLE
captcha_continuation_id NULLABLE
capture_generation
purpose: normal_capture / captcha_late_capture / recovery_observe
state: pending / verified / failed / outcome_unknown
capture_lease_id, capture_fencing_token, capture_browser_boot_id, capture_holder_session_id
verified_result_staging_id NULLABLE UNIQUE, staging_hash
started_at, verified_at, terminal_at, error_type
UNIQUE(submission_operation_id, capture_generation)
partial UNIQUE(submission_operation_id) WHERE state='pending'
```

Normal capture 也写 generation 0 attempt；post-submit captcha 可以在 quota reservation header/item 已 settled 后，以新的 capture lease/token 写 generation+1。Late capture 只新增 capture attempt、staging、capture receipt/effect 和 request-item result projection，绝不覆写 `dispatch_*` provenance、复活 terminal reservation item或重新 debit。Capture attempt 只能在其 `verified_result_staging_id` 指向同 operation/request item、同 generation、`result_kind=verified_answer` 且 staging 已 verified 时转 verified。若原 submission 是 unknown，而 verified late capture 足以证明平台已接受，则同一 adjustment effect group执行 `unknown -1, debit 0, captured +1`；原 unknown submission fact和 generation 0 terminal manifest仍保留并关联 resolution evidence。

Operation/request item 上的 `latest_verified_result_staging_id` 只是可重建 projection，必须用 expected `result_projection_version`、选优 policy version 和 capture generation CAS 更新；它不覆盖原 terminal manifest。已经生成的 `CollectionTask` 不随 projection 自动换答案，任何换版都要显式 revision/audit。成功 task/link 必须冻结选中的 `capture_attempt_id + staging_id + content_hash`，从而证明答案来自哪一代 capture lease。

- 完整、可重构的 `CollectionBatchItemResult`；
- 内容 hash 和 adapter/schema version；
- submission operation/request item ID，以及获 grant 时的 reservation item ID；
- 不可变账号、实例、region、mode、attempt provenance；
- 原始证据引用而非不稳定的同名本地路径。

写入协议：

1. 以 `submission operation + capture generation + result kind` 的确定性 key 写 CAS；内容寻址/条件 put 保证重复内容幂等；
2. 校验读取 hash；
3. DB staging 行 `INSERT ... ON CONFLICT` 比较同一 `(operation, capture generation, result kind)` 的 contract/content hash；
4. 必需资产全部 durable 后才能把 item 标为 captured；
5. CAS put 成功但 DB commit 丢失时，retry 用确定性 key/head 找回并补 staging；
6. DB staging 存在但 CAS 缺失/损坏时 fail-closed，不能重新发送题目掩盖；
7. 明确截图、HAR、SSE、share 等哪些是恢复结果的必需资产，哪些允许延后；
8. 给出单对象/总大小限制、公开平台原文的既有 DLP 边界、保留期、孤儿 GC、backup/restore 和 hash 巡检。

Activity retry 看到选中 projection 指向 `verified + matching hash` 时直接恢复结果。相同 `(operation, capture generation, result kind)` 出现不同 payload 必须 fail-loud，不得覆盖；不同 capture generation 的追加结果合法且必须保留旧行。

不只成功 capture 要 staging：wall、accepted-capture-failure、unknown、最终 neutral aborted 和未 grant tail 也必须保存可重构的 terminal result manifest，否则 attempt 2 无法精确恢复等长 batch，仍可能误发。单个 reservation item 的 `settled_released` 若 request item 仍 waiting，只是资源尝试审计，不得提前伪造成题级 terminal result；只有 workflow deadline/cancel/策略明确终结该 request item 时才写最终 neutral manifest。

证据文件 stem 必须加入 `run_pub_id + submission_operation_pub_id`，获 grant 时再附 `reservation_item_pub_id`，解决跨 run 相同 business key 覆盖本地路径的问题。

### 6.8 `collection_governance_outbox` 与 receipt

每个 submission operation/request item 的真实 terminal outcome必须在同一状态转换事务中先写**唯一权威governance gate effect**、同步应用admission-critical subject/mode/session gate，再插入只负责投影/通知的governance outbox。不能让terminal函数和outbox consumer分别对同一streak/gate做一次`+1`。Reservation grant/release另写append-only execution event，不把可重试资源释放冒充业务终态：

```text
collection_governance_transition_policy
id, platform, canonical_mode NULLABLE, policy_version
state: draft / verified / active / retired
failure_threshold, rate_limit_recovery_kind
event_classification_set_hash, transition_action_set_hash
verified_by, verified_at, evidence_hash
UNIQUE(platform, policy_version) WHERE canonical_mode IS NULL
UNIQUE(platform, canonical_mode, policy_version) WHERE canonical_mode IS NOT NULL
partial UNIQUE(platform) WHERE canonical_mode IS NULL AND state='active'
partial UNIQUE(platform, canonical_mode) WHERE canonical_mode IS NOT NULL AND state='active'

collection_governance_event_classification
id, transition_policy_id, event_kind, send_boundary_class, evidence_class
root_kind: platform_root / causal_suffix / capture_followup / task_followup / control
subject_action, mode_action, session_action
action: no_effect / record_success / record_failure / explicit_block /
        clear_gate_reset_streaks / verify_session / retire_session
streak_axis: none / subject / mode
required_effect_dimension_set_hash, action_contract_hash
UNIQUE(transition_policy_id, event_kind, send_boundary_class, evidence_class)

collection_governance_control_operation
id, operation_key UNIQUE
kind: session_verifier / admin_governance / expiry_reconciler /
      migration_baseline / reconciliation
quota_subject_id, canonical_mode NULLABLE, binding_revision_id NULLABLE
expected_subject_gate_version NULLABLE, expected_mode_gate_version NULLABLE
expected_session_revision/version/gate_epoch NULLABLE
target_action, policy_version, evidence_hash, not_before NULLABLE
state: prepared / claimed / applied / rejected / quarantined
claim_token, claim_generation, claim_expires_at
requested_by, created_at, applied_at NULLABLE
UNIQUE(id, kind)

collection_governance_gate_effect
id, source_kind, source_event_key_hash UNIQUE
terminal_submission_operation_id NULLABLE
governance_control_operation_id NULLABLE
source_generation
root_governance_event_key_hash, causal_predecessor_effect_id NULLABLE
request_item_id NULLABLE, event_generation
quota_subject_id, canonical_mode, binding_revision_id NULLABLE
effect_class: critical / audit_only
event_kind, event_contract_hash
transition_policy_id, event_classification_id, action_contract_hash
old/new subject gate version/epoch/state, subject streak delta
old/new mode gate version/epoch/state, mode streak delta
old/new session revision/version/gate epoch/state NULLABLE
effect_dimension_set_hash, applied_at
UNIQUE(root_governance_event_key_hash) WHERE effect_class='critical'
UNIQUE(terminal_submission_operation_id, source_generation, event_kind)
  WHERE source_kind='terminal_submission'
UNIQUE(governance_control_operation_id, source_generation)
  WHERE source_kind<>'terminal_submission'
CHECK((source_kind='terminal_submission') =
      (terminal_submission_operation_id IS NOT NULL AND governance_control_operation_id IS NULL))
CHECK((source_kind<>'terminal_submission') =
      (terminal_submission_operation_id IS NULL AND governance_control_operation_id IS NOT NULL))
CHECK(audit_only implies every gate/streak delta=0 and old=new)
```

`source_kind`只能取`terminal_submission/session_verifier/admin_governance/expiry_reconciler/migration_baseline/reconciliation`。Terminal来源以FK指向immutable submission operation；其他来源以`(governance_control_operation_id, source_kind)`复合FK指向同kind durable operation，恰一source FK非NULL。Session verifier、admin、expiry、baseline和reconciliation没有submission operation，不允许伪造一个假operation迁就schema；它们同样必须先prepare/claim、冻结target和expected version/evidence，再调用唯一内部`apply_collection_governance_effect()`。所有runtime角色撤销subject/mode/session表直接UPDATE和内部apply函数EXECUTE，只能调用各自窄prepare/apply入口。

Active policy选择规则必须唯一且无隐式fallback：带canonical mode的terminal/grant只能选择exact `(platform, canonical_mode)` active policy；subject-global control operation只能选择`canonical_mode IS NULL`的active global policy；mode control选择exact mode policy。缺行或多行均fail-closed。受控activate按`platform -> global/mode discriminator -> canonical mode`稳定键锁旧/新policy，验证classification/action双向anti-join与hash后在同一事务retire old、activate new并写activation receipt；上面的global/mode partial unique兜底，并发activate只有一个winner。Terminal/grant以`FOR SHARE`锁定解析出的active row并冻结ID/version/hash；cutover以排他锁竞争，所以不存在同一fact随机选两版。

Grant把唯一active transition policy ID/version/classification-set/action-set hash写入reservation。`activate_execution_grant`和`begin_submission`要求current active policy仍等于该snapshot；若policy已切换，尚未dispatch的reservation释放并按新policy重授权。`begin_submission`的同一CAS再把snapshot复制到submission operation和submit permit；一旦dispatching，terminal无论何时到达都只按operation冻结版分类/apply，不能改读current policy。Effect source-key ACK retry也read-back同一policy/action hash，不会因cutover选择另一阈值。Policy切换只影响新dispatch，不重写历史effect或已发送operation。

Event taxonomy必须由verified registry按平台、canonical mode、send boundary和terminal evidence确定，至少区分：真正平台root outcome；由该root导致的未发送suffix neutral/aborted；同root的capture generation/late capture；task-success；人工reconciliation。一次root可在一条effect中原子改变subject、mode及可选session多个维度，但`effect_dimension_set_hash`必须完整，不能拆成多个consumer各加一次。`suffix_neutral/never_granted_tail/late_capture_same_root`只能是`audit_only`且delta全0；late capture可以追加capture/unknown adjustment事实，但不再次增加root failure streak。新的独立人工证据若确需改变gate，使用新的approved control operation/root key和predecessor effect，不复用旧terminal event。

状态转移合同不能留给adapter自由发挥。首版registry必须冻结并由apply函数执行下表；任何平台差异只能发行新的verified policy version/action hash，不能在代码`if platform`旁路：

| 事实分类                                                                                     | send/quota事实                 | subject action                     | mode action                                  | session action                                   | streak合同                                                                                                   |
| -------------------------------------------------------------------------------------------- | ------------------------------ | ---------------------------------- | -------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| verified platform answer root                                                                | accepted/captured；debit已发生 | policy选择`record_success`或none   | policy选择一个且仅一个success轴              | none                                             | 目标轴`success=old+1, failure=0`；不自动解除任何blocked gate                                                 |
| explicit platform refusal/error root                                                         | accepted或明确平台响应         | policy选择一个且仅一个failure轴    | 与subject轴互斥，除非registry明确完整双轴set | none                                             | 目标轴`failure=old+1, success=0`；`new failure >= threshold`才以同effect进入指定block                        |
| quota wall（pre-submit已证明未发或post-submit已接受）                                        | 前者release、后者debit         | 通常none                           | `explicit_block(quota_blocked)`              | none                                             | 显式wall不再额外加failure；old streak保持，避免同root block+failure双算                                      |
| platform mute/rate-limit/hard account block                                                  | 按真实send boundary结算        | registry指定唯一global block       | 可选明确mode block                           | none                                             | 默认streak不变；`blocked_until`只按policy/evidence设置，不自行解门                                           |
| captcha required/session expired/credential tainted                                          | 按真实send boundary结算        | none                               | none                                         | 追加对应session revision并递增version/gate epoch | subject/mode streak delta=0                                                                                  |
| capture/DOM/parser/storage failure、unknown send、fence lost、worker/DB/Temporal infra error | 按发送ledger保守settle         | none                               | none                                         | none，除非独立可信session证据                    | 所有streak delta=0                                                                                           |
| pause/cancel/deadline/never-granted/neutral/suffix aborted                                   | release或无quota effect        | none                               | none                                         | none                                             | 所有streak delta=0，必须引用causal root（如有）                                                              |
| task success/materialization、同root late capture                                            | task/capture专用ledger         | none                               | none                                         | none                                             | 所有governance streak delta=0，不重放root                                                                    |
| admin clear / approved expiry recovery                                                       | 无quota effect                 | `clear_gate_reset_streaks`按target | 同左                                         | none                                             | 只在expected current gate/version与evidence匹配时`state=enabled, success=0, failure=0, gate version/epoch+1` |
| verified login/captcha assist                                                                | 无quota effect                 | none                               | none                                         | `verify_session`追加新verified revision          | session version/gate epoch+1；subject/mode streak不变                                                        |

`record_failure`阈值语义固定为`new_failure = old_failure + 1`后比较`new_failure >= failure_threshold`，threshold必须为正整数；未达阈值只推进projection version，达到阈值同一effect推进gate version/epoch，不能先加streak后异步block。`record_success`与`record_failure`互相把对侧连续streak清零。Explicit block不靠streak触发，不能再叠加一次failure。Blocked状态永远不能因普通success自动恢复或被较弱状态覆盖；clear/recovery必须引用current blocked effect、expected gate version和批准证据。

`blocked_until`只是expiry reconciler的`not_before`，时间到达本身仍保持fail-closed。Reconciler以两阶段claim/apply创建durable expiry operation，在DB now达到not-before、current state/reason/version仍匹配且policy允许该恢复类型时才写clear effect；claim过期重领、apply commit ACK丢失只read-back同operation/effect。Admin clear与并发terminal root、session verifier与旧captcha terminal都靠相同subject→account→session→governance锁序和expected-version CAS产生确定winner：较旧clear/verifier不能覆盖较新block；若clear先赢而随后出现新的真实platform root，后者可按新version重新block。Verifier还要冻结/验证captcha root effect与submission terminal high-watermark，存在尚未结算的前置session-root时不能先verify。

```text
outbox_id
governance_gate_effect_id UNIQUE
submission_operation_id
reservation_item_id NULLABLE
capture_attempt_id NULLABLE
result_staging_id NULLABLE
quota_subject_id, platform_account_id_snapshot
binding_revision_id, browser_id NULLABLE
canonical_mode
event_kind
event_generation, event_key_hash UNIQUE
payload/contract_hash
state, attempt_count, next_attempt_at
claim_token, claimed_at, claim_expires_at
delivered_at, last_error, poison_reason
created_at, updated_at
```

要求：

- `event_key_hash`按`source kind + source ID/generation + event kind + contract hash`规范计算；operation terminal事件、每一代capture事件和task-result revision事件各有独立key。不得只用`UNIQUE(submission_operation_id,event_kind)`挡住后续合法capture generation。每个新协议terminal result仍必须有operation，即使从未获grant；
- terminal transition在全局锁序下先CAS operation/item winner，再`INSERT governance_gate_effect ... ON CONFLICT DO NOTHING RETURNING`。Insert winner才按effect中冻结的old/new版本更新subject/mode/session权威行；loser必须逐项compare既有effect并read-back，绝不重放delta。Operation terminal、effect、critical projection、outbox FK与terminal manifest同commit；commit/ACK不明时retry只能恢复同一组事实；
- outbox必须引用已经applied的matching gate effect；不存在“先写outbox，稍后再决定critical gate”的状态。`audit_only`也有零delta effect，使consumer不必按event_kind猜是否该加streak；
- consumer 使用 `FOR UPDATE SKIP LOCKED`，以随机 claim token 和短 lease claim；
- 处理事务先无锁解析event target，再依次完成：校验claim token→按全局序锁冻结的external identity/quota subject、account、可选binding session、subject governance、browser及全部历史/当前projection bucket→read-back并compare已经applied的gate effect→`INSERT delivery receipt ... ON CONFLICT DO NOTHING RETURNING`→winner只做通知子outbox、legacy字段绝对SET和明确非critical派生→outbox delivered claim-token CAS→commit。它**不得**修改authoritative subject/mode/session gate、gate epoch或success/failure streak；
- delivery receipt与非critical副作用/outbox delivered绝不能分两次commit。Insert loser只在既有receipt contract完全相同且原outbox已delivered时read-back成功；出现“delivery receipt已有但outbox未delivered/投影缺失”属于原子性或越权漂移，必须poison/quarantine并告警，不能再补一次critical delta或盲目标delivered；
- 所有 ack/update 带 claim-token CAS，旧 claimant 不能覆盖重新 claim 的消费者；
- quota debit 已在发送边界完成，governor success 不得再次增加额度；
- 失败重试不会重复emit状态事件，root cause、suffix和late capture的因果link可重建。

Delivery receipt表必须显式定义`outbox_id/event_key_hash/governance_gate_effect_id/submission_operation_id/reservation_item_id/capture_attempt_id/result_staging_id/quota_subject_id/platform_account_snapshot/binding_revision_id/browser_id/canonical_mode/event_kind/event_generation/contract_hash/applied_at`、`UNIQUE(outbox_id)`、`UNIQUE(event_key_hash)`、FK、ACL。Gate effect、subject/account/binding snapshot必须经reservation/operation复合FK一致，consumer不得按当前browser反查subject。需要quota effect的event强制能经reservation item或capture attempt解析到原冻结bucket set；neutral ungranted事件不得产生quota effect。另建/约束`task_success_receipt(task_id UNIQUE, request_item_id UNIQUE, first_success_revision_id UNIQUE, source_reservation_item_id, event_key_hash UNIQUE, applied_at)`。Task/revision选择事务在同一commit写不可变success command，冻结当时的task/request item、selected revision/staging/hash、source reservation item和完整bucket-set count/hash。Consumer只读解析这些immutable FK后，按`quota subject -> account -> governance -> policy/scopes(如需) -> browser(如需) -> 全部bucket -> effect/receipt -> outbox CAS`加锁；**不得锁CollectionTask/Run，也不得先锁Task再取subject/account/bucket**。Insert task-success receipt winner才执行`success +1`；它与governance gate effect是不同且不重叠的事实。同一业务Task即使人工产生多个submission operation、多个capture generation或多次selection也最多贡献一次success；后续换成另一成功revision不重复计数。若必须撤销已确认success，只能走显式人工/reconciliation adjustment与审计，不得删除receipt或让普通selection静默搬账。Outbox支持poison/dead-letter、人工inspect/replay和完整审计，但replay仍服从同一receipt。

Activity finalize到workflow persist之间也必须durable。对`task_persistence_policy=persist`，每个request-item terminal manifest或新verified-answer selection在其execution/staging事务写确定性：

```text
collection_task_materialization_command
id, command_key UNIQUE
execution_request_id, request_item_id
submission_operation_id, reservation_item_id NULLABLE
capture_attempt_id NULLABLE, result_staging_id, result_content_hash
source_generation, predecessor_command_id NULLABLE, result_kind
selection_policy_version, task_persistence_policy_hash, contract_hash
state, claim_token, claim_expires_at, delivered_at, last_error
UNIQUE(request_item_id, source_generation)
UNIQUE(predecessor_command_id) WHERE predecessor_command_id IS NOT NULL
partial UNIQUE(request_item_id) WHERE state='applying'

collection_task_materialization_receipt
command_id UNIQUE, request_item_id, task_id, task_result_revision_id
contract_hash, applied_at

collection_task_lifecycle_receipt
request_item_id UNIQUE, task_id UNIQUE, creation_command_id UNIQUE
completed_projection_applied_at

collection_task_class_transition
task_id, selection_version, from_class, to_class
delta_completed, delta_success, delta_failed, applied_at
UNIQUE(task_id, selection_version)

collection_task_suppression_receipt
request_item_id UNIQUE, run_pub_id, assignment_id, task_persistence_policy_hash, reason, applied_at

collection_run_item_resolution_receipt
run_pub_id, request_item_id UNIQUE
resolution_kind: task_materialized / task_suppressed
task_lifecycle_receipt_id NULLABLE, task_suppression_receipt_id NULLABLE
item_contract_hash, applied_at
UNIQUE(task_lifecycle_receipt_id) WHERE task_lifecycle_receipt_id IS NOT NULL
UNIQUE(task_suppression_receipt_id) WHERE task_suppression_receipt_id IS NOT NULL
CHECK(exactly one source receipt matching resolution_kind)

collection_task_success_command
id, command_key UNIQUE, request_item_id UNIQUE, task_id UNIQUE
selected_result_revision_id, selected_result_projection_version, selection_policy_version
submission_operation_id, source_reservation_item_id
capture_attempt_id, result_staging_id, result_content_hash
expected_bucket_set_count, expected_bucket_set_hash
state, claim_token, claim_generation, claim_expires_at, applied_receipt_id NULLABLE
created_at, applied_at, last_error
```

Terminal transaction必须二选一：persist写materialization command；suppress写suppression receipt。Late capture/人工新operation的结果在request-item行锁内写严格连续的下一`source_generation`与predecessor link，但仍落同一Task的新revision。Materializer每request item最多一条applying，只能claim最小未完成generation；generation N必须看到N-1 receipt后才能apply，N先被worker看到时释放/延后，绝不能倒序创建或回退selection。Predecessor poison会fail-closed阻塞后代并告警，须修复/审计skip policy，不能静默越过。Revision/selection CAS同时比较source generation和selection policy，低代永不覆盖高代；initial operation来自generation 0而非最先到达的command。

Workflow的`persist_collection_result()`和后台materializer/reconciler只能claim/消费同一command；consumer在一个`Run -> Task -> Revision -> lifecycle/materialization receipt -> run-item resolution receipt -> command delivered`事务更新run/task投影，receipt与副作用不能拆commit。只有`collection_task_lifecycle_receipt`和对应run-item resolution receipt的insert winner创建业务Task并让run `materialized_task_count/resolved_item_count += 1`；后续revision不再重复run resolved计数。若run维护current success/failed数，selection改变时以`task_class_transition(task,selection_version)`对old→new做净delta，completed恒为0；同版本重复/ACK丢失不重复。符合采样候选条件的revision在同一Task事务写sampling candidate command，但sampling projector在独立锁域决定candidate/completed-sample/cell-observed，run resolution本身绝不计采样。Workflow在Activity finalize后被terminate、返回ACK丢失或persist Activity未调度时，后台仍会完成；重复workflow caller只reload相同receipt。

Suppress的execution/request-item terminal事务**只写immutable suppression receipt，不锁或更新Run**。独立run-domain projector先无锁解析receipt，再按`Run -> run-item resolution receipt`插入唯一winner并更新`resolved_item_count/suppressed_item_count`；它只读验证immutable request-item/run FK，不取得execution行锁。Suppression receipt存在时永不补Task，但governance ledger/staging照常保留；不能拿“没有Task”解释为“没有完成”。这样不存在request item→Run反向边，也不会因persist materializer持Run与suppress terminal持item而死锁。

Task/revision/selection事务创建success command时，必须用复合FK/受控函数冻结同一task/request item的**当前已选revision及其selection version**、该revision的operation/reservation/capture/staging/hash和reservation item bucket-set count/hash；success eligibility为假、无verified answer/capture或无grant reservation的revision不能建command。`task_success_receipt`反向引用command且继续以task/request item唯一。Consumer先无锁解析immutable command，再按quota subject→account→policy/scopes→browser(如需)→全部冻结bucket→effect/receipt→command final CAS取锁；不锁Task/Run，不查询“当前selected revision”替换command内容。相同唯一键ACK丢失只read-back；若selection后来变化，普通selection不删除/搬移已确认success，纠错只能显式reconciliation。

Run终结必须与“execution已结束”和“结果已物化/抑制齐全”分层，禁止workflow `finally`无条件把Run强置completed。为每个run冻结`run_execution_item_count`和规范ordered item-set hash，并建立幂等`collection_run_closure_command/receipt`或等价两阶段字段：`execution_state=active/terminal`、`materialization_state=pending/complete/poison`、`customer_state=running/completed/cancelled/failed`。Execution closure只证明全部request item已有唯一terminal fact、无live grant/fence/handoff/waiter；随后closure consumer按`Run -> request-item terminal summary/immutable receipts`校验双向anti-join：persist题必须各有连续materialization receipt+唯一lifecycle receipt，suppress题必须各有suppression receipt，pending/applying/poison command为0，item count/hash完全等于冻结run集合。只有校验winner才能CAS materialization complete并发布客户run终态。取消/失败可以先execution terminal，但结果补齐前必须显示`materialization_pending`而不是少算run完成；poison fail-loud并阻断“完成”发布。全suppress run以resolved/suppressed item统计完成，不能伪造Task；late revision只改变选中结果/class，不改变run expected/resolved计数。Campaign cell是否observed由独立sampling projector决定，与run customer terminal解耦。

`CollectionTask` persist不得在持有run锁时反向更新execution/reservation item。给`CollectionTask`增加immutable`execution_protocol_version/initial_submission_operation_id`（只作首次审计）、`execution_request_item_id`和`selected_result_revision_id/result_projection_version`。当前结果的operation/reservation/capture真源全部来自selected revision，不能用顶层initial operation解释后续人工submission generation。建立append-only：

```text
collection_task_result_revision
id, task_id, execution_request_item_id, revision, source_generation
source_materialization_command_id UNIQUE
submission_operation_id, reservation_item_id NULLABLE
capture_attempt_id NULLABLE, result_staging_id, result_content_hash
result_kind: terminal_manifest / verified_answer
supersedes_revision_id NULLABLE
selection_policy_version, created_at, actor, reason
UNIQUE(task_id, revision)
UNIQUE(task_id, source_generation)
UNIQUE(task_id, result_staging_id)
```

`CollectionTask.execution_request_item_id`对v1建立唯一约束，确保一个业务request item只有一条Task；revision中的request item必须与Task相同。

Task与首条revision存在插入循环，不能同时用两个immediate NOT NULL FK硬顶。物理上`selected_result_revision_id`保持nullable并增加`provenance_state=building/committed`以及`selected_source_generation`：同一task事务先插入/锁定Task(building)，再插Revision，随后以expected projection version、source generation和selection policy CAS selected revision/version并转committed。使用`DEFERRABLE INITIALLY DEFERRED`复合FK或deferred constraint trigger在COMMIT验证：所有v1 committed Task都有一条属于同task/request item的selected revision，selected revision反向指回同一Task；revision的source command/request item/generation/operation/staging与materialization receipt完全一致；selected source generation不得回退；v0可为空。Crash在任一步都整体回滚，重试按task/request-item唯一键恢复；API/UI/下游只读取committed Task。不得把building行跨事务暴露或用关闭约束解决循环。

Task事务只做`RUN -> TASK -> RESULT_REVISION -> TASK_OUTBOX`。协议v1 task必须关联request item并有selected revision；每个revision必须关联自己的operation，真正获grant的发送结果同时关联该operation的reservation item；带成功/可恢复答案的revision还必须关联同operation/request item的verified capture attempt与verified-answer staging并比较content hash。Neutral/wall revision绑定terminal-manifest staging，capture attempt可以为空。复合FK/受控服务校验tenant、run、business key、generation、result kind和hash一致。若unknown/accepted-capture-failure task已经存在，late capture或人工新submission operation只追加新revision并以expected projection version选择，绝不覆盖/删除原revision，也不创建第二个业务Task；是否对客户展示纠正必须由显式selection policy和audit决定。Task-success command必须引用这次选中的revision/staging/capture attempt以及该revision reservation item冻结bucket-set；最终由task/request-item唯一receipt保证一题最多`success +1`。若为了兼容在execution item上反向投影`task_pub_id`，使用独立幂等command：先完成Task事务并释放Run/Task锁，projection事务只锁execution item并只读验证immutable task/revision FK，不得持execution锁再取得Task/Run锁，也不得把该投影当成Task存在真源。

Expand阶段必须兼容既有v0历史task：request-item/initial-operation/revision link保持nullable，并以条件CHECK/约束trigger表达“`execution_protocol_version >= 1`才必须有request item、initial operation和selected revision；每条v1 revision必须有自己的operation；v1 success-answer revision才必须有capture/staging”。不得为了contract migration伪造历史operation，也不得把列直接改成对所有行NOT NULL。v1 worker返回缺provenance字段必须fail-closed，不能降级当作legacy。

即使assignment冻结`task_persistence_policy=suppress`（legacy input表现为`persist_results=False`），平台真实发送和治理事实也不能丢失；每题必须有suppression receipt。Submission operation + reservation item/effect/outbox是发送事实真源，不能依赖是否创建了customer-facing task。

### 6.9 强化 `browser_fence`

保留现有行和单调 token，禁止删除行重置 epoch。建议扩展：

```text
instance_key（当前列名 platform 可先兼容，不能误解为平台级锁）
state: free / held_unactivated / held / revoking / quarantined / recovering
lease_id UUID
holder_id
holder_workflow_id, holder_workflow_run_id, holder_activity_id, holder_attempt
holder_node_id, holder_worker_boot_uuid
holder_service_unit, holder_unit_invocation_id
holder_worker_runtime_scope_id, holder_worker_scope_effect_epoch
holder_pid, holder_pid_starttime, holder_cgroup, holder_container_id NULLABLE
holder_execution_scope_id NULLABLE
purpose: collection / captcha_assist / capture_continuation / maintenance / recovery
current_credential_install_receipt_id NULLABLE
handoff_state: none / captcha_pending / assist_held / resume_pending
handoff_id, handoff_run_pub_id, handoff_business_key, handoff_deadline
fencing_token bigint
acquired_browser_boot_id
acquired_at, heartbeat_at, expires_at, released_at
revoke_requested_at, revoked_at, revoked_by, revoke_reason
quarantine_reason
```

必要约束：

- instance 单行唯一；
- state `CHECK`；
- token 非负且永远单调；
- live lease_id 唯一；
- held_unactivated/held状态必须有lease_id/holder/expires；held_unactivated没有effect permission/current credential，held必须有matching current install receipt；
- free 状态不得保留活动 holder；
- handoff state、purpose、expected token/run/business key 必须满足跨字段 CHECK；
- API/worker 不得拥有 DELETE 权限。

单调性和权限不能只靠应用约定：

- 数据库 trigger 阻止 `fencing_token` 下降和 fence 行 DELETE；
- acquire、normal release、handoff owner 变更、`held/held_unactivated -> revoking/quarantined`、recover/free 和 browser boot 变更都必须原子 `token + 1`；同token的held_unactivated→held只在验证matching credential receipt后激活，只有 heartbeat 不递增。任何所有权/可写能力边界都使旧 heartbeat、permit 和连接失效；
- 每次状态/token/boot 变化与对应 `browser_fence_event` 在同一事务提交；
- 普通 worker/admin 撤销对 fence 的任意 UPDATE，只能调用带 expected lease/token/state/version 的受控函数或最小列权限语句；
- `recover/free` 和当前 boot identity 只能由独立 privileged recovery/supervisor role 写；若使用 `SECURITY DEFINER`，必须固定安全 `search_path`、限定 owner 并做权限测试。

新建 append-only `browser_fence_event`，记录 acquire、heartbeat-loss、normal-release、revoke、quarantine、restart、recover 的 old/new token、boot ID、actor 和原因。

浏览器当前 boot identity 与 lease snapshot 必须分开：

- `CollectionBrowser.current_boot_id/current_invocation_id/current_process_start_id/current_recovery_epoch` 只允许 supervisor/recovery role 通过受控函数写；recovery epoch 单调；
- `BrowserFence.acquired_boot_id` 在 acquire 时冻结；
- reservation/item 保存相同 acquired boot snapshot；
- heartbeat、strong validation、begin submission、release 都比较 current boot 与 acquired snapshot；
- held 期间 resident browser 自动重启必须 token+1/quarantine，gateway 注册并 recover 前不得开放。

Systemd InvocationID 不足以单独证明 Chromium 子进程已更换；还要验证 Chrome PID starttime/cgroup，或使用 supervisor 生成并绑定实际进程的 boot nonce。

新增 durable `browser_recovery_operation`：

```text
operation_id UNIQUE, instance_key
recovery_epoch, claim_generation
expected_fence_token, expected_old_boot_id
target_new_boot_id
worker_runtime_scope_id, expected_worker_scope_epoch
parent_worker_scope_recovery_operation_id NULLABLE
phase: quarantined / holder_fenced / browser_stopped / browser_started / boot_committed / gateway_synced / sealed / completed
claim_token, claim_expires_at
holder_blast_radius_count, holder_blast_radius_set_hash
current_action_sequence, current_command_id, systemd_job_id NULLABLE
old/new unit InvocationID, PID starttime, cgroup
context_taint_receipt_id, reset_or_restart_receipt_id
supervisor_boot_id, supervisor_ack_hash
state, last_error, created_at, updated_at
UNIQUE(instance_key, recovery_epoch)
```

每次重新 claim 都原子增加 `claim_generation`。数据库 claim token 只负责工作分配，不能授权 systemctl；真正的外部动作还必须由 node-local supervisor 对 `(recovery_epoch, claim_generation, phase, expected boot/token)` 做接收端 fencing。

Per-instance fence不能代表共享worker service unit的物理影响域。新增稳定runtime scope及其scope级恢复协议：

```text
collection_worker_runtime_scope
id, scope_key UNIQUE
node_identity, worker_service_unit
current_unit_invocation_id, current_worker_boot_uuid
state: open / draining / emergency / recovering
effect_authorization_epoch
active_scope_recovery_operation_id NULLABLE
version, updated_at
UNIQUE(node_identity, worker_service_unit)

collection_worker_scope_recovery_operation
id, operation_key UNIQUE, worker_runtime_scope_id
expected_unit_invocation_id, expected_worker_boot_uuid
scope_recovery_epoch, expected_scope_effect_epoch
state: prepared / gate_closed / blast_set_frozen / members_quarantined / unit_job_pending / unit_stopped / browsers_started / physical_sealed / release_pending / members_released / blocked / completed / superseded
expected_member_count, expected_member_set_hash
supervisor_resource_set_hash, supervisor_seal_receipt_id NULLABLE
physical_isolation_receipt_id NULLABLE UNIQUE
claim_generation, claim_token, claim_expires_at
created_at, completed_at, last_error
UNIQUE(worker_runtime_scope_id, scope_recovery_epoch)
partial UNIQUE(worker_runtime_scope_id) WHERE state NOT IN ('completed','superseded')

collection_worker_scope_recovery_member
scope_recovery_operation_id, instance_key
lease_id, fencing_token, browser_boot_id, holder_session_id
reservation_id NULLABLE, browser_recovery_operation_id
physical_isolation_receipt_id NULLABLE, terminal_settlement_receipt_set_hash NULLABLE
release_blocker_count, release_blocker_set_hash
state: frozen / quarantined / physically_sealed / release_pending / released / terminal
UNIQUE(scope_recovery_operation_id, instance_key, lease_id, fencing_token)

collection_worker_scope_recovery_member_release_blocker
id, scope_recovery_member_id
blocker_kind: workflow_assignment_terminal_obligation / reservation_settlement / run_materialization / administrative_hold
blocker_key, workflow_assignment_id NULLABLE, initial_chain_generation_id NULLABLE
observed_assignment_termination_obligation_epoch NULLABLE
observed_closing_operation_id NULLABLE
expected_terminal_contract_hash, terminal_receipt_id NULLABLE
state: pending / satisfied
UNIQUE(scope_recovery_member_id, blocker_kind, blocker_key)

collection_worker_scope_member_hard_request_obligation
scope_recovery_member_id, workflow_assignment_id, hard_termination_request_id
initial_target_chain_generation_id, initial_target_physical_target_id
latest_target_chain_generation_id, satisfying_closing_operation_id NULLABLE
assignment_termination_obligation_epoch
hard_closure_terminal_receipt_id NULLABLE
state: pending / following_successor / satisfied / quarantined
UNIQUE(scope_recovery_member_id, hard_termination_request_id)

collection_worker_scope_physical_isolation_receipt
id, scope_recovery_operation_id UNIQUE, worker_runtime_scope_id
scope_recovery_epoch, expected_scope_effect_epoch, closed_scope_effect_epoch
expected_old_unit_invocation_id, expected_old_worker_boot_uuid
supervisor_resource_set_hash
expected_member_count, expected_member_set_hash
unit_kill_action_receipt_id UNIQUE
old_holder_exit_receipt_count, old_holder_exit_receipt_set_hash
child_physical_seal_receipt_count, child_physical_seal_receipt_set_hash
new_boot_context_gateway_receipt_count, new_boot_context_gateway_receipt_set_hash
scope_supervisor_seal_receipt_id UNIQUE
supervisor_boot_reconciliation_receipt_id
raw_listener_gate_receipt_hash, receipt_contract_hash, applied_at
```

`scope_key`稳定表示node上的service unit；InvocationID/worker boot是该scope当前代际，不能放进可复用主键后靠新行绕过旧gate。每个worker boot receipt必须FK到该scope并冻结scope epoch。所有normal execution受控函数，尤其grant、activation、heartbeat normal续权和`begin_submission`，都按`control -> assignment -> chain -> worker runtime scope`以共享/条件锁读取当前scope，要求`state=open`、InvocationID/boot匹配且poller receipt中的scope epoch等于当前值；多个不同账号/browser的normal grant可以共享open scope并发，不把service unit行当业务互斥锁。Scope recovery才以排他锁关闭门；scope重新开放只递增epoch，旧Activity不会恢复normal。

Scope的current InvocationID/worker boot只能由可信supervisor registrar在startup reconciliation receipt齐全后通过expected-version受控函数更新，普通worker和API无UPDATE权限。观察到unit意外重启、boot receipt与scope current identity不一致或systemd manager boot变化时，必须先CAS scope为emergency并递增epoch，再枚举旧boot blast set；不能直接把新boot写成current并继续。新worker即使已经开始进程，也只能等待scope recovery/seal后登记normal poller，不能靠新PID/空内存自动获得授权。

`collection_worker_scope_physical_isolation_receipt`是“旧writer已经物理不可写”与“browser可以重新分配”的中间线性化事实，二者不能合并。它是append-only，并以复合FK/受控函数绑定scope operation、旧unit/worker boot、关门后的scope epoch、规范resource set和完整member set。只有unit kill已终态、每个旧holder的PID/starttime/cgroup/socket均有exit/barrier证据、每个child已完成新browser boot commit、旧context销毁或reset、gateway全量同步及supervisor seal、旧boot supervisor journal无相交未终态动作、raw listener对normal worker仍不可达且只开放固定recovery allowlist时才能写。受控函数对member、holder-exit、child-seal和new-boot/context/gateway四组集合分别做count/hash及双向anti-join；receipt commit后scope和fence仍是`recovering/quarantined`，没有normal effect permission。Hard target只有通过matching member引用这条receipt才可`isolated`，不能仅凭scope operation ID或child `sealed`投影。

每个member的release blocker同样必须有规范化行而不是只有hash。Workflow根必须使用稳定的`assignment_id + terminal obligation contract + observed assignment termination-obligation epoch`，并保存冻结时的initial chain generation作审计；**不能**只绑定原generation，因为它合法Continue-As-New后hard request可能沿同一assignment迁到successor。其余根使用reservation/item settlement、run materialization或明确administrative hold。Header count/hash与成员行双向一致；为避免scope→chain反向锁，blocker只存immutable ID/hash snapshot，不建立会在scope事务中取得workflow/Run行锁的普通或deferred FK。

Hard request prepare、follow-successor/bootstrap bind和watcher owner建立/接管在其既定`assignment -> chain/closing/intent -> assignment terminal receipt -> termination request header -> runtime scope/member -> ... -> work claim final CAS`锁序内，必须为每个命中的scope recovery member创建或推进唯一`member_hard_request_obligation`，递增assignment termination-obligation epoch并冻结request initial target；request沿CAN时只更新append-only target event与link的latest target/satisfying closing，不能把原generation `continued`当terminal，也不能另建一个遗漏旧member的request。Assignment域的最终terminalizer必须把start operation、全部chain generation与successor intent看成一个闭包：只要start仍为`prepared/claimed/rpc_dispatching/outcome_unknown/started`且没有matching终态receipt，或任一generation仍为`intent/bootstrap_pending/active/closing`，或存在未决CAN successor，就不得写terminal receipt。合法完成证明只有三类且互斥：A) 无termination root的普通cooperative normal-return/内部cancel，actual final generation已由matching closing owner置completed，全部item/materialization、holder/effect清零且无successor；B) termination root的`unstarted_assignment` no-run receipt完成；C) termination root的`chain_generation`已由matching existing cooperative closure标`satisfied_by_existing_closure`，或其当前satisfying hard/watcher closing四轴completed，并且root/全量item/materialization terminal contract齐全。满足其一才写immutable assignment-terminal receipt/command；独立projector再按scope/member锁序把assignment blocker和所有termination-request links标satisfied。Terminal receipt已经存在时的新post-terminal intent不递增obligation epoch、不创建link/blocker，也不会使已satisfied blocker复活。Logical release在member锁内对冻结blockers及所有引用该member的request/closure physical target links做双向anti-join，要求obligation epoch无缺口且全部terminal receipt齐全；termination request与logical release因共同锁scope/member而只有“request/link先进入集合”或“member先安全released、request不得再把它当live writer”两个顺序。Scope worker不能自报完成，也不能因暂时没看到closing、原generation continued、start receipt未落库或request正在following successor而删掉blocker。

共享unit stop/kill的线性化顺序必须固定：

1. 以expected InvocationID/boot锁runtime scope并CAS `open -> emergency`、递增scope effect epoch；与grant的scope共享锁竞争，故要么先发生的grant提交并被下一步枚举，要么scope gate先赢且该grant在任何资源/attach/send前失败；
2. 在同一短事务中按instance key锁所有仍指向该scope+旧boot的live fence，创建scope operation/member快照和child browser recovery operation，以双向anti-join及count/hash冻结完整blast set。不能先无锁扫描、稍后再关gate；
3. 事务外逐成员停止新permit、冻结其发送边界并quarantine；已转发题保守进入accepted/unknown settlement流水，但**物理kill不等待所有业务settlement完成**。只要完整blast set已冻结、normal effect已撤销且所有成员已进入可停止屏障，就可继续物理隔离；漏成员、身份漂移或仍可能签发新effect才阻止unit job。Settlement轴随后独立收口，不能拿它冒充physical receipt；
4. Supervisor对完整resource set只执行一次scope级unit stop/kill，证明旧holder/unit终态，再重启/提交各browser新boot、销毁或reset旧context、同步gateway并逐child physical seal；此阶段fence保持`quarantined/recovering`、scope保持`recovering`且raw端口不向normal worker开放；
5. 全部child physical seal和旧动作终态齐全后，scope supervisor seal并在DB原子写上述scope physical-isolation receipt；matching hard target此时可推进physical axis，但child仍不能free/completed；
6. 每个member的全部release blockers（包括引用它的hard/watcher closure）各自完成后，才允许把该child `recovering -> free`、browser recovery `sealed -> completed`并把member标released/terminal。全部member settlement与release完成、无live blocker且receipt仍matching后，DB才把scope operation标completed、把scope `recovering -> open`并再次递增epoch。若无法精确确定共享scope，升级global emergency，不能猜一个较小blast radius。

`blocked` operation仍占据live-owner唯一约束并保持scope emergency，不能用“失败了”绕过。只有已证明旧supervisor/resource action全部terminal、保存supersede receipt并把完整member/settlement责任原子移交给明确successor operation时才可转`superseded`；successor不得遗漏旧member或重置scope epoch。

Supervisor不能只按instance建互不相知的队列。每条命令必须冻结其**物理资源集合**，例如稳定的`node/<id>/worker-unit/<unit>`、`node/<id>/browser-unit/<unit>`、`browser/<instance>`；任何instance command必须声明会触及的祖先unit key，可能从instance升级为共享unit kill时须先回DB创建scope operation并重新adopt完整集合，禁止运行中锁升级。Supervisor按`node -> worker unit -> browser unit -> instance`及规范key排序获取resource executor/lease；有任一资源交集的命令严格串行，无交集的不同账号/浏览器仍可并行。两个instance recovery若目标同一worker unit，只能由同一scope operation编排，不能各自提交stop/start并分别seal。

Supervisor还必须有独立于进程内存的append-only、fsync持久化command journal，并把终态receipt镜像回DB：

```text
collection_node_control_plane_release
id, component_kind: supervisor / gateway
release_name, binary_artifact_digest, config_contract_hash
control_protocol_min, control_protocol_max
journal_writer_schema_version, journal_reader_count, journal_reader_set_hash
command_contract_set_hash, terminal_kind_set_hash, db_receipt_contract_set_hash
credential_contract_hash NULLABLE, barrier_contract_hash NULLABLE
captcha_effect_contract_hash NULLABLE, network_isolation_policy_hash
state: approved / draining / revoked
registered_by, registered_at, evidence_hash
UNIQUE(component_kind, release_name)
UNIQUE(component_kind, binary_artifact_digest, config_contract_hash)

collection_node_control_plane_release_journal_reader
release_id, journal_kind, journal_schema_version
record_contract_hash, command_kind_set_hash, terminal_kind_set_hash
minimum_writer_protocol, maximum_writer_protocol
UNIQUE(release_id, journal_kind, journal_schema_version, record_contract_hash)

collection_supervisor_boot_attestation
supervisor_boot_id PRIMARY KEY, node_identity, release_id
binary_artifact_digest, config_contract_hash
journal_reader_set_hash, control_protocol_min, control_protocol_max
systemd_manager_boot_id, process_start_identity, attestation_hash, started_at

collection_gateway_boot_attestation
gateway_boot_id PRIMARY KEY, node_identity, release_id
binary_artifact_digest, config_contract_hash
journal_reader_set_hash, control_protocol_min, control_protocol_max
network_isolation_policy_hash, process_start_identity, attestation_hash, started_at

supervisor_command_journal
command_id UNIQUE, operation_id, operation_kind
writer_release_id, writer_artifact_digest
journal_schema_version, record_contract_hash, minimum_reader_protocol
command_contract_hash, terminal_kind_set_hash
resource_set_hash, ordered_resource_keys
issuer_recovery_epoch, issuer_claim_generation, phase_sequence
accepted_supervisor_boot_id
state: prepared / accepted / dbus_submitted / in_flight / outcome_unknown / reconciling / terminal
systemd_manager_boot_id, systemd_job_id NULLABLE, systemd_job_path NULLABLE
expected_unit_invocation_id, expected_pid_starttime, expected_cgroup
observed_unit_state NULLABLE, observed_unit_invocation_id NULLABLE
observed_pid_starttime NULLABLE, observed_cgroup NULLABLE
terminal_kind NULLABLE, terminal_receipt_hash NULLABLE
prepared_fsync_at, accepted_fsync_at, terminal_fsync_at NULLABLE

collection_supervisor_os_action_receipt          # DB append-only mirror
command_id UNIQUE, operation_id, supervisor_boot_id
writer_release_id, journal_schema_version, record_contract_hash
resource_set_hash, recovery_epoch, claim_generation, phase_sequence
systemd_manager_boot_id, systemd_job_id NULLABLE
terminal_kind, actual_unit/process/cgroup_snapshot_hash
local_journal_terminal_receipt_hash, applied_at

collection_supervisor_boot_reconciliation_receipt
supervisor_boot_id UNIQUE, node_identity
supervisor_release_id, journal_reader_set_hash
predecessor_supervisor_boot_set_hash
reconciled_command_count, reconciled_command_set_hash
unresolved_command_count
systemd_manager_boot_id, actual_resource_inventory_hash
raw_listener_gate_receipt_hash, reconciled_at
CHECK(unresolved_command_count = 0)

gateway_control_command_journal
command_id UNIQUE, record_kind: credential_install / purpose_barrier /
                              pause_or_revoke_barrier / captcha_effect
writer_release_id, writer_artifact_digest
journal_schema_version, record_contract_hash, minimum_reader_protocol
command_contract_hash, terminal_kind_set_hash
instance_key, lease_id, fencing_token, gateway_boot_id, gateway_connection_id NULLABLE
operation_or_permit_id, phase_sequence, state
prepared_fsync_at, forwarded_fsync_at NULLABLE, terminal_fsync_at NULLABLE
terminal_kind NULLABLE, terminal_receipt_hash NULLABLE
```

先fsync prepared intent，才允许向systemd D-Bus/child/pidfd发命令；取得job ID/path后立即fsync accepted/in-flight，实际终态及unit/process/cgroup证据再次fsync后才能ACK DB。`D-Bus调用已发出但返回丢失`必须记`outcome_unknown`，不得因没有job ID重发同一stop/start/kill。Supervisor若在send与第二次fsync之间崩溃，新进程看到旧boot的`prepared`也必须按“可能已提交”进入reconciliation/outcome-unknown，而不能重发或标confirmed-not-started；这是本地journal与D-Bus之间无法原子提交时的保守边界。

Journal必须位于supervisor专属的持久卷/目录，不可放`/tmp`或仅依赖journald；可用SQLite WAL `synchronous=FULL`或等价append log，但必须校验record checksum、fsync数据及必要目录元数据、处理磁盘满/只读/损坏并fail-closed。只有DB action receipt已提交、对应operation/scope seal完成且保留期满足后才能compaction；不得在服务启动脚本中清空。目录owner/SELinux/AppArmor权限只给supervisor principal，普通worker不能伪造terminal receipt。Systemd unit ordering必须让gateway/raw-listener开放条件依赖supervisor reconciliation-ready，而不是二者并行启动形成窗口。

Supervisor与gateway发布也必须遵守expand-reader→switch-writer→contract的协议。每个boot由可信registrar绑定immutable release/digest/config与reader set；每条本地journal及DB mirror都携writer release、schema version、record/command/terminal合同和minimum reader。Startup在宣布ready前扫描**全部仍保留**的记录，并对`(journal kind, schema version, record contract hash)`与本boot registry member做双向匹配；任一未知version、未知terminal kind、hash不符、截断/损坏或minimum reader不满足，都进入`incompatible_journal`、保持raw listener关闭和scope emergency/reconciling，明确告警且绝不跳过、按“未知即terminal”或先compaction。Gateway对credential/barrier/captcha effect journal执行同一规则；它未reconcile为零时不能开放upgrade或签ACK。

滚动升级先部署能读取旧+新schema但仍写旧schema的reader-capable release，验证所有boot attestation后才切新writer；contract只能在所有可能回滚候选都不再需要旧reader、且相应records满足DB receipt/operation完成/保留期后进行。任何新writer→旧reader演练失败的版本组合不在rollback集合中；保留当前基础设施binary继续reconcile，比强退一个不认识journal的旧binary安全。

新supervisor进程启动默认处于`reconciling`：独立网络/OS门禁继续使raw CDP对worker不可达，它拒绝新的adopt、seal和所有destructive command。它必须装载所有旧supervisor boot的非终态journal，查询systemd manager boot/ListJobs/job path以及实际unit InvocationID、PID starttime、cgroup和child/pidfd状态，逐条证明job已终态/安全取消并落reconciliation receipt；job已从ListJobs消失时也必须以命令特定postcondition和当前物理状态证明，不得把“查不到”当作成功。无法区分提交与否或结果时，把所有相交runtime/browser scope保持emergency/quarantined，并交给新的显式recovery generation；在证明旧job不可能迟到生效前不得ACK adopt/seal、开放raw port或签发grant。只有旧boot accepted/in-flight/outcome_unknown集合为零、全部resource set已对账，且DB登记matching supervisor boot reconciliation receipt后才进入ready。Supervisor自身DB不可达同样保持reconciling/fail-closed。

迁移时先为每个 `CollectionBrowser.instance_key` seed 一条 fence 行，避免运行期首次插入 gap race。

浏览器页面/context状态也必须durable，不能只存在worker内存。新增`browser_context_generation`/等价表，至少记录`instance_key/browser_boot_id/context_generation/state(clean/owned_dirty/owned_dirty_handoff_pending/owned_dirty_transfer_pending/tainted/resetting/retired)/current_owner_lease_id/token/holder/reservation NULLABLE/last_operation_id/last_forwarded_sequence/pending_effect_count/taint_source_operation_id/taint_reason/tainted_at/isolation_method/cleanup_or_reset_receipt_id/current_purpose_handoff_operation_id NULLABLE/current_owner_transfer_operation_id NULLABLE/verified_at`。

正常、事实已知的`dispatching/accepted/captured`只把clean context转为绑定当前lease/token/holder/reservation的`owned_dirty`；它不改变browser health gate，且同一holder/grant在上一operation已terminal、pending effect/frame/stream=0、forwarded sequence闭合后可以顺序处理下一题。`begin_submission`接受`clean`，或接受owner/lease/token/reservation全部等于当前grant且前序operation已闭合的`owned_dirty`；其他owner、新grant或只凭page看似稳定均拒绝。这样K-item grant的第1题不会让第2题因通用“tainted”门禁失败。

`lost/outcome_unknown`、holder/token/boot不可信、detach/barrier sequence无法闭合、存在未知已转发命令或跨owner状态不明才把context置`tainted`并推进health gate/quarantine；tainted不能走normal cleanup/free。正常batch结束时，仍持有效token的同一holder执行定义明确的page quiescence/reset：证明全部operation terminal、网络/stream/下载/弹窗等平台特定pending set为0，执行verified返回neutral page或销毁并重建context，关闭旧connection并取得最后forwarded sequence/barrier ACK，写append-only`collection_browser_context_cleanup_receipt`，再以CAS把owned_dirty→clean。Cleanup失败/ACK不明转tainted/quarantine。`networkidle`、DOM稳定或单纯`page.close()`本身都不是充分证明。若某平台无法提供安全normal cleanup，必须明确采用每批context reset/restart、验证登录态与容量，而不能把普通成功悄悄当tainted后又直接free。Recovery seal/free必须引用相应cleanup或restart/reset receipt。

Cleanup本身是可崩溃外部I/O，使用durable operation/member/receipt而不是自报`pending=0`：

```text
collection_browser_context_cleanup_operation
id, operation_key UNIQUE, browser_id
browser_boot_id, old_context_generation_id, cleanup_generation
lease_id, fencing_token, holder_session_id, reservation_id
expected_operation_count, expected_operation_set_hash
required_check_kind_count, required_check_kind_set_hash
expected_gateway_connection_id, expected_last_forwarded_sequence
cleanup_method: neutral_page_reset / destroy_and_recreate_context
state: prepared / executing / receipt_ready / applied / quarantined
claim_token, claim_generation, claim_expires_at, created_at, applied_at NULLABLE
UNIQUE(old_context_generation_id, lease_id, fencing_token, cleanup_generation)

collection_browser_context_cleanup_operation_member
cleanup_operation_id, submission_operation_id
terminal_state, terminal_receipt_id, last_forwarded_sequence
UNIQUE(cleanup_operation_id, submission_operation_id)

collection_browser_context_cleanup_check
cleanup_operation_id, check_kind
check_kind: pending_cdp_command / network_request / response_stream /
            download / popup / dialog / worker / gateway_frame
observed_count, observation_receipt_hash, observed_at
UNIQUE(cleanup_operation_id, check_kind)

collection_browser_context_cleanup_receipt
id, cleanup_operation_id UNIQUE, browser_id, browser_boot_id
old_context_generation_id, new_context_generation_id
lease_id, fencing_token, holder_session_id, reservation_id
operation_count, operation_set_hash
check_kind_count, check_kind_set_hash, total_pending_count
gateway_connection_id, last_forwarded_sequence
gateway_barrier_event_id, gateway_barrier_ack_hash
cleanup_method, cleanup_command_id UNIQUE
prober_boot_receipt_id, supervisor_boot_receipt_id NULLABLE
cleanup_contract_hash, evidence_hash, applied_at
CHECK(total_pending_count=0)

collection_browser_context_owner_transfer_operation
id, operation_key UNIQUE, continuation_id, browser_id
browser_boot_id, context_generation_id
predecessor_reservation_id, successor_reservation_id
old_lease_id, old_fencing_token, old_holder_session_id, old_holder_process_identity
new_lease_id, new_fencing_token, new_holder_session_id, new_holder_process_identity
gateway_connection_id, predecessor_credential_install_receipt_id
root_submission_operation_id, root_terminal_receipt_id, root_staging_or_capture_receipt_id
expected_operation_count, expected_operation_set_hash
expected_pending_effect_count, expected_last_forwarded_sequence
state: prepared / barrier_pending / receipt_ready / activated / quarantined
created_at, activated_at NULLABLE
UNIQUE(continuation_id, successor_reservation_id)
UNIQUE(context_generation_id, new_fencing_token)
CHECK(old and new holder session/process identity are exactly equal)

collection_browser_context_owner_transfer_member
owner_transfer_operation_id, submission_operation_id
terminal_state, terminal_receipt_id, last_forwarded_sequence
UNIQUE(owner_transfer_operation_id, submission_operation_id)

collection_browser_context_owner_transfer_receipt
id, owner_transfer_operation_id UNIQUE
continuation_id, browser_id, browser_boot_id, context_generation_id
predecessor_reservation_id, successor_reservation_id
old_lease_id, old_fencing_token, new_lease_id, new_fencing_token
holder_session_id, holder_process_identity, gateway_connection_id
predecessor_credential_install_receipt_id
root_submission_operation_id, root_terminal_receipt_id, root_staging_or_capture_receipt_id
operation_count, operation_set_hash, total_pending_effect_count
last_forwarded_sequence, barrier_event_id UNIQUE, barrier_ack_hash
old_purpose, new_purpose, receipt_hash, verified_at
CHECK(total_pending_effect_count=0)

collection_browser_context_purpose_handoff_operation
id, operation_key UNIQUE, continuation_id, browser_id
kind: collector_to_captcha_assist / assist_to_capture_root
browser_boot_id, context_generation_id, continuation_phase, owner_transition_version
old_lease_id, old_fencing_token, old_holder_session_id, old_holder_process_identity
old_worker_runtime_scope_id, old_execution_scope_exit_receipt_id
new_lease_id, new_fencing_token, new_holder_session_id, new_holder_process_identity
new_worker_runtime_scope_id, old_purpose, new_purpose
predecessor_credential_install_receipt_id, predecessor_detach_receipt_id
expected_operation_count, expected_operation_set_hash
expected_pending_effect_count, expected_last_forwarded_sequence
state: prepared / credential_pending / receipt_ready / activated / quarantined
created_at, activated_at NULLABLE
UNIQUE(continuation_id, owner_transition_version, kind)
UNIQUE(context_generation_id, new_fencing_token)

collection_browser_context_purpose_handoff_member
purpose_handoff_operation_id, submission_operation_id
operation_state, terminal_or_staging_receipt_id NULLABLE, last_forwarded_sequence
UNIQUE(purpose_handoff_operation_id, submission_operation_id)

collection_browser_context_purpose_handoff_receipt
id, purpose_handoff_operation_id UNIQUE
continuation_id, kind, continuation_phase, owner_transition_version
browser_id, browser_boot_id, context_generation_id
old_lease_id, old_fencing_token, old_holder_session_id, old_holder_process_identity
new_lease_id, new_fencing_token, new_holder_session_id, new_holder_process_identity
old_worker_runtime_scope_id, new_worker_runtime_scope_id
predecessor_detach_receipt_id, old_execution_scope_exit_receipt_id
gateway_barrier_event_id UNIQUE, gateway_barrier_ack_hash, last_forwarded_sequence
new_credential_install_receipt_id, operation_count, operation_set_hash
total_pending_effect_count, old_purpose, new_purpose, receipt_hash, verified_at
CHECK(kind/purposes are exactly collector->assist or assist->capture)
CHECK(total_pending_effect_count=0)
```

Prepare事务冻结该lease/reservation下**全部**operation成员和平台policy要求的全部check kind，count/hash分别双向anti-join；漏一条已发送operation、漏一种stream/download/popup检查或混入别owner都失败。事务外cleanup命令携唯一command ID、generation和expected owner/boot/context，通过gateway/supervisor journal执行；receipt逐项绑定实际last-forwarded sequence/barrier。Apply按`browser -> health/policy -> current context -> fence -> cleanup operation/receipt`锁序重新做成员/check双向anti-join，验证所有operation terminal、总pending=0及owner/token未变，再原子把old owned-dirty→clean（或retired并建立new clean generation）并推进release phase。ACK丢失只read-back同operation/command/receipt；错误owner、单纯networkidle、集合遗漏或不同hash只能quarantine。

`owned_dirty_transfer_pending`不是普通可用状态，只为**完全相同 holder session、进程身份、execution scope、gateway connection、browser boot 与 context**的captcha capture→collection直升优化保留。Joint grant事务必须同时创建successor `reserved_unactivated`、把fence/token与continuation推进到effect-none credential pending、冻结predecessor/successor及root terminal/staging的完整成员集合，并创建唯一owner-transfer operation；只有这时context才可从旧reservation的`owned_dirty`进入`owned_dirty_transfer_pending`，且任何普通acquire/begin/attach都拒绝。事务外purpose barrier关闭旧capture credential并取得直接predecessor ACK；专用activation事务按完整normal锁序重新做operation/member双向anti-join，要求root已terminal、capture/staging已完成、pending effect/frame/stream=0、last-forwarded sequence精确闭合、holder/process/scope/connection均未改变，再把context原子转为绑定新lease/token/reservation的`owned_dirty`，同时激活reservation和collection credential。ACK丢失只按同owner-transfer operation、successor reservation和receipt read-back；任何不符、超时或结果不明都把context taint并quarantine，不能退回旧token或把transfer-pending改clean。

Dirty context跨owner默认禁止，只开放三条**穷举白名单边**：`collector -> captcha_assist`、`captcha_assist -> capture_root`、以及完全相同holder/process/scope/connection的`capture_root -> collection`。前两条必须使用上面的purpose-handoff operation/member/receipt：prepare以直接predecessor detach、gateway barrier、旧scope/socket退出、完整operation/pending/sequence集合把context置`owned_dirty_handoff_pending`，新credential此时effect none；activate在§9.5完整hard-gate锁序下验证matching install/capability receipt，才原子把context重绑新lease/token/holder并回`owned_dirty`。它们不分配quota，只开放精确assist或capture purpose，永远不能调用`begin_submission()`。第三条只走前述same-holder owner-transfer operation。任一phase、purpose、holder、boot/context、member、pending count或sequence证据不符都taint/quarantine；任意第四种跨holder边、普通collection/new grant跨holder、以及用nullable/通用“handoff”冒充白名单的计数必须为0。

要从purpose-limited captcha路径回到collection，前任必须仍以有效cleanup-only capability完成durable cleanup，使context先成为`clean`，再detach/barrier并交给新holder；若前任已detach、失联或无法完成cleanup，只能taint→quarantine→physical recovery。Pre-submit captcha在assist结束后必须先由assist holder完成session恢复所需页面动作，再做context cleanup→clean，之后才detach和session verifier；post-submit capture在需要释放holder时，必须先完成root capture/staging与context cleanup→clean，再做capture detach。这样跨holder的新collection grant永远走普通clean activation，只有同holder capture→collection直升才走明确的owner-transfer activation，不能让实现者用一条宽松“handoff特批”同时覆盖两种安全边界。

### 6.10 管理状态不是第二事实源

`CollectionPlatformAccount.runtime_state/current_run_pub_id` 和 `CollectionBrowser.activity` 目前容易变成陈旧缓存。目标定义：

- active reservation 是账号 busy/running 的事实源；
- held_unactivated/held/revoking/quarantined/recovering fence 是浏览器不可供普通新grant的事实源；
- current browser boot、current clean context generation和matching readiness/recovery receipt是浏览器可用性的事实源；`CollectionBrowser.error_streak/activity`不再参与v1 admission；
- 平台账号级muted/quota wall/rate-limit/hard-error/mode block属于external identity/quota subject；仅session/captcha/browser污染属于binding revision/browser；account header上的同名列只能做兼容投影；
- API 可以为兼容继续投影 `running/current_run`，但投影必须在 reservation 同事务更新并有 reconciliation；
- 不得只凭一个可能陈旧的 `runtime_state='idle'`、`browser.activity='idle'`或裸`browser.error_streak`做admission。

为保留真正的browser-local健康信号而不污染subject治理，建立独立单调ledger：

```text
collection_browser_health_projection
browser_id PRIMARY KEY
current_browser_boot_id, current_context_generation_id
state: ready / suspect / quarantined / recovering
consecutive_infra_failures, projection_version
gate_version, gate_epoch
current_health_policy_id, current_health_policy_version
current_health_policy_contract_hash
last_health_effect_id, current_readiness_receipt_id NULLABLE
updated_at

collection_browser_health_policy
id, platform, browser_profile, policy_version
state: draft / verified / active / retired
infra_failure_threshold, readiness_validity_kind, readiness_ttl_ms NULLABLE
event_classification_set_hash, transition_action_set_hash, contract_hash
verified_by, verified_at, evidence_hash
UNIQUE(platform, browser_profile, policy_version)
partial UNIQUE(platform, browser_profile) WHERE state='active'

collection_browser_health_event_classification
id, health_policy_id, event_kind, evidence_class
action: no_effect / record_success / record_infra_failure /
        mark_suspect / quarantine / recover_ready
action_contract_hash
UNIQUE(health_policy_id, event_kind, evidence_class)

collection_browser_health_control_operation
id, operation_key UNIQUE
kind: migration_baseline / admin_health / reconciliation
browser_id, expected_boot_id, expected_context_generation_id
expected_gate_version, expected_projection_version
health_policy_id, target_action, evidence_hash
state, claim_token, claim_generation, claim_expires_at, applied_at NULLABLE
UNIQUE(id, kind)

collection_browser_readiness_probe_attempt
id, attempt_key UNIQUE, browser_id
browser_boot_id, context_generation_id, browser_recovery_operation_id
health_policy_id, health_policy_version, policy_contract_hash
expected_gateway_or_raw_isolation_receipt_id, expected_supervisor_boot_receipt_id
state: prepared / claimed / completed / timed_out
claim_token, claim_generation, claim_expires_at, claimant_boot_id
created_at, terminal_receipt_id NULLABLE

collection_browser_readiness_probe_receipt
id, attempt_id, claim_generation, claim_token_hash, claimant_boot_id, browser_id
browser_boot_id, context_generation_id, browser_recovery_operation_id
health_policy_id, health_policy_version, policy_contract_hash
actual_gateway_or_raw_isolation_receipt_id, actual_supervisor_boot_receipt_id
result: ready / not_ready / contract_rejected / stale_claim_noop / expired_claim_noop / timed_out
is_attempt_terminal_winner
probe_manifest_hash, evidence_hash, measured_at
validity_kind: until_boot_or_context_change / time_bounded
fresh_until NULLABLE
applied_health_effect_id NULLABLE
UNIQUE(attempt_id, claim_generation)
partial UNIQUE(attempt_id) WHERE is_attempt_terminal_winner
CHECK(time_bounded iff fresh_until IS NOT NULL)
CHECK(stale/expired claim noop implies not terminal winner and no health effect)

collection_browser_health_effect
id, source_kind, source_event_key UNIQUE, source_generation, browser_id
browser_boot_id, context_generation_id
terminal_submission_operation_id NULLABLE
browser_health_control_operation_id NULLABLE
browser_recovery_operation_id NULLABLE
event_class: verified_ready / browser_infra_failure / tainted / audit_only
action: no_effect / record_success / record_infra_failure / mark_suspect / quarantine / recover_ready
old/new state, old/new consecutive_infra_failures
old/new projection_version
old/new gate_version, old/new gate_epoch
health_policy_id, health_policy_version, policy_contract_hash, action_contract_hash
evidence_hash, browser_readiness_probe_receipt_id NULLABLE, applied_at
UNIQUE(terminal_submission_operation_id, source_generation, event_class)
  WHERE source_kind='terminal_submission'
UNIQUE(browser_health_control_operation_id, source_generation)
  WHERE source_kind IN ('migration_baseline','admin_health','reconciliation')
UNIQUE(browser_recovery_operation_id, source_generation)
  WHERE source_kind='recovery_ready'
CHECK(exactly one source FK is non-NULL and it matches source_kind)
CHECK(audit_only implies state/streak/projection/gate delta all zero)
FK/CHECK(action, health_policy_id, action_contract_hash) matches registered classification action
```

Browser health policy选择必须是exact `(platform, browser_profile)`且无fallback，缺/多active行fail-closed。受控activate按稳定platform/profile锁序验证classification/action集合hash，在同一事务retire old、activate new并写receipt；并发activate由partial unique和expected old version收敛。Grant共享锁唯一active policy并冻结ID/version/contract/classification/action hash；policy切换必须推进对应browser health gate version/epoch，尚未dispatch的旧reservation失效，已经dispatch的terminal仍按operation冻结policy分类。

`source_kind`只能取`terminal_submission/migration_baseline/admin_health/reconciliation/recovery_ready`，并分别以FK指向真实submission operation、typed health control operation或browser recovery operation，恰一source FK非NULL；migration 41 baseline不伪造submission，recovery也不能拿任意admin operation冒充。只有能定位到matching browser boot/context的transport crash、CDP disconnect、corrupt context、gateway/fence violation等browser-local证据才能`record_infra_failure`；平台wall/refusal、subject/session gate、答案质量、pause/cancel/deadline、never-granted、neutral suffix、Task/materialization故障都写audit-only或不写browser health effect，不能增加infra streak。Effect按typed source key唯一、冻结health policy/classification并old/new CAS。

Browser streak与授权gate必须分层：verified success或低于阈值的infra failure只互斥更新连续streak和`projection_version`，不改变`gate_version/gate_epoch`，所以多题成功和第1..N-1个允许失败不会让同batch后缀暗中regrant。`new_infra_failures = old + 1`且`new >= infra_failure_threshold`时，同一effect才把ready→suspect/quarantined并推进gate version/epoch；如果策略要求一次即停，就显式把threshold设为1。任何state、boot、context、readiness receipt或health policy改变都推进gate version/epoch；block→recover也再次推进，旧reservation不能ABA。Verified success可以按policy把failure streak清0，但不能单独把suspect/quarantined恢复ready。

唯一恢复矩阵必须写死：matching physical recovery + terminal-winner ready receipt产生的`recover_ready` effect，在同一CAS把`state recovering/quarantined -> ready`、`consecutive_infra_failures -> 0`、`projection_version + 1`、`gate_version/gate_epoch + 1`，并切换current boot/context/readiness receipt；effect完整保存old/new值以便重建。历史`error_streak=41`因此先迁移为quarantined/41，再经真实恢复变为ready/0；这不是普通verified success自动解门。恢复后的下一次第1至`threshold-1`个infra failure从0重新计数且不关gate，第threshold个才关；若恢复没有把streak归0、只改state或只改legacy投影，验收直接失败。

任何health effect把state推进到非ready时，必须在同一`browser -> health/policy -> current context -> fence -> live continuation`事务撤销normal/captcha effect：对held/owned browser至少递增health gate并把holder capability降为cleanup-only；若原因含unknown/taint/ownership不可信则同时把context置tainted、token+1进入quarantined，并把matching continuation置quarantined或只允许已发送root的有界settlement。不能只把health写suspect却让既有assist/capture/collection credential继续；也不能在持fence后反锁subject。Terminal路径需要同时更新subject时使用§7.5完整subject-first锁序，browser-local admin/recovery只走browser→health/policy→current context→fence→continuation。

Readiness probe不能由将受益的普通worker自报。Privileged、attested prober先prepare/claim attempt，claim generation单调且token/claimant boot冻结，HTTP/CDP/OS probe在DB锁外执行。Completion先无锁解析immutable target，再按`browser -> health/exact policy -> current context -> readiness attempt -> claim -> receipt`比较current、未过期claim及expected browser/recovery/boot/context、gateway/raw isolation和supervisor boot evidence；只有current valid claim可CAS `terminal_receipt_id`成为每attempt唯一terminal winner，`ready/not_ready/contract_rejected`都关闭attempt。Claim g1过期被g2接管后，g1迟到只能以`(attempt,g1)`写非winner`stale_claim_noop/expired_claim_noop`（或进入独立audit流），不能占terminal winner、改变health或阻断g2。Timeout reconciler先在锁内使旧claim失效并取得专用新claim generation，再以同一规则写`timed_out` winner；不能和旧worker共用generation。Receipt/commit ACK丢失只按`(attempt, claim generation)`read-back，同键不同token/evidence/result fail-loud。

`until_boot_or_context_change`只在同boot+context+policy内有效；`time_bounded`还要求DB now早于fresh-until。Receipt的ready本身不开放浏览器，只有matching recovery apply函数可引用**terminal-winner ready receipt**写`recover_ready`；stale/nonwinner/contract-rejected receipt永远不能被当前projection引用。

`recover_ready`只能由§9 physical recovery完成后写：必须引用matching旧writer隔离、restart或可信context reset、新boot/context、raw-port/gateway sync、supervisor reconciliation和上述主动readiness probe receipt，并与fence recovery同一最终受控事务提交。Grant/activation/heartbeat/begin-submission冻结并比较browser-health **gate** version/epoch、policy、boot、context generation和readiness receipt（不比较普通projection version）；任何gate漂移或time-bounded receipt到期使未发送item停止。V1所有generic/manual/scheduler入口使用同一门，不能绕过。

Expand迁移必须专门处理历史`CollectionBrowser.error_streak`：任意非零、NULL/负值异常、来源无法与唯一事件对账，或已知豆包北京`41`这类高值，均不能直接清零或继续当永久block；先写migration baseline health effect，把对应fence置quarantined、context置tainted/unknown并阻断grant。只有完成旧holder隔离、restart/reset、新boot+clean context+probe receipt后才以`recover_ready`开放。之后旧`error_streak/activity`仅由projector从health/fence真源做带version的**绝对SET**供兼容UI，contract撤销所有旧`+=1` writer和所有admission读取；projection漂移只告警/修复，不能影响授权。

账号换绑、地域修改、quota 下调必须锁 account，并在存在 live reservation/fence 时拒绝或进入显式 drain 流程。不得在运行中悄悄改变 grant 的账号归属。

不能把数据库中的`platform_account_id`本身当成“平台真实账号”身份，否则把同一个豆包账号复制/撤销/重建成两行，就能获得两套quota bucket。建立不可删除、全局规范化的quota subject：

```text
collection_external_platform_identity
id, platform
stable_platform_subject_hash
state: pending / verified / conflicted / retired
verified_platform_subject_id_ciphertext NULLABLE
evidence_hash, verified_at, verified_by
created_at, retired_at NULLABLE
UNIQUE(platform, stable_platform_subject_hash)
UNIQUE(id, platform)

collection_external_platform_identity_alias
id, external_identity_id, platform
identity_scheme, canonicalizer_version, canonical_alias_hash
evidence_hash, verified_at, verified_by, retired_at NULLABLE
UNIQUE(platform, identity_scheme, canonicalizer_version, canonical_alias_hash)
UNIQUE(external_identity_id, identity_scheme, canonicalizer_version, canonical_alias_hash)

CollectionPlatformAccount                         # 稳定管理header
id, platform
lifecycle_state: draft / active / rebind_pending / draining / conflicted / revoked
current_binding_revision_id NULLABLE, current_binding_version NULLABLE
version, created_at, updated_at
UNIQUE(id, platform)
UNIQUE(id, current_binding_revision_id, current_binding_version)

collection_platform_account_binding_revision      # snapshot字段append-only
id, account_id, binding_version, platform
external_identity_id, resident_browser_id, region_id
stable_platform_subject_hash, identity_alias_id
identity_scheme, canonicalizer_version, canonical_alias_hash
revision_state: prepared / verified / draining / retired / conflicted
identity_evidence_hash, browser_evidence_hash, session_evidence_hash
verified_at NULLABLE, verified_by NULLABLE, retired_at NULLABLE
predecessor_revision_id NULLABLE, rebind_operation_id NULLABLE
UNIQUE(account_id, binding_version)
UNIQUE(account_id, id, binding_version)
UNIQUE(id, account_id, binding_version, platform, external_identity_id,
       resident_browser_id, region_id, stable_platform_subject_hash,
       identity_alias_id, identity_scheme, canonicalizer_version,
       canonical_alias_hash)

collection_platform_account_rebind_operation
id, operation_key UNIQUE, account_id
source_binding_revision_id NULLABLE, target_binding_revision_id
expected_account_version, expected_source_binding_version
state: prepared / draining / ready_for_cutover / completed / aborted / blocked
target_external_identity_id, target_resident_browser_id, target_region_id
claim_token, claim_generation, claim_expires_at
reason, evidence_set_hash, created_at, completed_at NULLABLE
UNIQUE(account_id) WHERE state IN ('prepared','draining','ready_for_cutover')
UNIQUE(target_external_identity_id) WHERE state IN ('prepared','draining','ready_for_cutover')
UNIQUE(target_resident_browser_id) WHERE state IN ('prepared','draining','ready_for_cutover')
```

Canonical identity必须来自平台会话可验证的稳定subject/account ID；手机号、cookie文件名、展示昵称、环境变量名或browser instance key本身都不能单独充当canonical identity。Subject header的`stable_platform_subject_hash`只对`platform + 官方稳定subject ID`做domain-separated规范hash，**不把scheme/canonicalizer放进唯一域**；因此同一官方subject的手机号alias、旧/新canonicalizer或多种证据scheme最终都必须指向同一header。Scheme/canonicalizer只存在append-only alias/evidence表并进入binding/grant审计合同。无法取得官方稳定subject、或两个alias是否同subject不确定时只能pending/conflicted，不能verified或创建quota。原值按敏感信息规则加密/最小化保存。

数据库contract约束必须写到DDL测试，不得只写“必要unique”：

- identity registry对`(platform, stable_platform_subject_hash)`做**全局永久唯一**；同一外部平台账号无论alias scheme/canonicalizer、tenant、region或browser如何变化都仍指向同一个`external_identity_id`。Verified函数从平台identity policy推导唯一authoritative subject extractor，调用方不能自选scheme把同一账号拆开；它按stable hash及全部alias hash排序取得advisory locks，`INSERT ... ON CONFLICT`后逐项核对subject/evidence。若业务将来允许多个tenant共享，表示为一个subject的多条授权membership，quota ledger仍只挂subject，不能复制subject/account；
- verified binding revision要求`external_identity_id/resident_browser_id/region_id/binding_version/stable subject hash/所用alias revision/三类evidence/verified_at/verified_by`全部NOT NULL，identity本身也必须`state=verified`且platform/stable hash一致，alias必须反向属于该subject；用deferred constraint trigger或受控verify函数校验跨表状态；
- account header通过`(account_id, current_binding_revision_id, current_binding_version)`同account复合FK指向自己的current revision。`lifecycle_state=active`当且仅当current revision为verified；draft/conflicted/revoked无可调度current，rebind_pending/draining即使仍指旧revision也使新grant=0。Header不能跨account指revision，也不能只改version数字；
- binding revision的identity/browser/region/canonical字段及predecessor一经插入不可更新；只允许受控状态机`prepared -> verified -> draining -> retired`或`prepared -> conflicted/retired`推进lifecycle字段。历史grant永远FK到原revision，所以old revision退休不会破坏历史FK，current pointer切换也不会改写旧事实；
- `UNIQUE(external_identity_id) WHERE revision_state IN ('verified','draining')`确保同一真实账号的当前/排空revision至多一条；`UNIQUE(resident_browser_id) WHERE revision_state IN ('verified','draining')`确保一个resident browser在可写或排空期间只属于一个正式账号。Prepared target由每account唯一live rebind operation及target-browser partial unique占位，cutover时仍在锁内重查subject/browser唯一性；Region不进入这些唯一域：换地域是同account的新revision，不是复制账号；
- rebind分三阶段且无中间授权窗口：第一事务插入`prepared` target revision+rebind operation并把header置`rebind_pending`，普通grant立即为0；第二阶段把old current置`draining`并等待/撤销到live reservation、permit和fence effect=0；最终cutover事务按全局锁序同时把old`draining -> retired`、target`prepared -> verified`、header current composite FK/version→target、header→active并写唯一completion receipt。任一commit前崩溃只留下非active header或old draining，grant=0；cutover commit ACK丢失按operation key/current pointer read-back，不得创建另一revision；
- revoke同样只推进header/revision状态，不删除snapshot或external identity。若新管理account以后复用同一subject，仍引用原identity/quota ledger；若未来确需一个browser承载多个账号，必须先设计显式context-level identity、credential隔离和quota subject映射，不允许加一个`allow_shared=true`绕过unique；
- Grant header冻结`binding_revision_id`并以composite FK引用`account_id + binding_revision_id + binding_version + platform + external_identity_id/quota_subject_id + stable subject hash + identity alias/scheme/canonicalizer/hash + resident_browser_id + region_id`。Grant最终门还要求header current pointer仍等于该revision、account active、revision verified；scheduler提示只是早期反馈。豆包与其他平台使用同一规则，不允许adapter特例回旧路径。
- 每个verified binding revision还必须存在matching current `verified` session revision；verify/cutover函数在同一事务核对session evidence和复合pointer。Rebind target没有verified session row/evidence时不能cutover为active；cutover不继承old revision的session状态。Old revision进入retired时同步追加retired session revision并递增gate epoch，历史grant保留FK但只能cleanup。Grant header同时冻结session revision/version/gate epoch/evidence/expiry；begin-submission要求binding和session两个current pointer均未变化。

所有quota policy/scope/bucket的授权主体必须是稳定的`external_identity_id`（下文记作`quota_subject_id`），`platform_account_id`只可作为当次binding审计快照。撤销binding、换browser、换region或新建管理行都不能建立新quota余额。若历史同一canonical identity已有多条账号行：migration不得任选一个winner、删除/合并审计或把计数清零；先把整个duplicate group置`conflicted`并令grant=0，停止live effect后按immutable operation key去重既有effect，无法证明不重叠的当前exposure取保守上界/合计并标baseline unverified，由人工核准唯一subject和唯一verified binding。只有duplicate anti-join=0、subject ledger守恒且partial unique可VALIDATE后才允许该账号canary。

## 7. 精确事务协议

### 7.1 Execution 域锁顺序

所有模块统一下面的逻辑顺序；不需要的行跳过，但不得反向获取：

```text
0. 单一request/operation幂等key advisory xact lock；termination入口必须使用全局`termination_ingress.ingress_key`命名空间并先read-back/compare，不能分别锁root alias与post-terminal key
1. collection_dispatch_control
1a. collection_launch_series/current pending attempt（仅自动launch producer；按series key/ID，run-now跳过）
2. collection_execution_protocol_assignment（以及只读/锁定的run-start identity，按run ID；普通grant通常只读compare）
3. active collection_workflow_chain_generation（按assignment/generation；effectful路径FOR SHARE/条件锁并要求active）
3a. assignment terminal receipt/pointer（absence由已锁住的assignment父行保护；normal路径要求pointer为空，terminalizer/request/watcher都在这里线性化“正常终态先赢”或“termination root先赢”）
3b. assignment级live termination-request root/header gate（含hard/user-cancel/deadline/policy；这是授权gate而非operation-final work row。Normal路径要求不存在；存在即停止，不继续取后序资源锁）
4. collection_worker_runtime_scope（按稳定scope key；normal effect要求open/current boot/epoch匹配）
5. collection_region
5a. collection_launch_candidate_pool/current revision（仅launch mint或候选池管理；按platform/region/mode、pool ID）
5b. collection_external_platform_identity / quota subject（按platform、identity ID；缺行claim时先取规范identity-hash advisory xact lock）
6. collection_platform_account
6a. current binding-session header/revision（按binding revision；admission通常FOR SHARE）
7. quota config gate、subject global/mode governance gate、exact active governance transition policy与active quota policy/stable scopes/policy-scope revisions，按subject、platform/mode、policy/scope ID（admission通常FOR SHARE）
8. collection_browser -> collection_browser_health_projection/exact policy -> current browser_context_generation（同browser；多context按generation ID）
9. quota buckets，固定 policy registry 的 `scope_kind_rank`（account/global=0，mode=1，其余显式枚举）-> quota_scope ID -> period(day=0，week=1，year=2) -> bucket ID；不得依赖自由字符串/locale 排序
10. browser_fence
11. collection_execution_request（事务需要修改 request projection 时）
12. reservation header
13. request items，按 ordinal升序
14. submission operations，按 ordinal/generation升序
15. captcha continuation/generic resume waiter，按类型、ID升序
16. reservation items，按 ordinal升序
17. capture attempts，按 generation升序
18. capture staging/result projection，按 operation、generation、result kind升序
19. quota effect group、submission/capture receipt、append-only event
20. durable work operation/claim 与 outbox（workflow-start intent、termination work claim、`collection_dispatch_control_operation`、`browser_recovery_operation`、worker-scope recovery、release/closure operation、governance outbox），只在最终 claim-token/phase CAS 时加锁；step 3b termination-request header不在此列
```

`CollectionRun/CollectionTask` 属于另一个锁域。实施时必须审计 `persist_collection_result()` 现有 `CollectionRun FOR UPDATE`，并遵守：

- execution/grant/settlement 路径永远不锁 `CollectionRun`；
- task persist保持 `CollectionRun -> CollectionTask -> result revision`，只通过确定性key关联既有submission operation/可选reservation item及选中的capture attempt/verified staging/hash并插入幂等command；
- task persist 不反向修改 account/quota bucket/fence；
- 如果关联 item 需要 `UPDATE`，确保没有任何 item 路径随后再锁 run，并用并发测试证明无环。

不要直接把 account 锁追加在 run 锁后又让其他路径 account→run。推荐让 quota/submission ledger 在 adapter 侧事务成为发送事实源；task persist 只负责客户任务和关联。

规则：

- 普通effect/grant/settlement事务只处理一个request/account/instance。两类显式多资源例外是admin rebind的受控短事务，以及fresh v1 launch-evaluation/mint短事务；后者必须锁完整required-member候选集合并按全局ID排序，但只能读取/冻结eligibility并创建配置事实，**不得**取得fence、预占quota、写submission/effect、执行外部I/O或等待schedule锁。Candidate pool必须有配置上限并监控事务/锁等待；超限fail-closed拆成独立launch plan，不能截断候选集；
- 批量处理按 account ID、instance key、item ordinal 排序；
- sweeper 先只读选 candidate ID，再按上述顺序逐个短事务；
- reaper 若需要同时结算 quota/reservation 并处置 fence，先完成 account/bucket/reservation 结算并 commit，再用独立 fence-only CAS 事务 quarantine/release；不得跨域反向持锁；
- 使用 `lock_timeout` 和有界 retry；
- 不使用无限阻塞的 advisory lock；同 request 每事务只取一个 try/advisory lock；
- 使用 DB 时间，不使用 worker 本地时间决定 lease/bucket 生死；
- 如 Session 之前读过对象，锁定读取必须 `populate_existing` 或改用显式 SQL 返回最新值。
- 任何同时触及 reservation 与 fence 的事务都必须 fence 在前，禁止 `reservation -> fence`；
- 同时触及quota policy/scope和browser的事务固定为`external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> exact governance transition policy -> active quota policy/stable scopes/policy-scope revisions -> browser -> browser health/exact policy -> current context -> buckets -> fence`；不得在某条captcha/recovery/admin路径改成browser→policy。只改quota policy的admin事务可以跳过binding-session/browser，但仍保持subject→account→quota config gate→governance→policy/stable scope→bucket→durable operation-final；会话gate flip固定subject→account→binding session，不能反向先锁session。Browser-local health/recovery固定browser→health/policy→current context→fence，不能先锁fence再补health/context；
- Launch plan freeze、primary partition/replacement这类**不创建Run**的sampling控制事务仍只使用各自`campaign -> formal legs -> slots/revisions -> origin intent -> operation final`锁域。任何真正mint fresh v1 Run/role/assignment/start operation/outbox的scheduler、run-now、API、CLI或reconciler则只能调用统一launch-evaluation函数，并固定为`series/launch-key advisory -> control -> launch series/current pending attempt(自动producer) -> required regions去重 -> candidate-pool headers/current revisions/members -> 全部候选external identities/quota subjects去重 -> accounts -> binding sessions -> quota config/global+mode governance/exact policies/stable scopes -> browsers -> health/exact browser policy/current contexts -> current buckets -> campaign -> formal legs -> primary slots/current revisions -> run-origin intent -> launch attempt/evaluation final -> Run -> run-leg assignments -> protocol assignment/items -> gen0/start operation/outbox`；各集合跨member全局去重、稳定排序，锁后重读完整candidate/member/scope/bucket快照，并在同一短事务按冻结selection policy计算winner/role而非接收caller role。禁止保留旧的`control -> campaign -> ... -> Run`mint函数后再补锁region/account/browser/bucket，也不得先插Run后靠Activity或竞态补eligibility/primary。Start consumer先用claim-only事务领取outbox并提交，apply事务才按`control -> protocol assignment -> gen0 -> start operation/outbox CAS`复核冻结结果；不得先锁assignment/start operation再取control，也不得跨Temporal RPC持任何DB锁。Temporal start在事务外，随后用read-back receipt短事务完成最终CAS。Enforce同样control→assignment/inventory。Sampling projector仍按campaign→leg→cell，不会在持cell时反向锁slot/Run/Task；slot replacement也不锁cell。
- normal heartbeat使用唯一完整锁序`control -> assignment -> chain generation -> hard-request gate -> worker runtime scope -> region(需要复核时) -> external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> exact governance transition policy -> active quota policy/stable scopes/policy-scope revisions -> browser -> browser health/exact policy -> current context -> buckets(需要续expiry时) -> fence -> reservation`，复核全部冻结gate/deadline后才续normal。不得保留旧的browser→fence后再反锁subject/account的短路径。Chain closing、hard request或任一scope/session/governance/health/context gate失效时，只能在不随后取得任何被跳过前序锁的独立cleanup事务中，凭冻结operation/permit事实给有界cleanup heartbeat；禁止新effect。无quota captcha owner transfer使用`control -> protocol assignment -> active chain generation -> hard-request gate -> worker runtime scope -> collection_browser -> browser health/exact policy -> current context -> fence -> request/continuation`，只获取assist/capture cleanup capability；它不得在同事务回头锁subject/session，session verifier必须另起下述subject-first事务；
- quota settlement使用`external identity/quota subject -> account -> buckets -> request(如需) -> reservation -> request item -> submission operation -> reservation item -> receipt`，不锁fence；
- governance consumer先按claim token只读确定target，再用`external identity/quota subject -> account -> binding session(如需) -> subject governance -> browser(如需) -> 所有历史/当前buckets固定序 -> delivery receipt -> outbox delivered CAS`；它只read-back terminal事务已提交的authoritative gate effect并做非critical投影/通知，不再改变critical gate/streak；不得先锁outbox/receipt后再取subject/account/bucket；
- task-success consumer不锁Run/Task/revision，只消费Task事务已经冻结的immutable command，按前一条account/browser/bucket顺序执行；task selection与success apply并发靠`task_id/request_item_id`唯一receipt收敛，不建立Task→account锁边；
- execution item上的task_pub_id兼容projection与Task事务分开：先释放Run/Task锁，再在独立事务只锁item；任何持item/account/bucket的路径不得随后取得Run/Task锁；
- 任何同时修改`CollectionBrowser`、health/context与`BrowserFence`的admin/recovery事务使用browser→health/exact policy→current context→fence，systemctl/CDP/restart在事务外通过outbox分阶段执行。
- shared-worker scope recovery固定为`control(仅global升级时) -> worker runtime scope -> 全部受影响browser按ID -> 全部health/context按browser/context排序 -> 全部fence按instance key -> scope/member/release-blocker operation`；grant对同scope取共享/条件锁。Scope gate、blast-set与基于immutable owner snapshot的release-blocker冻结必须同commit，禁止“先扫描holder，再停unit”；blocker在此事务不通过FK/trigger锁workflow/Run。Supervisor物理resource key另按`node -> worker unit -> browser unit -> instance`排序，不能与DB锁混在一个长事务中。Physical-isolation receipt consumer先无锁解析immutable集合，再按`runtime scope -> browsers -> health/context -> fences -> scope operation/members -> receipt`验证并插入；它不得锁workflow closing或等待业务settlement。Hard closure只读matching receipt推进physical axis。Logical release另起事务按`runtime scope -> browser -> health/context -> fence -> member/blockers -> child operation`验证所有blocker terminal后free；不能把physical seal和free塞回同一事务。
- hard-termination request prepare先无锁解析start-operation/资源ID，再固定为`control -> assignment -> gen0或actual chain及existing closing/intent/transfer -> assignment terminal receipt(step 3a) -> termination request header(step 3b) -> 该run当前worker runtime scopes按scope key -> browsers -> health/current-context -> fences按instance -> execution requests/items/submission operations按稳定ID -> expected target manifests/可选new hard closing -> termination work claim与start operation final expected-phase CAS`，只冻结expected set和关闭assignment级终止门，不在事务内执行RPC、gateway或OS I/O，也不锁account/bucket做settlement。Assignment锁是terminal/root absence的predicate fence：terminal receipt先存在时只写existing-root alias或rootless post-terminal intent并立即返回，绝不能继续锁资源；只有terminal pointer为空才可插入/读取request header，再取得资源。已有request的retry也必须先锁同一header，绝不能先item后request。Gen0 intent按start dispatch证据进入no-run closure或waiting-start-resolution；gen0 bootstrap_pending/active时同commit建立hard owner；已有cooperative owner时只写request，不改owner。Successor bootstrap、workflow abort-to-hard transfer也先`control -> assignment -> chain generations/existing closing/intent/transfer -> assignment terminal receipt -> termination request header -> new hard closing/receipt/target -> work claim final CAS`，与prepare共用前缀；不能先锁request/claim再反向锁assignment/chain。Request target manifest保存已验证的immutable snapshot，不建立会让bootstrap从chain域反向取得browser/request父行锁的FK trigger；adopt只做manifest count/hash复制与contract compare，持续invariant离线join核对。Watcher对bootstrap_pending无owner路径或cooperative supersede都使用相同assignment→chain→terminal receipt→request前缀；前者在terminal evidence完整时直接建watcher owner，后者才是old superseded→new watcher owner，两者均同commit冻结sets和递增epoch。后续每个item由同样先读assignment/chain/terminal receipt/request gate的普通settlement锁序独立结算，physical target由gateway/supervisor operation独立处理；最终consumer先无锁解析immutable sets，再按`assignment -> chain/closing -> assignment terminal receipt -> termination request -> work claim final CAS`验证receipt并完成，禁止把所有域锁进一个巨型事务。

所有 durable operation/outbox consumer 使用统一“两阶段 claim/apply”，防止先锁 work row 再反向等待 control/account/browser：

1. claim-only 短事务只锁 operation/outbox 行，写随机 claim token、generation 和 expiry后立即提交；这一事务不得再取得任何业务资源锁；
2. apply 前无锁读取 target ID/snapshot，在新事务中按上表从 control/account/browser 等前序资源开始加锁，最后才锁 operation/outbox，并以 claim token + generation + expected phase CAS 提交；外部 I/O 仍在事务外；
3. claim 过期的旧 worker可以完成只读核对，但不能 ack、推进 phase 或覆盖新 claimant。`dispatch_control_operation`、`browser_recovery_operation`、captcha closure、normal release 与 governance consumer 不得各自发明反向锁序。

稳定 request header/items 的首次创建和 contract compare 由独立的 `ensure_execution_request()` 短事务完成；该事务绝不锁 control/account/browser/bucket/fence。随后 grant 事务可以先只读 request snapshot，但只有在已锁定前置资源后才按上表锁 request 行并分配 generation。禁止在一个既有 request 上先持 request 行锁再等待 account，从而避免 `request -> account` 与 settlement 的 `account -> request` 死锁环。

### 7.1.1 全局 `SECURITY DEFINER` 与数据库权限硬化合同

本设计的正确性不能只靠“应用都调用正确函数”。Primary-slot freeze/replacement、identity verify/rebind、quota policy/bucket、grant/activate/debit/release、chain owner transfer、region observation apply、browser recover/free等路径都依赖受控函数或trigger；任何一个高权限入口被同名对象劫持、被普通角色直接调用内部phase、或在RLS外执行，都能绕过整套协议。因此以下合同覆盖**所有现存及本次新增的`SECURITY DEFINER`函数、procedure、trigger function和它们调用的helper**，不是只覆盖browser recovery。

角色与所有权必须分离：

| 主体                                                 | 必须具备                                                                             | 明确不得具备                                                                                      |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `geo_collection_schema_owner`（建议名）              | `NOLOGIN`；拥有trusted schema、table、type、sequence                                 | `SUPERUSER/BYPASSRLS`；运行时登录；业务API权限                                                    |
| `geo_collection_definer_owner`（建议名）             | `NOLOGIN`；拥有受控函数；只获函数所需的精确table/sequence权限                        | 拥有业务table/schema；`SUPERUSER/BYPASSRLS/CREATEROLE/CREATEDB`；成为任一runtime role成员         |
| migration owner                                      | 仅迁移窗口创建/ALTER对象和ACL，凭据不进入运行时                                      | 被API/worker继承；永久开放登录                                                                    |
| API/worker/scheduler/projector/verifier/recovery角色 | 只获对应trusted schema的必要`USAGE`、公开函数的精确signature `EXECUTE`、必要只读view | 业务table直接`INSERT/UPDATE/DELETE/TRUNCATE`；内部helper `EXECUTE`；互相`SET ROLE`；对象owner身份 |

不要让definer owner同时拥有table；否则PostgreSQL table owner默认可绕过RLS。所有运行时definer owner必须`NOLOGIN NOSUPERUSER NOBYPASSRLS`，且tenant表启用并在适用时`FORCE ROW LEVEL SECURITY`。每个函数仍要显式带`tenant_id/assignment_id/subject_id`谓词、复合FK和expected-version校验，不能把RLS当作唯一授权。普通生产函数固定`row_security=on`；不得在函数体执行`SET row_security=off`，不得调用有`BYPASSRLS`的中间角色。确需跨tenant的离线修复必须是另一个默认拒绝、全局pause下运行、以冻结manifest逐项处理并写审计receipt的专用流程，不能复用在线grant/admin入口。

默认使用`SECURITY INVOKER`。只有必须跨越已撤销表DML权限、且能把一个完整状态转换封装为单事务的窄函数才可使用`SECURITY DEFINER`。每个definer函数定义中必须同时固化：

```sql
SECURITY DEFINER
SET search_path = pg_catalog, geo_collection_private
SET row_security = on
```

`geo_collection_private`只是示例trusted schema，实施时使用项目确定的真实私有schema。它及所有被引用schema都必须`REVOKE CREATE ON SCHEMA ... FROM PUBLIC`并撤销runtime角色的`CREATE`；`search_path`不得包含`$user`、`public`、调用方schema或`pg_temp`。即便固定了`search_path`，函数体仍必须全限定业务table、view、sequence、type、collation、operator及被调用函数；不能依赖解析顺序。不得使用调用方可控的relation/column/function/operator/schema名，不得把字符串拼进SQL，首版所有definer函数**禁止dynamic SQL/`EXECUTE`**。如果未来确有静态枚举无法表达的必要场景，必须另写安全ADR、将允许标识符映射为代码内闭集并独立渗透审查，不能只用`quote_ident()`就放行。

每个公开函数只表达一个capability和固定phase，例如`grant.prepare`、`grant.activate`、`quota.debit`、`binding.cutover`；禁止一个接收`operation_kind/state/role`后任意改表的generic super-function。函数自行从已锁定DB事实推导role、state、subject、bucket set和fence generation；caller值只能做contract compare。所有参数使用精确类型，避免`unknown`/隐式cast/overload解析歧义；外部函数与内部helper分schema，runtime角色没有private helper schema的`USAGE/EXECUTE`。函数不得信任调用方传入的`changed_by/tenant/current role`作为授权依据，审计同时记录可信连接身份、请求主体、幂等键和受影响contract hash。

ACL与默认权限必须在同一migration中原子收口：

1. 从`PUBLIC`撤销数据库/所有业务schema上非必要`CREATE`，从所有函数/procedure撤销默认`PUBLIC EXECUTE`；
2. 对schema owner和definer owner分别设置`ALTER DEFAULT PRIVILEGES ... REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`，防止后续migration新建函数重新开放；
3. 逐signature向窄runtime角色显式`GRANT EXECUTE`，内部helper向runtime grant数必须为0；重载函数每个signature单独审计；
4. contract migration撤销业务table的直接DML/危险列级grant、sequence写权限和旧protocol角色；只读通过限定view或必要列grant提供；
5. 禁止runtime `CREATE/ALTER/DROP FUNCTION`、改变owner、改`proconfig`、创建可见同名operator/cast，禁止其成为owner角色成员；迁移后用catalog快照锁定owner、ACL、`prosecdef`、`proconfig`和函数definition hash；
6. trigger function也应用相同owner/search-path/RLS合同；不得因为“只能被trigger调用”就保留`PUBLIC EXECUTE`或不限定对象。

迁移和CI必须生成机器可读的definer inventory，并让以下anti-join恒为0：业务schema中存在但未登记的definer函数；登记函数owner不是专用`NOLOGIN` owner；缺固定`search_path`或`row_security=on`；引用可写schema/未限定业务对象；`PUBLIC`或错误runtime角色拥有`EXECUTE`；definer owner拥有table或`BYPASSRLS`；runtime拥有业务table DML、private schema `USAGE`或owner membership；函数definition hash与批准manifest不一致。新增definer函数没有manifest、威胁模型、精确GRANT和负测时CI直接失败。

至少执行这些真实PostgreSQL攻击性测试：

- 以API/worker等实际runtime role把session `search_path`设为`attacker,public,pg_catalog`，在自有schema及temporary schema创建与业务table、sequence、helper function、type/operator同名对象；调用每个公开函数后只能访问全限定trusted对象，攻击者对象零读写、零回调；
- 在contract前遗留一个`public`同名恶意对象，再执行migration；migration应安全清点/阻断而不是解析到它，contract后`PUBLIC CREATE`和再次创建同名对象被拒绝；
- 未授权角色直接`EXECUTE`公开函数、已授权角色调用错误capability/internal helper/其他重载signature、直接DML关键table/sequence、修改function owner/config/body、`SET ROLE`到owner均被拒绝；
- 伪造caller提供的tenant/account/subject/role/state、跨tenant ID和错误复合snapshot不能越过显式predicate、RLS、FK或expected-version CAS；同一连接切换tenant上下文后prepared statement也不能读写旧tenant；
- 尝试通过字符串、数组、JSON、排序键和错误类型注入relation/function/operator名，证明没有dynamic SQL路径或隐式overload落入更高权限函数；
- 创建一个未显式GRANT的新测试definer函数，验证默认`PUBLIC EXECUTE`为false；迁移重跑、失败回滚和schema restore后再次运行catalog anti-join，不能留下半套owner/ACL；
- 用所有正式runtime角色枚举`information_schema.role_table_grants/role_routine_grants`及`pg_catalog`实际ACL，证明“能够完成被授权phase”与“无法直写或调用相邻phase”同时成立；不能只用migration文件文本断言。

这套合同是发布门禁。发现任意definer函数未登记、ACL漂移、恶意shadow命中、RLS跨tenant或直接DML成功，必须保持global paused并撤回候选release；不能靠应用路由“不调用那个函数”降级接受。

### 7.2 `acquire_execution_grant(request, expected_account_id, expected_instance_key, holder_session)`

先调用上述 `ensure_execution_request()` 幂等建立 request/request items/submission operations；随后一个完整 grant 事务按以下步骤执行：

1. 校验 request schema、ordered business keys、query hash 和 contract hash。
2. 使用稳定 request key 取得 idempotency advisory xact lock。
3. 只读 request/reservation snapshot 并验证 contract。若已有事实：
   - hash 一致先返回 item/staging/ownership 状态；
   - hash 不一致则报 non-retryable drift。
4. 锁dispatch control；非open属transient deferred，不创建永久rejection。随后按全局序读取/锁定run protocol assignment与actual workflow chain generation并要求chain=active，同时在assignment共享锁保护下确认没有live hard-termination request，再锁实际poller/worker boot指向的runtime scope，要求scope=open、InvocationID/boot与boot receipt一致且poller冻结scope epoch等于当前值。Generation 0逐项比较initial start receipt+bound chain gen0；generation>0比较当前Activity run ID对应的active chain generation及其input/continuation manifest hash，不再拿initial receipt的run ID拒绝合法CAN。两者都比较assignment/control/scope/request contract；不匹配、closing、hard request或scope drain/emergency在任何资源授权前fail-closed/deferred cleanup。
5. 以共享锁读取/复核region：必须derived`effective_state=ok`、DB now早于`effective_fresh_until`且health policy verified，并要求current `last_projection_event_id`指向的immutable health event逐项镜像current投影；冻结`region_id/region_projection_event_id/region_health_epoch/region_applied_projection_generation/region_effective_fresh_until/region_health_policy_version`到reservation，只把复合snapshot FK指向该immutable event，不指向可变region current行。`terminal_attempt_high_watermark`只用于审计，严禁进入reservation、grant或begin-submission授权合同。未纳管地域不得默认放行。多个admission可共享读取同一region，不得用地域行把不同账号/平台采集长期串行；共享锁只与§6.1.2短暂的probe receipt/manual override apply事务线性化，任何HTTP probe都在锁外。Begin-submission要求current health epoch/policy相等、effective仍ok、`current applied projection >= frozen`、current freshness不早于冻结且DB now未过期；不以generation相等误杀benign refresh，generation回退则fail-closed。Stale observation/claim、blocked diagnostic或乱序probe completion不能更新这些门禁。
6. 只读预选阶段已经给出expected account/instance和quota subject。事务只允许授权这组资源；先按全局顺序共享锁`collection_external_platform_identity/quota subject`并要求verified。此步只从预选参数/identity binding索引解析expected account ID并保存compare snapshot，**不得取得account行的`FOR SHARE`或其他会在下一步升级的锁**；正式字段在首次排他锁后重读。
7. 以`FOR UPDATE NOWAIT`或统一的短`lock_timeout`作为expected account的**第一次且唯一一次行锁**；若被占用返回明确retryable busy，不把`SKIP LOCKED`产生的“没行”误报为不存在。锁后从行内重读并验证ID等于预选值、account lifecycle=active，锁/复核header current pointer指向的verified binding revision及其external identity/resident browser/region/mode composite snapshot，且subject/browser两个verified/draining partial unique仍成立；任何预选漂移返回retryable reselect，调用方释放local lock后重选。随后共享锁该binding的session header和current append-only revision，要求pointer一致、state=`verified`、evidence完整且DB now未超过verified-until；缺session row一律fail-closed。Grant冻结binding revision以及session revision/version/gate epoch/evidence/expiry，不引用可变header字段替代snapshot。禁止“两个事务都先持account共享锁，再升级`FOR UPDATE`”的实现，`SKIP LOCKED`也不能修复已经持有的共享锁升级死锁。
8. 在identity/account/session锁后，先共享锁该subject的quota config gate，要求state=`open`、current policy指向唯一active verified quota policy，并冻结gate epoch/state/current policy；再共享锁subject global与本canonical mode governance row，要求两者gate state均`enabled`且没有待恢复block，冻结各自gate version/epoch/state/evidence hash。`block -> recover -> enabled`也必须两次推进gate version/epoch，所以旧grant不能通过ABA。Success/failure统计等非授权projection只推进projection version；只要没有改变gate/state/evidence就不撤销同一batch已授权的下一题。随后按exact `(platform, canonical mode)`选择并共享锁唯一active governance transition policy，冻结ID/version/classification/action-set hash，缺/多行不授权；再读取gate指向的active quota policy、本mode requirement所需稳定scopes及对应policy-scope revisions，要求每个scope `grant_state=open`并冻结其epoch集合hash。由platform requirement生成subject全局、当前mode等expected scope，双向anti-join确认零缺失/零多余；随后依次锁browser、health projection/exact policy和`CollectionBrowser.current_context_generation_id`指向的权威context行，要求health=`ready`、boot/context指针双向匹配、context=`clean`且matching readiness receipt在其validity合同下有效，冻结health gate version/epoch、policy ID/version/hash、context和receipt/expiry；不冻结普通projection version。任何context writer也必须先锁同一browser+health，禁止绕过health直接改context。最后锁全部当前day/week/year stable-scope bucket，要求baseline verified、blocker为空，冻结block version并计算：

   ```text
   available_scope = quota_limit - debited_units - reserved_units
   grant = min(requested, all finite available_scope, configured segment cap)
   ```

9. grant 为 0 时记录 transient attempt reason；当前 bucket reset/配置变化后同 request 可重评。不要偷偷 env fallback。
10. 以 NOWAIT/短 `lock_timeout` 锁 expected browser_fence，不能持有 account/bucket 锁长等 heartbeat。若held_unactivated、held、revoking、quarantined、recovering或boot identity不可信，返回retryable busy/reselect；精确匹配的captcha resume handoff走专用adopt分支。
11. 锁 request 行并重新验证步骤 3 的 snapshot；若有 current/predecessor reservation，先锁其 header，再按 ordinal 锁目标 request item/submission operation，确认 operation 仍 `not_started`，再分配下一 `grant_generation`。
12. 原子取得 fence：生成 acquisition `lease_id`、token+1、holder session、短 TTL、purpose=collection；fence 进入 `held_unactivated`且effect permission=none。
13. 创建 `reserved_unactivated` reservation header/items/bucket-set links，冻结binding-session和subject global/mode gate的完整snapshot、policy version、expected scope count/hash；再次 anti-join确认 link恰等于 expected scope×period 后，全部 bucket 的 reserved 各增加 grant。Commit后在事务外让gateway安装绑定新lease/token/boot/holder/collection purpose的credential，install receipt必须引用匹配的clean release/recovery seal predecessor。只有reservation+fence activation CAS成功后才能attach。
14. reservation 保存相同 lease/token/boot ID，账号 running projection 同事务更新。
15. 写 append-only audit event，commit。

步骤 3 不能简单“查到就 return”。恢复决策表必须固定：

| 已有事实                                                          | 当前 caller 动作                                                                                |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| request completed，terminal manifests 完整                        | 原样恢复并返回，不再授权                                                                        |
| live reservation，same holder 且尚未/已经 activation              | 按 activation/read-back 规则恢复同一物理 acquisition                                            |
| live reservation，different holder                                | 只恢复 item/staging；active lease 不可 adopt，进入等待或物理 fencing                            |
| terminal grant generation，operation 仍 not_started 的安全 suffix | request 行内分配下一 grant generation，复用该 operation 再授权                                  |
| 任一 item dispatching/accepted/unknown                            | 该 operation 禁止再授权；只恢复/对账，同 request 的其他 waiting suffix可用下一 grant generation |
| transient attempt 但没有 reservation                              | 重新评估 control/region/account/quota/fence                                                     |

所有新 generation 都必须引用 predecessor 和 generation reason；并发 retry 由 advisory+request 行锁保证只有一个 winner。

随后由实际 holder 调用 `activate_execution_grant()`，在同一短事务验证append-only credential install receipt并CAS reservation `reserved_unactivated -> active`、fence `held_unactivated -> held`及current receipt projection，成功后才允许attach。Request identity 不含 attempt；holder session 必须包含worker boot UUID、Activity attempt和随机session UUID。

Activation本身是`control -> protocol assignment -> active chain generation -> hard-request gate -> worker runtime scope -> region -> external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> exact governance transition policy -> quota policy/stable scopes -> browser -> browser health/policy -> current context -> buckets -> fence -> reservation -> credential receipt`的短事务：只允许协议及control/chain/scope/region epoch匹配、assignment无live hard request、runtime scope仍open且boot/InvocationID未变、binding与session current pointer/version/gate epoch/evidence/expiry、quota config gate state/epoch/current policy、required scope grant state/epoch集合、subject global/mode gate version/epoch/state/evidence、governance transition policy ID/version/classification/action hash、quota policy及bucket baseline/block snapshot、browser-health gate version/epoch/policy/boot/context/readiness receipt和权威context owner/state全部等于reservation冻结值且config/scope gate open、bucket verified/unblocked、health ready/context clean/readiness有效，并且DB now严格早于current/frozen session verified-until、quota policy/scope revision effective-to、time-bounded readiness fresh-until及reservation deadline、相同holder session、lease/token/boot、fence held_unactivated；普通browser-health projection version变化不使activation失败。任一session/governance/quota-config/quota-scope/health/context gate经历block/drain/dirty→recover/open/clean也因单调gate version/epoch或context identity不等而拒绝当前activation，安全释放reserved并新建grant。Commit明确成功或随后read-back证明reservation active+fence held+same receipt前，调用方绝不能attach。Activation commit outcome unknown时本地fail-closed，先按同holder/receipt read-back；不能因为“很可能成功”继续。Install失败、hard request、scope/gate/health关闭、policy/snapshot漂移或pending/会话/readiness过期不能回旧token，只能release reserved并revoke→quarantine。

数据库 commit 成功但响应丢失时：

- 同一 holder session 可以查询并完成原 activation；
- 不同holder看到`reserved_unactivated`也**不得原地adopt、改holder或沿用旧token**：gateway install可能已经发生而DB activation/receipt ACK未知，仅靠DB“尚未active/没有permit”不能证明旧writer不存在。它必须先按同一operation read-back旧install/connection/holder scope；只有原holder可恢复原activation。其他holder一律撤销旧pending credential、取得detach+gateway barrier或旧execution scope退出证据，证据不全则quarantine/recovery；释放旧reserved后以新grant generation重新走`held_unactivated -> install receipt -> activation`，不能在旧reservation上捷径接管；
- 不同 holder 若看到 active，不能复用旧 lease；只能恢复 terminal/staging，或等待旧 holder结算；所有权无法证明时 revoke→quarantine→物理恢复，再为 operation 仍 not_started 的安全 suffix 取得新 grant generation；
- attempt 1 的 `to_thread` 可能仍存活，attempt 2 绝不能因为逻辑 request 相同就同时成为合法 writer。

### 7.3 `heartbeat_execution_grant()`

一次短事务只能按§7.1完整顺序`control -> protocol assignment -> actual workflow chain generation -> hard-request gate -> worker runtime scope -> region(需要normal/capture权限时) -> external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> governance transition/quota policies -> browser -> browser health/policy -> current context -> buckets -> fence -> reservation`条件续期二者，并比较当前Activity实际workflow run与reservation/poller/boot receipt冻结身份；不接受caller另传一组可漂移ID，也不得先锁browser/fence再补锁subject/account：

```text
WHERE reservation_id/token/state live
  AND assignment/version/contract 匹配 reservation 与 control 最低版本
  AND chain.temporal_run_id = actual Activity workflow_run_id
  AND chain.state IN ('active', 'closing')
  AND worker_scope.id/epoch/InvocationID/boot 匹配 reservation/poller/boot receipt
  AND worker_scope.state IN ('open','draining','emergency','recovering')
  AND region health/policy/freshness 与 reservation 匹配且 DB now 未过期（normal/capture新权限）
  AND binding/session current revision、version、gate epoch、evidence 与 reservation 匹配
  AND DB now < session verified_until（非NULL时）
  AND quota config gate.state='open' 且 epoch/current policy 与 reservation 匹配
  AND 本mode全部required quota scope grant_state='open' 且epoch集合hash匹配
  AND subject global/mode gate version/epoch/state/evidence 与 reservation 匹配
  AND governance transition/quota policy ID/version/hash 与 reservation 匹配
  AND browser health gate version/epoch/policy/boot/context/readiness 与 reservation 匹配
  AND quota policy/scope effective-to、readiness/bucket/reservation各自纯时间deadline均未越界
  AND fence.instance/token/lease_id/holder 全匹配
  AND fence.state IN ('held_unactivated','held')
  AND revoke_requested_at IS NULL
  AND expires_at > clock_timestamp()
```

返回新的DB`expires_at`、control directive和`allowed_capability_ceiling`，不是bool。Ceiling至少分`normal_same_grant/capture_settle_only/cleanup_only/none`，不能用一个normal布尔把“允许继续捕获”误解为“允许发送下一题”：

- `held_unactivated`只可在严格credential-install/activation timeout内续`none`，不能attach/write；
- fence held、matching credential、chain active、无live hard request、control/scope/session/governance/health gate均有效时，context=`clean`可返回`normal_same_grant`；
- context=`owned_dirty`且browser/boot/context/lease/token/holder/reservation精确等于当前grant时，若current operation仍dispatching/accepted或有合法capture stream，只返回`capture_settle_only`，允许有界capture/staging/settlement但禁止begin下一题；若前序operation已terminal且pending effect/frame/stream=0、forwarded sequence闭合，可返回`normal_same_grant`，下一题仍须由`begin_submission`最终复核；
- context=`tainted`、owner lineage不匹配或health non-ready时只能`cleanup_only/none`并按原因quarantine，绝不续normal；
- 普通health projection version/streak在未达阈值时不降级；health gate/context identity/policy改变、session/readiness纯时间过期则立即降低ceiling，旧state恢复也不复活grant。

Heartbeat新expiry继续clamp到session verified-until、time-bounded readiness fresh-until和所有bucket/deadline，不得跨界。Chain=`closing`、live hard request、control=`pause_requested/draining`、quota config gate非open/session/governance/health失效或worker scope=`draining/emergency/recovering`只能在预先冻结的短cleanup上限内续`capture_settle_only/cleanup_only`，用于结算已提交项、为关门前已经dispatching/accepted的题完成有界capture/staging、cleanup/detach/finalize，不能创建普通request/grant、延长/新签submit permit、准备后缀或发送。Cleanup/pending deadline到达仍未收口则quarantine/recovery，绝不能靠无限heartbeat阻塞CAN/pause/scope recovery。Global或worker-scope`emergency`要求lost/quarantine；若共享unit将被kill，heartbeat ACK不能替代member physical isolation。Retry/Reset的actual run无active/closing chain行时rowcount=0。任何rowcount=0、commit outcome unknown或DB连接不确定都使本地handle进入`suspect/lost`：

- 禁止新的浏览器写操作；
- 下一个 submit 前必须同步 strong validation；
- 到 `last_confirmed_expires_at - safety_margin` 仍无法验证时永久 lost；
- token mismatch/revoked/expired 立即 lost。

### 7.4 `begin_submission(item, lease)`

这是“系统签发发送授权并保守 debit”的数据库线性化点，不是第三方平台收到 click 的线性化点。它必须紧邻 click/Enter 之前调用，但两者之间仍存在不可消除的外部 I/O 窗口：

1. 事务开始前只允许从reservation/item的immutable snapshot解析将要锁定的ID并规范排序，不取得任何业务行锁、不读取“当前可用”结论，也不产生外部I/O。步骤2是本事务唯一的加锁动作；后续步骤只在已持完整前缀锁时验证/写入，禁止先锁region/account再回头取得assignment/chain。
2. 一次性按`control -> protocol assignment -> active chain generation -> hard-request gate -> worker runtime scope -> region(shared) -> external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> governance transition policy -> quota policy/stable scopes -> browser -> browser health/policy -> current context -> buckets(scope/period/id固定序) -> fence -> request(如需) -> reservation -> request item -> submission operation -> continuation(如需) -> reservation item -> capture attempt(如需)`取锁。所有context writer也必须经过browser→health前缀。Gate/policy/health/context writer使用相同锁域与排他锁，所以从检查到dispatching/debit/permit commit没有TOCTOU。
3. 在上述锁内统一验证：control=`open`；protocol/actual chain/三级effect epoch匹配且chain active、assignment无live hard request、runtime scope open；region derived state仍`ok/fresh`、health epoch/policy等于reservation且current freshness不短于冻结值；subject verified、account active、binding/session current snapshot与expiry匹配；quota config gate open且epoch/current policy匹配，本mode全部required stable scope open且epoch集合hash匹配，policy/scope revision与bucket baseline/block snapshot匹配；subject global/mode governance enabled且gate snapshot、exact transition policy合同匹配；browser health ready、current context权威行与reservation的boot/context/owner lineage匹配；fence/lease/token/holder与剩余TTL匹配。DB now必须严格早于session、policy/scope、bucket、readiness、permit和reservation全部纯时间边界。Observation generation仅因success-to-success推进并单调延长freshness、或普通非授权projection version变化时不撤销；region/config/manual/policy、binding/session、quota config/scope/bucket gate、governance gate、browser health/context identity/policy任一漂移都阻断未发送item并release/regrant。Context门允许初始`clean`，或只允许精确same browser/boot/context/lease/token/holder/reservation的`owned_dirty`且全部前序operation terminal、pending effect/frame/stream=0、forwarded sequence闭合；current/前序operation仍在capture、另一owner dirty或tainted时不能开始下一题。Context `clean -> owned_dirty`、operation `not_started -> dispatching`、debit和permit在同commit发生；后续已知terminal只更新owned lineage，unknown/lost则同commit taint/quarantine。该CAS同时把冻结governance/browser-health policy合同复制进operation/permit。
4. 仅允许 operation `not_started -> dispatching` 与 reservation item `reserved/preparing -> dispatching` 在同一事务同时成功；任一 CAS 失败则全回滚。
5. bucket-set 中每个 bucket 原子执行 `reserved_units -= 1; debited_units += 1`；任一行不满足条件则整个事务回滚。
6. item 写 disposition=unknown 的保守起点、lease/token、DB timestamp，并签发绑定 control/chain/worker-scope effect epoch、worker boot/InvocationID、instance、lease、token、browser boot、holder、purpose、audience 的一次性极短期 `submit_permit_id/expires_at`。
7. 写唯一 transition/outbox，commit。

若这个事务失败或结果不确定，adapter 不得 click。Permit 到期后不得 click；公共 guard 必须在本地原子消费一次。`permit_consumed_at` 只表示 guard 开始调用，不证明平台接受，也不能与 click 原子提交。若 gateway 另行实现并验证了 per-effect authorization control channel，可再由 gateway 消费 submit nonce；不要假设 gateway 能从通用 Playwright CDP 消息可靠识别“这就是平台提交”。若无法证明未发送则保守 unknown；若事务明确成功后进程崩溃，重试看到 dispatching，只能 unknown-debit，不得重发。

`pause_requested` 提交后不会再产生新 submit permit，但在它之前签发的 permit 仍可能随后 click。Cooperative direct-CDP 模式只能依靠最后一次 control strong check、短 TTL 和 holder cancel 缩小窗口；严格 gateway 用新 pause epoch 安装 connection/message barrier，拒绝旧 connection credential 下尚未转发的 effectful CDP message，而不是假装理解题级 submit permit。两种模式都不能撤回已经转发的命令，Pause API 必须等 quiescence/隔离后才宣告 `paused`。

### 7.5 `mark_submission_result()`

提交 helper 必须返回三态，而不是 bool：

```text
not_attempted          # 可以安全选择另一种发送方式
dispatched             # 已发出命令，禁止 fallback 再发
outcome_unknown        # 命令是否生效未知，禁止 fallback 和 retry
```

观察到 composer 清空、请求/响应或 completion 开始后，条件更新为 accepted。若 click 已调用但返回异常，直接 unknown；不得再使用 Enter。只有能证明 click 根本没有执行时，才允许在同一 dispatching operation 内选择一次键盘路径。

`mark_submission_result()`先无锁解析operation冻结的subject/account/session/policy/browser/boot/context/bucket IDs，再在一个短事务中按`control/assignment/chain cleanup gate -> subject -> account -> binding session -> subject governance -> frozen governance policy -> quota policy -> browser -> browser health/frozen policy -> frozen/current context -> buckets -> fence(只有taint/quarantine需要) -> request -> reservation -> request item -> submission operation -> reservation item -> effects/receipts/outbox`取锁。它用operation+reservation item的expected state/version、permit ID、holder/lease/token CAS选出唯一operation winner，并在**同一commit**完成：operation/item终态、quota effect、唯一governance gate effect及critical projection、governance outbox、唯一browser-health effect、context owned-dirty/tainted投影，以及达到health threshold或unknown/lost时的fence quarantine/token event。重复/ACK-loss winner read-back也必须比较同一context generation/version和整组effect/receipt，不能把browser health/context放到terminal后的第二事务，也不能先block browser再留下item/额度悬空。

纯成功/已知accepted/captured只按冻结health policy做允许的streak projection，并保持精确same-holder `owned_dirty`，不推进health gate或误quarantine；unknown/ownership不明同commit taint+quarantine，已证明browser-local infra failure只有达到显式threshold才推进gate。Governance/browser-health两个effect分别以同一operation root派生的确定source key幂等；只有各自insert winner应用一次old→new delta。Stale/重复caller或insert loser逐项compareterminal、quota、governance、health、context、fence event和outbox并恢复winner，不得再次调用平台、再次加streak或改写事实。Terminal commit ACK丢失时按operation root同时read-back整组事实；缺一或hash冲突即fail-loud/quarantine，不创建“补一条”不同event。

### 7.6 Capture、settlement 与 task persist

- accepted 后捕获答案；长等待循环必须轮询 `lost_event/cancel_event`。
- 捕获开始先建 capture attempt；成功先写 durable staging/hash并把 attempt verified。Normal capture 可同时把尚未 settled 的 reservation item 置 captured；late captcha capture 只更新 capture ledger/request result，绝不修改已 terminal quota item。
- captured/accepted 最终 consume；明确 not attempted/not sent 才 release；dispatching 未知则 unknown-debit。
- 每个terminal transition同事务写唯一gate effect和引用它的governance outbox；outbox consumer不再更新critical gate/streak。
- 如果outcome会立即改变admission（wall_quota、muted、captcha、session expired、credential tainted、mode block、hard error），terminal事务按`subject -> account -> binding session -> subject governance -> ...`统一锁序同步更新规范化authoritative gate，递增对应gate/session version+epoch，并让当前Activity立即停止/release suffix；不能等待异步outbox后才阻断下一题。Suffix terminalization引用root effect但自身写`audit_only delta=0`，不放大failure streak。
- Activity retry 从 item/staging 恢复，不再发送。
- workflow的`persist_collection_result()`只消费Activity结果携带的materialization command ID，并根据submission operation、可选reservation item、答案必需的capture attempt+verified staging/content hash校验不可变provenance，幂等写`CollectionTask`/result revision/receipt；late capture command只追加revision并CAS选择。后台materializer使用完全相同的consumer。任何反向projection交给不持run锁的consumer。
- 旧无 operation/reservation 的历史结果仅在明确 legacy history 兼容路径接受，并标注 legacy provenance；新 enforced execution 的所有结果必须有 submission operation，凡实际获 grant/attach/send 的结果缺 reservation item 必须 fail-closed；最终未 grant neutral 结果允许 reservation 为空但必须有 verified request-item manifest。

### 7.7 `finalize_execution_grant()`

Finalizer 必须在采集 Activity 返回结果之前幂等执行；workflow 后续的 task persist 不拥有、也不能继续 heartbeat 这个浏览器 lease。Activity 在 finalize 前崩溃时由 sweeper/reconciler 接管。拆成三个阶段，避免持 DB 锁 detach：

1. `prepare_finalize`短事务：结算所有reservation item；未执行`reserved/preparing` release并把对应request item退回waiting（除非业务已明确terminalize），dispatching保守unknown-debit；header聚合只作校验缓存；写durable finalization intent，并为普通结束创建deterministic release operation的`prepare_cleanup`行及完整cleanup manifest。Captcha路径改为创建/推进handoff operation，不同时创建generic release。
2. 事务外由同一浏览器线程按唯一command执行page/context cleanup，落matching cleanup receipt并apply；只有release到`detach_pending`后才detach CDP并记录可验证结果。不得在DB事务内执行cleanup或detach。
3. `complete_finalize` 不得另写一套裸 fence UPDATE。普通完成时创建/恢复 §8.5 的同一 deterministic `browser_release_operation`，只有账号仍属于本 grant且未被更硬状态覆盖时才把 running projection CAS回idle，并按 release operation推进 detach/gateway-close receipt、expected lease/token/boot/context CAS及event。Detach失败、token/side effect不确定或lost时以旧generation CAS转quarantine并创建recovery operation。Activity只有在read-back得到 `released`、`quarantined` 或 `stale_winner/no-op` 这类durable终态，且本地holder已停止写/退出作用域后才能返回；release commit ACK丢失必须恢复同一operation，不能再次盲目release。Captcha分支显式调用 `complete_captcha_handoff()` 转移owner，不能再执行generic normal release。

不得在 finally 中无条件把 account/browser 改回 idle，也不得无条件 release 当前 fence 行。

### 7.8 Sweeper/reconciler

每分钟处理过期 live reservation：

| item 状态            | 回收动作                         |
| -------------------- | -------------------------------- |
| `reserved/preparing` | release reserved units           |
| `dispatching`        | unknown-debit，禁止重发          |
| `accepted`           | consume；若无 capture 则进入对账 |
| `captured`           | consume，并确保 staging/outbox   |

然后 terminalize reservation，安全恢复账号 projection。关联 fence 若非 normal detach 证据则 quarantine。

Reconciler 还必须持续检查：

- account running 但没有 live reservation；
- live reservation 但 account 非相容状态；
- reservation lease 与 browser fence token 不一致；
- bucket projection 与 item ledger 不守恒；
- task已存在但item兼容projection未关联；或policy=persist的terminal/captured item既无pending materialization command也无receipt/Task；policy=suppress却无suppression receipt；
- outbox/receipt 卡住；
- expired held_unactivated/held fence 未 quarantine；
- 同一 logical operation 多个发送终态。

修复动作必须幂等并留下审计，不能静默改历史。

## 8. Browser fencing 的实现要求

### 8.1 显式 lease handle

把当前 tuple `BrowserLease = (context, page, is_resident)` 改成显式对象，例如：

```python
@dataclass
class BrowserFenceLease:
    instance_key: str
    holder_id: str
    lease_id: UUID
    fencing_token: int
    browser_boot_id: str
    worker_runtime_scope_id: str
    worker_scope_effect_epoch: int
    pause_epoch: int
    expires_at: datetime
    lost_event: threading.Event
    cancel_event: threading.Event

    def assert_owned(self, stage: str, *, strong: bool = False) -> None: ...
    def begin_submission(self, item_token: str) -> SubmissionPermit: ...
    def mark_lost(self, reason: str) -> None: ...
```

`platform_browser()` 返回包含 `context/page/resident/lease` 的对象。五个平台 `_browser_session()`、`_collect_one()` 和 submit helper 必须显式传递 lease，不能继续让 token 藏在锁对象私有字段里。

每次 acquisition 使用独立 `lost_event/stop_event`，禁止复用可能被旧 heartbeat 线程污染的 Event。

### 8.2 Holder 身份

`hostname:pid` 不足以定位一个 Activity，PID 还会复用。Holder 至少包含：

```text
worker boot UUID
node/machine identity
worker service unit 或 container identity
systemd unit InvocationID
PID + `/proc/<pid>/stat` starttime
cgroup path；如使用容器则包含 immutable container ID
workflow_id
workflow_run_id
activity_id
activity attempt
run_pub_id
per-attempt execution scope/subprocess ID（若已隔离）
```

日志和 UI 可以 mask/缩写，但 DB 审计必须能精确定位并取消旧 holder。进程检查/终止优先用 pidfd + cgroup + InvocationID；不得先按 PID 检查一次、稍后再对可能已复用的同一数字执行 kill。

当前 Temporal worker 使用共享进程线程池，取消 `asyncio.to_thread()` 不能物理终止其中一个 Activity。目标实现优先让每个浏览器-owning Activity/instance 运行在受 supervisor 管理的独立 subprocess 或 systemd transient scope，并把 Playwright IPC、stdout/exit 和 cgroup 归属纳入 holder receipt。若首期仍使用共享 worker，异常恢复不得假装能 kill 单一线程：必须先按§6.9锁定稳定worker runtime scope、原子关闭该scope的新normal授权并冻结同一 `worker_boot_uuid + unit InvocationID` 下的全部 live lease member set，进入worker-scope emergency，逐一停止新 permit、结算/unknown、quarantine，再由唯一scope owner停止整个 worker unit。遗漏任一同 boot lease、扫描期间允许新grant漏入、或让两个instance queue分别操作同一unit时均不得执行kill，也不得只恢复目标instance。

### 8.3 先拿 local lock，再联合取得 DB grant

推荐流程：

1. 只读预选 account/instance，仅用于确定 local lock key，不产生授权；
2. 在实际执行 Playwright 的阻塞线程取得该 instance 的进程内 local lock；
3. 调用 `acquire_execution_grant(..., expected_account_id, expected_instance_key, ...)`，DB 事务只能锁定并授权预选的同一 account/instance；
4. 条件变化则返回 retryable reselect，释放 local lock 后重选；绝不能拿 A 的 local lock 却返回 B 的 DB lease；
5. `platform_browser(..., admitted_lease=lease)` 只 adopt 已取得的 DB lease，不得二次 acquire；
6. 外层 execution lease 是 heartbeat、settlement、detach、DB release 和 local release 的唯一 owner。

这避免 DB grant 已取得后在同一进程 local lock 上长时间等待。任何采用“DB 先 grant、再 local adopt”的实现，都必须在 adopt 失败时立即幂等结算 reservation 并 quarantine/release fence，且证明不会泄漏。

### 8.4 TTL、heartbeat 和 suspect/lost

当前 2 小时 TTL 过长。长 captcha assist 应靠持续 heartbeat，不应靠僵尸 TTL。建议从配置给出：

- heartbeat 15–30 秒；
- normal collection TTL 为 3–6 个 heartbeat 周期；
- reserved 未 active TTL 约 5 分钟；
- 绝对 `max_deadline_at` 不超过 Activity start-to-close 加合理清理窗口；
- submit strong check 要求 `expires_at - db_now` 大于明确 safety margin。

Heartbeat DB 异常不能无限 warning 后继续。`suspect` 与 terminal `lost` 分开：

- DB transport/commit 不确定立即进入 suspect；suspect 期间禁止所有 effectful CDP，包括导航、点击、键盘、mode toggle 和分享 UI，只允许明确无副作用的被动观察；
- token mismatch/revoked/expired/boot mismatch 立即 terminal lost，不可恢复；
- DB 恢复且 strong CAS 成功才可从 suspect 回 active；
- 越过 last-confirmed expiry margin 后永久 lost；
- adapter 的长等待必须分片轮询 lost/cancel，不允许一个 20 分钟阻塞调用忽略失租。

DB 不可达时只能本地 fail-closed，不能声称已经把数据库行置 quarantine；待连接恢复后，reconciler 以旧 lease/token 条件 CAS quarantine。Stale finalizer/reconciler rowcount=0 时不得触碰当前新 generation。

`asyncio.to_thread()` 被取消不会自动停止底层 sync 浏览器线程。必须把 cancellation 转成共享 abort event，并让 sync 循环主动检查。

### 8.5 Normal release

Normal release不是无记录的一次UPDATE。`prepare_finalize`在detach之前先创建/恢复确定性的`browser_release_operation`：

```text
operation_id, operation_key UNIQUE
instance_key, lease_id, expected_fencing_token, holder_session_id
expected_browser_boot_id, expected_context_generation, finalization_generation
state: prepare_cleanup / cleanup_executing / cleanup_verified / detach_pending / detach_verified / released / quarantined / stale_winner
context_cleanup_operation_id NULLABLE, context_cleanup_receipt_id NULLABLE
detach_receipt_hash NULLABLE, gateway_close_ack_hash NULLABLE
claim_generation, claim_token, claim_expires_at
fence_event_id NULLABLE, last_error, created_at, updated_at
UNIQUE(instance_key, lease_id, expected_fencing_token, finalization_generation)
```

其唯一key绑定`instance + lease_id + expected fencing token + holder session + finalization generation`；初始key不能依赖尚不存在的cleanup/detach receipt。`prepare_finalize`先把release置`prepare_cleanup`并冻结§6.9 cleanup operation/member/check manifest后commit；page reset/context recreate/gateway barrier全部在DB锁外以唯一command执行。Matching cleanup receipt apply后才到`cleanup_verified -> detach_pending`。事务外detach后，以operation phase/version CAS写一次`detach_receipt_hash`和gateway connection-close ACK并转`detach_verified`；相同hash重放幂等，不同hash fail-loud/quarantine。同一release retry必须复用operation/cleanup command ID，因此crash在cleanup发送前后、receipt commit前后、detach前后或DB ACK前都能read-back，不能盲目重做reset/detach。

正常 release 的顺序是：

```text
停止新的写操作
完成/结算当前 submission
prepare cleanup，冻结全部operation与pending-resource check manifest并提交
事务外执行唯一cleanup command；核验receipt并原子 owned_dirty -> clean
从同一线程 detach CDP
确认 detach 成功；gateway 模式还要先取得该 lease connection registry 的 close/barrier ACK
以 release operation + lease_id + holder + token + boot/context generation 条件 CAS 为 free，并 token+1；同事务写唯一 release event
释放 local lock
```

DB release commit/响应不确定时不得盲目再 release，也不得按 instance 无条件 quarantine，必须以同一 release operation 做 read-back：

- fence 已 free、token 正好是 expected old token+1，且匹配 release event/receipt 已存在：视为原 commit 成功；
- fence仍由同一lease/holder/old token持有，且durable cleanup+detach/connection-close/无taint证据都完整：只允许重试同一expected CAS；cleanup/证据不完整则先恢复同一phase，无法证明时用old-token CAS请求quarantine；
- token 已前进、boot/lease/holder 已变化：当前 caller 是 stale，只读归档结果并 no-op，绝不能释放或 quarantine 新 owner；
- DB 仍不可达或无法区分：本地永久 lost、保持 local lock/执行作用域隔离能力直到 supervisor接管；恢复连接后只以 old lease/token提交 quarantine intent。不得声称 free。

Stale release 必须返回确定性的 winner/read-back 状态，不得修改新 holder。即使 quota/item 已安全结算，浏览器所有权也必须单独按上述协议完成。

### 8.6 Force release 改成 revoke/quarantine

管理端接口必须要求：

- `expected_lease_id`；
- `expected_fencing_token`；
- 原因；
- 操作者权限和审计。

管理端不得把任何`held_unactivated/held`行直接置为free，包括已经过期的行。只有当前完全匹配的holder在成功detach且side effect/credential pending已结算后可以normal release；管理端对free行只能做幂等空操作。活动lease的操作是两阶段revoke：

1. token-CAS 写 `revoke_requested_at/reason/actor`，状态 revoking；
   同一事务递增 fencing token，使旧 heartbeat 和尚未使用的 permit 立即失效；
2. signal/cancel 精确 holder，等待绑定旧 lease/token 的 stopped/detached ack；
3. Holder ACK 只是协作证据。没有 gateway connection registry ACK 或可信 supervisor kill/boot 证明时，即使收到 ACK 也进入 quarantine+restart，不能直接 free；
4. 超时、无法确认或 ACK 不匹配都 quarantine；
5. 按 §8.9 完成 gateway 或 kill-holder+restart 的物理恢复，验证新 boot/epoch 后才能 recover/free。

禁止简单写 `released_at=now()` 后立即允许另一个 worker 接管。

### 8.7 异常过期不允许直接 preempt

旧 holder 在数据库 lease 过期后仍可能持有有效 CDP WebSocket。因此：

```text
held_unactivated/held + expired -> quarantined
```

而不是：

```text
held_unactivated/held + expired -> 新 holder token+1 直接接管
```

只有确认旧连接被物理断开后才能 free/acquire。浏览器重启必须记录并验证新的 boot identity，例如 systemd InvocationID 与 browser process start identity。

### 8.8 Token-aware CDP gateway

长期推荐把 raw Chrome CDP 端口只暴露给本机 gateway：

- worker 使用短期签名 gateway connection credential 连接。它必须绑定 `instance/lease/token/current+acquired boot/holder/purpose/pause epoch/audience/nonce/expiry`；fencing token 本身不是 bearer credential。该 credential 与题级 submit permit 是不同对象；前者 fencing 连接所有权，后者由 submission guard 控制一次业务发送；
- credential安装是可审计外部I/O，严格走DB `held_unactivated/credential_pending(effect none)`→gateway install/barrier→append-only install receipt→DB activation；gateway不能只因看见新token就开放连接，DB也不能在receipt前宣称held可写。Receipt direct lineage和ACK-loss规则统一使用§9.5的通用schema；
- gateway 在 WebSocket upgrade 校验 connection credential，并在每条 CDP message 转发前确认连接 generation 仍等于本地权威 epoch且未被 revoke。权威缓存必须由 DB/recovery supervisor 的有序事件同步；同步中断或 epoch 不确定时 fail-closed；
- DB revoke/pause 事务提交后，gateway 必须把 exact fence/control event 安装成转发屏障：先停止读取/接受旧 generation，取消或丢弃尚未写入 raw upstream 的旧队列 frame，等待所有 per-connection forwarder 越过 barrier，再关闭 browser/target/CDP session。只有此后才能 ACK；
- ACK 必须绑定 event ID/hash、instance、old/new token、old/new pause epoch、boot、gateway boot ID、旧连接数，以及每连接 last-forwarded/dropped sequence。它承诺“ACK 后不会再向 raw upstream 写入任何旧 generation byte”，而不是只承诺 WebSocket close 已调用；伪造、过早、旧 boot/event ACK 无效；
- gateway restart 必须先关闭全部现存连接，清空旧授权缓存；完成 DB/supervisor 全量 resync 前不得开放 upgrade；
- 一个 Playwright browser 下新建的 target、worker、service worker 和 nested CDP session 都继承同一 lease，不能另开未登记连接；
- gateway 必须重写 `/json/version`、`/json/list` 等 discovery 返回的 WebSocket URL 为 gateway 地址，日志/UI 也不得泄露 raw endpoint；
- probe、drill、OTP、captcha assist 和 maintenance 必须同时持有 purpose 明确的 lease并经 gateway；maintenance/recovery purpose 不携带 collection quota，也不能调用 submit guard；
- 验证当前 patchright/Playwright `connect_over_cdp(headers=...)` 能承载 permit，并对 reconnect、redirect、new target 做旁路测试。

“raw 端口绑定到 `127.0.0.1`”不是访问控制。当前若 Chrome、worker 和工具使用同一 OS 用户，本机任意进程仍能绕过 gateway。严格 fencing 上线必须选定并实测至少一种物理拓扑：

1. Chrome remote-debugging pipe/Unix socket 只授予 gateway，worker 看不到 raw endpoint；
2. Chrome/gateway 与 worker 使用不同 OS principal，并用 network namespace、nftables/cgroup 或等价机制限制 raw port；
3. 其他能由负向测试证明“worker UID 即使知道地址也无法建立 raw CDP 连接”的机制。

Gateway barrier ACK 只能证明 ACK 之后不再转发旧 generation byte，不能撤回 barrier 前已经写入 raw socket/kernel/Chromium 的 click、脚本或网络请求。旧队列中被丢弃的 frame只证明“这一帧未转发”，不能证明某题没有提交：通用 Playwright/CDP gateway 无法把任意 `Input.*`、`Runtime.evaluate` 或随后无关 frame可靠映射到业务 operation。除非另行实现并验证 per-effect channel，把 operation/submit permit 唯一绑定到准确 upstream frame/sequence，并证明不存在更早或替代提交路径，否则**异常终止、失联或跨owner交接中**边界不明的题级状态仍是 dispatching→unknown，不得 refund/retry；已写入或边界不明的 frame归 forwarded/unknown，旧 page/context置tainted，继续按 unknown/consumed 结算并重建context，无法证明时重启resident browser。相反，连接与owner始终可信、operation取得唯一accepted/captured/terminal receipt且forwarded sequence完整闭合的正常路径只维持同一owner的`owned_dirty`，可按§6.9 cleanup或受限same-holder owner transfer收口，不能因为“曾经转发过命令”就误判tainted。仅关闭socket、networkidle或页面看似稳定在两种路径中都不构成clean证明。

完整目标优先实现 gateway + raw-port isolation，并保留 browser restart 处理已转发/tainted 命令。如果分期使本期暂不实现 gateway，异常 lease 的最低恢复门槛是 `quarantine + 精确终止旧 holder process/unit + resident browser restart + 新 boot 验证`；任一证据缺失都保持 quarantine，不能恢复派发。这种 conservative kill-and-restart 可以避免并行接管，但不能宣称支持无中断严格 fencing。

### 8.9 Physical recovery 状态机

异常恢复必须由 durable outbox 驱动，并把数据库决定与进程 I/O 分段：

1. 短事务按 `browser -> browser health -> context -> fence` 锁序，用expected lease/token/current boot/context CAS `held_unactivated/held/revoking -> quarantined`；同一commit把health置非ready并推进gate version/epoch、把context置tainted、把fence token+1，创建下一`browser_recovery_operation.recovery_epoch`、typed health effect、fence event、受影响continuation/holder cleanup ceiling和outbox。任一行未命中则整笔无作用并read-back winner；不能只锁/刷新“地域行”，也不能先改fence后再补health/context。
2. Recovery worker claim 时在 DB 原子增加 `claim_generation`。它不能直接拥有 systemctl/kill 权限；必须先向 node-local supervisor 发送 authenticated `adopt(recovery_epoch, claim_generation, operation_id, expected token/boot, ordered physical resource set, expiry)`。Supervisor只接受已由DB冻结的resource-set hash。它按§6.9的稳定层级锁所有相交物理资源；per-instance executor只是最小叶子域，不能串行化共享worker unit上的stop/kill。任何instance recovery需要触碰共享unit时必须先成为matching worker-scope recovery的child。
3. `adopt(g+1)` 是接收端执行屏障，不是简单把 `max_generation` 写大：先停止接收/取消尚未开始的低代 command，再等待或安全取消所有相交resource set中已经通过校验的低代 systemd D-Bus job、子进程、pidfd/cgroup 动作，并逐个取得终态、实际 unit/process 状态和归档 receipt；只有确认不存在仍可能晚到生效的低代动作后才能 ACK g+1。若 job 卡住、取消语义不明或无法证明已终态，新的 adopt 不 ACK，DB 保持 quarantined。后续每个 `signal holder / install gateway barrier / stop / kill / start / probe / seal` 命令都带同一 generation、单调 phase sequence、expected InvocationID/boot/PID starttime/cgroup、resource-set hash 和唯一 command ID。每个命令严格走fsync command journal并同步 await terminal proof，禁止 fire-and-forget `systemctl`/shell child；重复 command 只返回已保存结果，绝不重做 kill/start。只有 supervisor OS principal 能操作 worker/browser unit。
4. 先物理终止/隔离旧 holder并取得 gateway barrier ACK，再 stop/kill browser cgroup；使用 pidfd、cgroup 和 unit InvocationID验证旧 holder及旧 Chromium均不存在，避免 PID 检查与 kill 之间的复用 TOCTOU。独立 per-attempt execution scope 可以精确终止；共享 worker 模式则必须使用已关闭scope gate且冻结完整member set的worker-scope operation，逐个quarantine、停止permit、结算其全部live lease，再让唯一scope owner停止整个worker unit，不能声称只杀一个`to_thread`。扫描与关gate不在同一线性化协议、遗漏或无法结算任一blast-radius member、或另一instance recovery持有相交resource key时均不得发unit job。随后idempotent start新browser，生成supervisor boot nonce。任何证据不完整都重试且保持quarantined。
5. 新 boot 启动后，用短事务按 `browser -> browser health -> context -> fence` 锁序校验recovery operation/current claim generation/expected quarantine token/old boot。旧context原子retired，创建绑定新boot的`resetting` context generation，写`CollectionBrowser.current_boot_*`，health置`recovering`并推进gate version/epoch，fence token+1且保持`recovering`，同时写event；此commit**不产生ready receipt、不把context置clean，也不开放任何credential**。若commit/ACK丢失，重试必须复用supervisor已保存的新boot结果和同一context generation，不能再次start或另建generation。
6. 健康检查只能调用 supervisor/gateway 固定的 `probe_boot_health` 方法级 allowlist，执行预定义、无页面写入的 boot/process/protocol 探测，只返回 digest，不向 recovery caller暴露通用 CDP credential、Browser/Page/Context 或 `Input.*`/`Runtime.evaluate` 能力；gateway 随后完成全量 resync和新 boot barrier ACK。每次探测必须先创建并claim §6.10的`collection_browser_readiness_probe_attempt`，外部I/O携带attempt ID、claim generation/token、prober boot和expected recovery/boot/context；完成后只有current未过期claim可写每attempt唯一terminal-winner receipt。g1过期、g2接管后g1迟到只能写nonwinner noop；`not_ready/contract_rejected/timed_out`关闭attempt但不能开放health。Recovery lease不能调用 collection submit guard。若当前技术栈只能发放通用 CDP 连接，就只能表述为“没有 DB submit permit”，不能声称物理无提交能力；该 principal 仍视为 effect-capable，必须被独立作用域/网络策略隔离，证据不足时继续 quarantine+restart。
7. Supervisor 执行 **physical seal**；seal 也是覆盖完整resource set的串行执行屏障，只有本 generation 的全部已接收systemd job/child/pidfd动作终态、所有相交低generation已被adopt barrier彻底排空、旧supervisor boot journal已reconcile为零，且current process/boot/context reset/gateway-sync receipt匹配时才ACK。它拒绝此epoch的任何后续destructive command；ACK绑定supervisor boot/reconciliation receipt、claim generation、resource-set hash、new browser boot、context generation和phase/action receipt hash。Physical seal后DB只可把child/member标`physically_sealed/release_pending`并写append-only physical-isolation receipt，fence仍`recovering/quarantined`；不得在这一步置free或把browser recovery标completed。Scope级physical seal等待全部member child seal和unit/old-holder exit receipt，但**不等待hard closure完成或DB free**；业务settlement作为独立release blocker继续收口。
8. Logical release使用另一个最终DB短事务，严格按`worker runtime scope -> browser -> browser health/policy -> context -> fence -> recovery operation/child/member -> release blockers -> readiness attempt/terminal receipt`加锁：验证matching physical-isolation/seal receipt、supervisor reconciliation、新boot与`resetting` context、raw-port/gateway isolation、唯一terminal-winner且result=`ready`的readiness receipt、无live reservation/permit/credential/handoff；任何曾为quarantined的captcha continuation都已在本次physical seal之后取得matching continuation-closure receipt并转真正terminal；该member规范化release-blocker集合也全部有真实terminal receipt，所有count/hash双向anti-join为零。若它属于hard/watcher closure，则该closure必须已经四轴`completed`。只有全部条件在**同一commit**成立，才把旧resetting context retired并创建/确认new clean context、以typed `recovery_ready` effect把health置ready、把`consecutive_infra_failures`原子归0、推进projection及gate version/epoch并更新current readiness receipt，以expected recovering token/boot CAS fence `recovering -> free`且token+1，并标child recovery completed/member released；任何一部分失败整笔回滚，不存在health ready但streak仍41、health ready但fence recovering、context clean但旧receipt、或fence free后再补gate的窗口。Scope只有全部member完成此步且无blocker，才completed/open并递增scope epoch。下一次正常acquire再token+1。任何阶段crash都从durable DB + fsync supervisor command journal重入，ACK丢失按同recovery operation/effect/receipt整组read-back；不得靠人工修改行跳阶段。Claim token过期本身不能撤销已经接收的命令，但更高claim generation的supervisor `adopt`会在接收端fence旧claimant。Supervisor进程重启先执行§6.9 startup reconciliation barrier；未ready时所有adopt/seal/原始端口/grant均fail-closed，不得用“新进程内队列为空”推断旧OS动作已经结束。

Resident browser 在 cooperative profile 下不得依赖未受控的 `Restart=always` 直接重新开放 raw CDP。必须禁用自动重启，或由 supervisor generation gate + OS principal/network namespace/firewall门控：意外/自动启动先保持 raw listener 对 worker不可达，登记新 InvocationID/PID starttime/cgroup/boot nonce并提交 DB recovering state、完成 gateway/supervisor sync 后才能开放。`ExecStart` 成功不等于可派发；启动到 boot commit/seal 之间任何 worker直连都必须失败。

必须故障注入四类物理竞态：其一，A claim 后停顿至过期，B 以更高 claim generation 完成 kill/start/boot commit/seal/free且新 holder 已 acquire，随后 A 恢复并发送 stop/kill/start；全部被拒绝。其二，A 的 `systemctl stop/kill/start` 已通过 precheck并阻塞在 systemd job 内，B 此时 adopt；B 在 A job终态/取消/归档前绝不能收到 adopt ACK，更不能 seal/free，之后 A 也不能影响新 boot。其三，scope recovery扫描共享worker的instance X后，instance Y并发申请grant；由scope锁线性化为Y先提交并进入冻结member set，或scope gate先赢使Y无副作用失败，绝不漏掉Y。其四，X/Y两个instance recovery并发触发同一worker unit stop/start；resource-set executor与唯一scope owner保证只有一个规范命令序列，任一child都不能越过scope seal。仅验证旧DB claim的rowcount=0、单instance queue或命令入口generation不匹配不足以通过。

Supervisor restart再单独覆盖：在prepared intent fsync前后、D-Bus调用发出但job ID响应前、job ID fsync前后、job in-flight、OS终态后但terminal receipt前、terminal receipt后但DB ACK前逐点kill supervisor。新进程必须先保持raw端口/command admission关闭，恢复旧journal并与systemd manager/job/unit/InvocationID/PID/cgroup逐项对账；任何unknown都保持相交scope emergency/quarantined，旧job绝不能在新seal/new grant后迟到生效。

若 gateway 已阻断旧连接但存在已转发命令，只能在保持 quarantine 的情况下观察 quiescence并销毁/重建 page/context；无法证明时仍执行 browser restart。Unknown item 永不因 recovery 成功而退款或重发。

## 9. 发送状态机与 adapter 改造

### 9.1 收敛五份 submit helper

建立公共 fenced submission guard，避免五个平台各自实现略有不同的 click/Enter fallback。公共层负责：

- strong fence/grant validation；
- `reserved/preparing -> dispatching` CAS；
- submit invocation 的三态结果；
- accepted/unknown 持久化；
- 禁止双重 click/Enter；
- lost/cancel/pause 的统一异常类型；
- durable staging 和 operation receipt。

平台 adapter 只负责：

- 找到 composer；
- 输入文本；
- 提供平台特定的一次性 submit 动作；
- 提供 accepted 的可观察证据；
- 捕获答案和证据。

### 9.2 发送边界错误词

新增或固定以下 error type，并保证不误计为 wall/breaker：

```text
browser_fence_lost_pre_submit
collection_paused_pre_submit
execution_grant_expired_pre_submit
execution_grant_deferred            # typed control flow，不能持久化为题级失败
workflow_start_receipt_pending       # start已存在、receipt待read-back；副作用前安全retry
submission_outcome_unknown
submission_accepted_capture_unknown
execution_grant_contract_drift
account_quota_unreserved            # 仅明确 terminal policy/deadline
```

Batch 第 N 题失租时：

- 已完成前缀原样保留；
- 当前题根据边界记 released 或 unknown/accepted；
- 当前 grant 内未开始的 reservation item release，但对应 request item/operation 保持 waiting/not_started；
- coordinator 在业务 deadline 内可以同 attempt 取得下一 grant generation，或在 durable finalize 后返回 typed deferred/抛受控 retryable，让 Activity retry从 ledger/staging恢复；不得把原始 adapter 异常直接交给无 ledger 的整批 retry；
- 只有 deadline、用户取消或明确 terminal policy 才把仍 waiting 的后缀写 neutral aborted manifest；最终交给 workflow 的结果必须等长、同序。

Per-task legacy 入口也必须依赖 durable item ledger；错误可 non-retryable 返回，但不能因为 Activity retry 再发送。

### 9.3 逐题结算真值表

| 观察事实                                       | item 终态 |                                                 额度 |                                               自动重发 |
| ---------------------------------------------- | --------- | ---------------------------------------------------: | -----------------------------------------------------: |
| 从未进入提交动作                               | released  |                                        释放 reserved | 同 submission operation 可由新 grant generation 再授权 |
| 已 dispatching 但原 holder能证明 submit 未执行 | released  |                                      释放/补偿 debit |   终结旧 operation；按策略创建新 submission generation |
| submit 调用已发出，是否生效未知                | unknown   |                                           保守 debit |                                                   禁止 |
| 平台确认接受，答案失败/拒答/墙                 | consumed  |                                debit，不增加 success |                                                   禁止 |
| 平台确认接受并成功捕获                         | consumed  | debit + captured；task-success receipt 后才 +success |                                                   禁止 |
| Activity 已捕获但 ACK 丢失                     | consumed  |                                      从 staging 恢复 |                                                   禁止 |
| 前序失败导致本题 aborted                       | released  |                                        释放 reserved |                                   由业务决定是否新授权 |

Unknown 默认永不自动退款。只有平台对账或人工证据通过后，写唯一 compensating ledger；不得改写原 terminal 事实。

### 9.4 Quota wall 与 muted/refusal

Wall 状态和额度 disposition 是两个维度：

- `wall_quota` 若在发送前出现：confirmed_not_sent，释放本题；同时 mode/account 进入 quota block；
- `wall_quota` 若平台接受请求后返回：debit；同时进入 quota block；
- captcha/refusal 同理依赖真实提交边界；
- terminal gate-effect insert winner同步更新治理状态且不再次debit；governance outbox consumer不得再次更新critical gate/streak；
- admission-critical gate已由terminal settlement同步更新；outbox只负责审计、通知子outbox、legacy绝对projection和明确非即时派生，不能留下同batch下一题继续撞墙的窗口；
- 一个根因只产生一次 breaker/wall 事件，最终 terminalized 的 suffix neutral result 不放大失败 streak。
- captcha/session-expired/credential-tainted若被平台policy分类为session-local，只推进matching binding-session revision/gate；它仍立即使该binding所有未发送item失效。恢复verified必须有受控login/assist evidence并产生新session version/epoch，旧grant/permit不能因state再次变回verified而复活。

### 9.5 Captcha 的安全 handoff

不要在人工等待 70 分钟期间占住未发送 quota，也不要释放 browser 后给其他 run 抢走。先建立 durable manifest，而不是在 Workflow history 里传一段可漂移数组：

```text
collection_captcha_continuation
id, pub_id, handoff_id UNIQUE
predecessor_continuation_id NULLABLE
execution_request_id
root_request_item_id, root_submission_operation_id, root_reservation_item_id NULLABLE
root_disposition
platform_account_id, quota_subject_id, browser_id, region_gb, mode
binding_revision_id, binding_version
stable_platform_subject_hash, identity_alias_id
identity_scheme, canonicalizer_version, canonical_identity_alias_hash
root_binding_session_revision_id, root_binding_session_version
root_binding_session_gate_epoch, root_binding_session_evidence_hash
captcha_governance_gate_effect_id, captcha_root_governance_event_key_hash
verified_session_revision_id NULLABLE, verified_session_version NULLABLE
verified_session_gate_epoch NULLABLE, session_verifier_operation_id NULLABLE
original_request_contract_hash, resume_slice_hash
state: detach_pending / captcha_pending / assist_credential_pending / assist_held / assist_detach_pending / session_verification_pending / resume_ready_detached / claimed / capture_credential_pending / capture_held / suffix_ready / capture_detach_pending / collection_credential_pending / completed / quarantined / terminal_cancelled / terminal_recovered
next_action: joint_collection / capture_root / finish
effect_permission: none / cleanup_only / assist / capture / collection
expected_lease_id, expected_fencing_token, expected_boot_id
current_owner_holder_session_id, current_owner_process_identity
owner_transition_version
current_owner_capability_receipt_id NULLABLE
context_cleanup_operation_id NULLABLE, context_cleanup_receipt_id NULLABLE
context_owner_transfer_operation_id NULLABLE, context_owner_transfer_receipt_id NULLABLE
collector_detach_receipt_id NULLABLE, assist_detach_receipt_id NULLABLE, capture_detach_receipt_id NULLABLE
predecessor_barrier_ack_id NULLABLE, predecessor_barrier_ack_hash NULLABLE
current_credential_install_receipt_id NULLABLE, current_credential_install_receipt_hash NULLABLE
owner_heartbeat_generation, owner_expires_at NULLABLE
claim_generation, claim_token, claim_holder_session_id, claim_expires_at
deadline_at, created_at, updated_at
partial UNIQUE(execution_request_id) WHERE state IN ('detach_pending','captcha_pending','assist_credential_pending','assist_held','assist_detach_pending','session_verification_pending','resume_ready_detached','claimed','capture_credential_pending','capture_held','suffix_ready','capture_detach_pending','collection_credential_pending','quarantined')

collection_handoff_detach_receipt
id, continuation_id, handoff_id, owner_transition_version
phase: collector_detach / assist_detach / capture_detach
predecessor_credential_install_receipt_id
old_holder_session_id, old_holder_process_identity
old_lease_id, old_fencing_token, old_boot_id, old_context_generation
gateway_connection_id, gateway_last_forwarded_sequence
gateway_barrier_event_id, gateway_barrier_ack_hash
execution_scope_exit_receipt_id, execution_scope_exit_hash
detached_at, verified_at, verifier_boot_receipt_id, receipt_hash
UNIQUE(continuation_id, owner_transition_version, phase)
UNIQUE(gateway_barrier_event_id)

browser_gateway_credential_install_receipt
id, continuation_id NULLABLE, reservation_id NULLABLE
instance_key, owner_transition_version, purpose
new_holder_session_id, new_holder_process_identity
new_lease_id, new_fencing_token, new_boot_id, new_context_generation
gateway_connection_id, credential_nonce_hash
predecessor_kind: detach / same_holder_purpose_barrier / clean_release / recovery_seal
predecessor_detach_receipt_id NULLABLE
predecessor_purpose_barrier_receipt_id NULLABLE
predecessor_browser_release_receipt_id NULLABLE
predecessor_recovery_seal_receipt_id NULLABLE
predecessor_barrier_event_id NULLABLE, predecessor_barrier_ack_hash NULLABLE
gateway_install_sequence, gateway_install_ack_hash
installed_at, verifier_boot_receipt_id, receipt_hash
UNIQUE(instance_key, new_fencing_token, purpose)
UNIQUE(credential_nonce_hash)
UNIQUE(gateway_connection_id, gateway_install_sequence)
CHECK(exactly one of four predecessor FKs matching predecessor_kind)

browser_gateway_purpose_barrier_receipt
id, continuation_id, instance_key, owner_transition_version
predecessor_credential_install_receipt_id
holder_session_id, holder_process_identity
lease_id, old_fencing_token, new_fencing_token, boot_id, context_generation
gateway_connection_id, old_purpose, new_purpose
last_forwarded_sequence, barrier_event_id, barrier_ack_hash
created_at, verified_at, receipt_hash
UNIQUE(continuation_id, owner_transition_version)
UNIQUE(barrier_event_id)

collection_captcha_owner_capability_receipt
id, continuation_id, owner_transition_version, capability_generation, purpose
predecessor_capability_receipt_id NULLABLE
protocol_assignment_id, workflow_chain_generation_id
worker_boot_receipt_id, activity_poller_receipt_id NULLABLE
worker_runtime_scope_id, holder_session_id, holder_process_identity
observed_control_effect_authorization_epoch
observed_chain_effect_authorization_epoch
observed_worker_scope_effect_authorization_epoch
observed_assignment_has_live_hard_request: false
capability: assist / capture
credential_install_receipt_id, issued_at, expires_at, contract_hash
UNIQUE(continuation_id, owner_transition_version, capability_generation)
CHECK(capability matches purpose and expires_at > issued_at)

collection_captcha_owner_heartbeat_receipt
id, heartbeat_key UNIQUE, continuation_id, owner_transition_version
old_heartbeat_generation, new_heartbeat_generation
old_capability_receipt_id, new_capability_receipt_id UNIQUE
purpose, holder_session_id, holder_process_identity
worker_boot_receipt_id, worker_runtime_scope_id
lease_id, fencing_token, browser_boot_id, context_generation_id
credential_install_receipt_id
control_effect_authorization_epoch, chain_effect_authorization_epoch
worker_scope_effect_authorization_epoch
old_owner_expires_at, new_owner_expires_at, applied_at, contract_hash
UNIQUE(continuation_id, owner_transition_version, new_heartbeat_generation)

collection_captcha_logical_action
id, continuation_id, owner_transition_version, action_slot
purpose, action_kind, action_contract_hash, immutable_action_payload_hash
predecessor_logical_action_id NULLABLE
state: open / permit_live / forwarded / outcome_unknown / completed / cancelled
current_permit_id NULLABLE, created_at, terminal_at NULLABLE
UNIQUE(continuation_id, owner_transition_version, action_slot)
UNIQUE(id, continuation_id, owner_transition_version, purpose, action_kind)

collection_captcha_effect_permit
id, permit_key UNIQUE, logical_action_id, action_slot
continuation_id, owner_transition_version, action_generation
predecessor_permit_id NULLABLE
owner_capability_receipt_id, purpose, action_kind, action_manifest_hash
holder_session_id, holder_process_identity, worker_boot_receipt_id
worker_runtime_scope_id, lease_id, fencing_token, browser_boot_id, context_generation_id
gateway_connection_id, credential_install_receipt_id
control_effect_authorization_epoch, chain_effect_authorization_epoch
worker_scope_effect_authorization_epoch
fence_lease_expires_at, owner_capability_expires_at
continuation_deadline_snapshot, issued_at, expires_at NOT NULL
dispatch_claimed_at NULLABLE, terminal_at NULLABLE
state: issued / dispatching / terminal_forwarded / terminal_confirmed_not_forwarded /
       terminal_outcome_unknown / expired_before_dispatch / revoked_before_dispatch
terminal_receipt_id NULLABLE
UNIQUE(logical_action_id, action_generation)
partial UNIQUE(predecessor_permit_id) WHERE predecessor_permit_id IS NOT NULL
composite FK(logical_action_id, continuation_id, owner_transition_version, purpose, action_kind)
CHECK(expires_at > issued_at)
CHECK(expires_at <= owner_capability_expires_at AND expires_at <= fence_lease_expires_at)
CHECK(expires_at <= continuation_deadline_snapshot)
CHECK(action_generation=1 iff predecessor_permit_id IS NULL)

collection_captcha_effect_dispatch_receipt
id, captcha_effect_permit_id UNIQUE, logical_action_id
continuation_id, owner_transition_version, action_slot, action_generation
gateway_command_id UNIQUE, gateway_boot_id, gateway_connection_id
holder_session_id, lease_id, fencing_token, browser_boot_id, context_generation_id
credential_install_receipt_id, action_manifest_hash
result: forwarded / confirmed_not_forwarded / outcome_unknown
pre_forward_sequence, post_forward_sequence NULLABLE
gateway_journal_record_hash, barrier_event_id NULLABLE, barrier_ack_hash NULLABLE
evidence_hash, recorded_at
CHECK(confirmed_not_forwarded requires matching barrier evidence)

collection_captcha_continuation_item
continuation_id, original_ordinal
role: completed_prefix / root / released_suffix / never_granted_tail
request_item_id, submission_operation_id
query_hash, eligible_for_resume
state
UNIQUE(continuation_id, original_ordinal)
UNIQUE(continuation_id, request_item_id)

collection_captcha_continuation_closure_operation
id, operation_key UNIQUE, continuation_id UNIQUE
reason: normal_finish / user_cancel / deadline / pause_emergency /
        health_quarantine / owner_unknown / hard_or_watcher_termination
termination_request_id NULLABLE, chain_closing_operation_id NULLABLE
browser_recovery_operation_id NULLABLE, scope_recovery_member_id NULLABLE
expected_continuation_state, expected_owner_transition_version
expected_item_count, expected_item_set_hash
expected_logical_action_count, expected_logical_action_set_hash
expected_permit_count, expected_permit_set_hash
expected_owner_transition_count, expected_owner_transition_set_hash
state: prepared / draining / physical_evidence_ready / effects_settled / completed / quarantined
claim_generation, claim_token, claim_expires_at
created_at, completed_at NULLABLE, last_error

collection_captcha_continuation_closure_receipt
id, closure_operation_id UNIQUE, continuation_id UNIQUE
old_state, new_state: terminal_cancelled / terminal_recovered
terminal_reason, owner_transition_version
clean_detach_receipt_id NULLABLE
browser_physical_isolation_receipt_id NULLABLE
scope_member_physical_isolation_receipt_id NULLABLE
item_terminal_receipt_count, item_terminal_receipt_set_hash
logical_action_terminal_receipt_count, logical_action_terminal_receipt_set_hash
permit_terminal_receipt_count, permit_terminal_receipt_set_hash
owner_transition_terminal_receipt_count, owner_transition_terminal_receipt_set_hash
materialization_receipt_count, materialization_receipt_set_hash
terminal_contract_hash, applied_at
CHECK(exactly one matching clean-detach or physical-isolation proof)
CHECK(new_state IN ('terminal_cancelled','terminal_recovered'))
```

`quarantined`仍是live cleanup状态，绝不能被当成“已经结束”；真正terminal只有`completed/terminal_cancelled/terminal_recovered`，所以partial unique会一直占位到closure receipt提交。进入quarantine的同一事务创建/恢复唯一continuation closure operation，冻结continuation items、全部logical action/permit代、owner transition/credential/detach集合与count/hash。已issued未dispatch permit可受控revoke；dispatching/forwarded/unknown必须由gateway journal/sequence/barrier或物理隔离收敛，无法证明未发的一律terminal outcome_unknown并永久封action slot。Root/suffix item再按真实发送边界settle并生成materialization/suppression receipt，不能因“验证码流程取消”删除已debit事实。

Closure分物理与逻辑两段以避免环：先允许browser/scope recovery在fence/context保持`quarantined/recovering`时完成gateway barrier、旧holder exit、restart/reset和append-only physical-isolation seal；此时**不得logical free**。随后closure apply前无锁解析immutable集合，并按`control -> assignment -> chain -> termination-request gate(如有) -> subject/account/governance/policy(需结算时) -> browser -> health/current context -> fence -> execution request/items/operations -> continuation -> actions/permits -> closure operation final claim CAS`验证clean-detach或matching physical-isolation receipt，分别对item/action/permit/owner-transition/materialization集合做双向anti-join，在同一commit写唯一closure receipt并把`quarantined -> terminal_cancelled/terminal_recovered`。Operation ACK丢失只read-back同receipt；集合漂移、旧claim或physical evidence不确定保持quarantined。

Browser logical release、workflow chain closure、assignment/run terminalizer只把matching continuation closure receipt当“handoff已排空”；它们不能因state字符串是quarantined就跳过，也不能要求browser先free才允许closure。固定顺序是`quarantine/effect revoke -> physical seal(recovering) -> captcha closure receipt/terminal state -> browser logical free -> scope open`。若无物理异常且已有完整clean detach/barrier receipt，可以不用restart，但仍须同一closure集合证明。这样owner不明不会永久占live unique，也不会用直接UPDATE continuation绕过permit/item结算。

Manifest冻结原始ordinal、四类item、root发送事实、next grant/submission generation、完整resume slice，以及root grant/terminal的quota subject、binding、stable identity/alias和binding-session revision/version/gate/evidence完整snapshot；以composite FK回指root reservation/operation。Captcha gate-effect/root key必须是该root terminal同步写入且把同session推进captcha-required的winner。Nested captcha建predecessor link，不覆盖旧manifest。Token/credential不进入Temporal history，只存manifest ID和内容hash。70分钟等待期间account current pointer或subject/session错位不能靠回查“当前账号”迁移证据。

状态CHECK/受控transition必须证明：`captcha_pending`引用与collector旧holder/lease/token/boot/context准确一致的collector-detach receipt；每个`*_credential_pending`已经以expected predecessor receipt把token/owner transition version+1并冻结新holder/lease/boot/purpose，但`effect_permission=none`且current install receipt为空；只有matching append-only install receipt落库后，第二短事务才能CAS到对应`assist_held/capture_held/completed(collection active)`并开放准确purpose。Held状态必须引用当前transition的install receipt，pending或ACK不明绝无attach/write权限。

Captcha不是pause/closing/hard-request门的例外。所有assist/capture的prepare与activate都必须按`control -> assignment -> active chain -> assignment级live hard-request gate -> worker runtime scope -> browser -> browser health/policy -> context -> fence -> continuation`取锁，要求control=`open`、chain=`active`、没有live hard request、scope=`open`且实际boot/InvocationID匹配，并逐项比较当前control/chain/scope三级effect authorization epoch；同一commit写append-only owner-capability receipt并把其ID装到continuation。Prepare到activate之间任一gate/state/epoch漂移，activate只能revoke/quarantine，绝不能开放purpose。Pause、closing或hard request之后，既有同owner只能按冻结operation做有界capture/staging/settlement/cleanup/detach；不得prepare或activate新的assist/capture owner，也不得把`cleanup_only`包装成captcha adopt。

`assist_held/capture_held`也不等于长期页面写权限。Continuation状态机先从可信UI/adapter阶段派生稳定`logical_action_id/action_slot`和immutable payload/contract，调用方不能靠换manifest/key伪造“新动作”。每一次OTP/手机输入、验证码确认、capture触发等effectful动作，都在上述完整前缀下为该slot签发一次性、极短期`collection_captcha_effect_permit`，绑定当前owner capability receipt、action generation/manifest、holder/process/scope、lease/token/boot/context/connection、purpose和三级epoch；`expires_at`由受控DB函数计算且DB dispatch CAS要求`clock_timestamp() < expires_at`，它不得晚于冻结continuation deadline、owner-capability或fence lease中最早者，复合FK逐项保证owner/credential/boot/context一致。

Captcha permit沿用submission三态，不能“转发成功后才消费”。Gateway在写raw upstream**之前**先以permit+owner/token/epoch/未过期条件CAS `issued -> dispatching`取得唯一winner，并在自己的fsync command journal记录prepared；随后至多执行一次外部写，最后写append-only receipt并映射`terminal_forwarded/terminal_confirmed_not_forwarded/terminal_outcome_unknown`。DB/gateway/网络在dispatching后任一点崩溃，都由journal、sequence和barrier证据收敛；不能证明未转发时就是unknown且该logical action永久封口。只有predecessor为同logical action、同continuation/owner/purpose/action、恰好`generation=n-1`且有matching `terminal_confirmed_not_forwarded` receipt时，受控函数才可创建generation n；`predecessor_permit_id`唯一，两个并发successor只有一个winner。Forwarded/unknown使该slot永久terminal；真正下一个业务动作必须由continuation状态机创建新的action slot，不能换manifest绕过。跨行条件用复合FK+deferred trigger/唯一受控函数实现，不能误写成普通CHECK。ACK丢失只按同permit/command/receipt read-back，同键不同result/evidence fail-loud。Control/chain/scope/hard-request writer提交时必须发布gateway revoke barrier；同步不明时gateway fail-closed。Pause/hard/closure的quiescence manifest必须枚举submit permits和captcha effect permits两类集合：所有issued只能在关门线性化前合法dispatch，所有dispatching必须取得terminal receipt或由gateway/holder物理隔离后保守unknown；不能只等TTL或只统计submit permits。只读观察也只能在purpose allowlist内；cleanup/detach不签新effect permit，而由§6.1.1已经存在的operation事实和cleanup-only allowlist授权。这样“prepare后才发生pause/hard request”“转发与消费之间崩溃”与“手机端长时间停留后再点击”都不能穿透关门点或重复输入。

70分钟人工等待不能靠一个长fence TTL，也不能调用绑定已关闭quota reservation和旧verified session的`heartbeat_execution_grant()`。新增专用`heartbeat_captcha_owner()`：每次只续很短窗口，按`control -> assignment -> active chain -> hard-request gate -> worker runtime scope -> browser -> browser health/policy -> context -> fence -> continuation`锁序，要求continuation处于`assist_held/capture_held/suffix_ready`、exact current credential/capability/holder/process/lease/token/boot/context均匹配，control/chain/scope仍normal且三级epoch未变、无live hard request、health ready、DB now早于continuation deadline。成功事务同时CAS fence与continuation owner expiry，递增`owner_heartbeat_generation`，追加新capability receipt及heartbeat receipt并切current pointer；新expiry取短TTL、continuation deadline、health/readiness validity与scope deadline的最小值。旧capability receipt和已签permit的expiry不延长，旧generation heartbeat永远rowcount=0，所以续租不能复活旧点击权限。ACK丢失按heartbeat key和old/new generation整组read-back，不另加一代。

Pause/closing/hard request、scope drain、health/context漂移或DB结果不确定时，`heartbeat_captcha_owner()`不得续normal owner capability或签新permit；既有同owner只由单独cleanup heartbeat在冻结root/permit manifest和短cleanup deadline内取得`capture_settle_only/cleanup_only`，完成已经forwarded root的staging/settlement、context cleanup、detach或quarantine。Cleanup deadline到期仍未收口必须quarantine/physical recovery，不能用反复heartbeat拖住pause/CAN/hard closure。这样合法assist可以用多次短租覆盖70分钟，而zombie holder最多活到最后一个DB确认窗口。

`assist_held/assist_detach_pending`保存准确assist holder/process/lease/token/boot；assist后的`resume_ready_detached/claimed`引用同一owner transition的assist-detach receipt和exact gateway barrier ACK；post-capture显式释放holder时，`suffix_ready -> capture_detach_pending`保持旧capture token且cleanup-only，完成detach/barrier后才token+1转新的`resume_ready_detached(next_action=joint_collection)`并引用capture-detach receipt。`claimed`只是DB协调claim，不更换物理owner。缺字段、phase/token/boot/context/sequence错配、旧ACK延迟到达或前任execution scope未退出时只能quarantine，不能用nullable兼容放行v1。

Credential/detach lineage双向约束：每条detach receipt必须引用该旧holder正在使用的直接predecessor install receipt，并逐项匹配continuation、相邻owner transition、instance/holder/process/lease/token/boot/context/purpose/connection与last-forwarded sequence；每条跨holderinstall receipt必须引用直接predecessor detach receipt及其barrier event/hash，并匹配新transition identity。Predecessor用kind+四个FK做XOR：assist/capture install必须有continuation+detach predecessor；handoff collection install通常有continuation+reservation+detach predecessor；仅同holder/process/context/connection从capture purpose直升collection时，可引用matching same-holder purpose-barrier receipt；fresh collection install必须有reservation，且其root predecessor是匹配同boot/context的recovery seal或上一合法clean browser-release receipt。Initial clean-boot collection install不能无证据NULL。

Purpose barrier本身必须引用直接old install receipt，证明same holder/process/lease/boot/context/connection，记录old/new token和purpose、最后forwarded sequence及barrier ACK；DB先token+1进入`collection_credential_pending(effect none)`，gateway再关闭/排空old capture credential并出barrier，新collection install引用该barrier，最后activation。任一identity/sequence不符或ACK未知则quarantine；不得把“同holder”当作免barrier。三类receipt append-only、FK `ON DELETE RESTRICT`，continuation/reservation上的current install receipt以复合FK匹配instance/token/owner version/purpose；这些current字段只是CAS projection，不覆盖历史。Nested continuation验证自己的直接链，不能只验证最早root。

Handoff 使用 detach-ready 两阶段，避免先 bump token 后 collector 已无法合法 detach：

1. 当前题按真实 disposition结算；suffix reservation item release，operation 仍 not_started 的 request item保持 waiting；关闭 quota reservation。
2. `prepare_captcha_handoff()`按`control -> protocol assignment -> active chain generation -> assignment级live hard-request gate -> worker runtime scope -> browser -> browser health/exact policy -> current context -> fence -> request -> continuation`写`detach_pending`，要求control/chain/scope仍允许该已发送root的有界handoff、无live hard request、health gate ready、boot/context/readiness匹配；context可为精确same holder/reservation且root operation lineage匹配的`owned_dirty`，不能是tainted或其他owner。提交后禁止新submit，但保持collector旧token只允许cleanup。它不得读取context却跳过权威context行锁，也不得在取得fence后回头锁context或assignment。
3. Collector 在事务外停止 effectful 写、从同一线程 detach，并写绑定旧 lease/token/boot 的 durable detach ACK。
4. `complete_captcha_handoff()` 验证 detach ACK后才 token+1，转 `captcha_pending` purpose并释放 local lock。崩溃、ACK 不明或 CAS 失败一律 quarantine；不能让 stale collector 与 assist重叠。
5. `prepare_captcha_assist_credential()`只做无quota、白名单`collector_to_captcha_assist` purpose handoff：使用上面的完整hard-gate锁序校验normal capability、health ready、matching boot/context/readiness、handoff/run/root/token和collector直接detach/barrier/scope-exit lineage；冻结完整member/pending/sequence集合，创建唯一purpose-handoff operation，把context置`owned_dirty_handoff_pending`，随后token+1/owner version+1转`assist_credential_pending(effect_permission=none)`，此时**尚未**把context交给新assist。事务外gateway安装绑定新token/purpose并写install receipt；`activate_captcha_assist_credential()`第二短事务重走同一完整锁序与双向anti-join，写matching purpose-handoff receipt和owner-capability receipt，才原子把context重绑新assist lease/token/holder并回`owned_dirty`、转assist_held。Effectful输入必须另持一次性captcha effect permit。Health suspect/quarantined/recovering、证据不全、任一步失败或ACK不明先按同operation/transition/receipt read-back，无法证明则revoke→quarantine，绝不回旧token。
6. Assist完成输入后不能直接把owner交给resume Activity。若`next_action=joint_collection`（pre-submit），assist holder必须先按§6.9完成durable context cleanup，取得receipt并把context变为clean；若`next_action=capture_root`（post-submit），为保留root页面只允许context继续作为同continuation owned-dirty，随后capture adopt必须引用直接assist detach lineage且仍无submit权限。之后用expected assist lease/token CAS转`assist_detach_pending`，旧assist变cleanup-only；它在同一线程停止写、detach，并取得绑定旧token/boot/connection registry的gateway close/barrier ACK。Cooperative模式还必须证明旧per-attempt execution scope和raw socket已退出；若仍是无法精确终止的共享线程，跨holder handoff不安全，只能quarantine+restart。`complete_assist_detach()`验证物理证据和上述context分支后才token+1，转`session_verification_pending(effect none)`并保存计划next action；不能直接进入resume-ready。失败、cancel、hard pause、超时或ACK不明均quarantine/recovery。
7. Session verifier采用prepare/claim/apply三段，不能在持browser/fence时执行，也不能反序锁durable operation。先用operation key创建或以独立claim-only事务取得typed `session_verifier` operation；外部验证只产生绑定claim generation的evidence。Apply前无锁解析immutable IDs，随后严格按`external identity/quota subject -> account -> binding session -> subject global/mode governance rows -> exact frozen governance transition policy -> continuation -> governance gate effect/session revision/receipt -> governance control operation final claim-token CAS`加锁；即使subject/mode delta为0也读取并锁其old/new snapshot，operation永远最后。逐项比较continuation冻结的subject/binding/stable identity/root session/captcha gate-effect、account current pointer、captcha terminal high-watermark、assist-detach/context cleanup分支证据和current policy；只有专用verifier证明确实恢复同一外部账号session，才以typed gate effect追加新verified session revision+epoch，并在同commit把continuation写入verified session snapshot、`session_verification_pending -> resume_ready_detached(next_action=...)`及operation applied。Policy切换、late captcha terminal或claim过期与apply只有一个winner；commit ACK丢失按同operation/effect/session revision/continuation整组read-back。Binding/rebind漂移或evidence冲突不能把证据挂到新subject/session，只能终结旧handoff并detach/release或quarantine后走普通新grant。

普通acquire看到held_unactivated/held fence都必须拒绝。Assist owner transfer与suffix quota grant是两个不同API：

- Assist/capture adopt都使用prepare credential pending→gateway install receipt→activate held三步，不分配quota，只允许准确assist/capture purpose。Capture prepare只接受来自`resume_ready_detached`的matching`claimed(next_action=capture_root)`并验证直接assist detach/barrier/scope-exit lineage；它使用完整hard-gate锁序并继续锁`request -> request item -> submission operation -> continuation`，要求normal capability、health ready、matching boot/context/readiness，且owned-dirty精确属于root continuation lineage。Prepare创建唯一白名单`assist_to_capture_root` purpose-handoff operation、冻结member/pending/sequence，把context置`owned_dirty_handoff_pending`，再token+1转`capture_credential_pending(effect_permission=none)`，并不提前交给capture holder。Gateway安装后，activation重走完整锁序与双向anti-join，写purpose-handoff receipt及owner-capability receipt，才原子把context重绑capture lease/token/holder并回`owned_dirty`、创建唯一capture generation、转`capture_held`；每个effectful capture动作另签一次性permit。Pending期间禁止capture/attach，失败不回旧token；
- Root capture verified/诚实失败且staging/terminal receipt齐全后，同一holder以capture lease/token CAS把continuation转`suffix_ready(next_action=joint_collection)`。有eligible suffix且保持完全相同holder/process/scope/connection时，可继续持有owned-dirty并等待下面的same-holder owner-transfer joint grant；若要释放holder、跨connection或交给另一worker，必须先由capture holder完成§6.9 cleanup把context变clean，再严格走`suffix_ready -> capture_detach_pending -> resume_ready_detached`并保存cleanup及capture-detach/barrier receipt。无eligible suffix也必须cleanup后走deterministic normal release。`capture_held/suffix_ready`已经改变物理owner，claim/heartbeat过期不得退回普通resume_ready；任何cleanup、detach或ACK不确定都转quarantine/recovery；
- `acquire_execution_grant_from_handoff()`才可恢复collection，并明确区分两个互斥分支。分支A接受已完成session verifier、matching且**context clean**的`claimed(next_action=joint_collection)`，其来源可以是pre-submit assist cleanup+detach，或post-capture cleanup+detach；它只能凭matching cleanup/detach receipt走普通clean-context grant/credential activation。分支B只接受post-capture仍由**完全相同holder session/process/scope/connection**持有、token匹配、context owned-dirty且session verifier snapshot仍current的`suffix_ready`；joint grant commit除正常reservation外还必须按§6.9创建context owner-transfer operation并把context置`owned_dirty_transfer_pending`，随后由purpose barrier与专用owner-transfer activation原子换到successor reservation，不得声称走“normal context clean activation”。两个分支都使用完整短事务锁序`control -> protocol assignment -> active chain generation -> hard-request gate -> worker runtime scope -> region(shared) -> external identity/quota subject -> account -> binding session -> quota config gate -> subject global/mode governance -> exact governance transition policy -> active quota policy/stable scopes/policy-scope revisions -> browser -> browser health/exact policy -> current context -> all buckets -> fence -> request -> predecessor reservation -> request items -> submission operations -> continuation -> new reservation items -> context transfer operation(如需)`，逐项比较root/verified session/current binding；quota config gate必须`open`且state/epoch/current policy等于root/claim与新reservation冻结值，本mode required stable scope集合必须count/hash双向完整、全部`open`且scope gate epoch及policy-scope revision/effective-to匹配；再比较subject/mode治理gate、exact transition/quota policy、health/readiness、claim/token/boot/scope epoch和context分支证据。任一config/scope/global/mode gate、hard request、health quarantine、policy、identity或context漂移仍阻断；不能因captcha已解就旁路。成功时冻结与normal grant完全相同的session/config-gate/scope-gate/governance/policy/health快照，预占quota、token+1并创建`reserved_unactivated`，continuation只到`collection_credential_pending(effect_permission=none)`。事务外安装collection credential；分支A由normal activation复核clean receipt，分支B由专用owner-transfer activation复核purpose barrier及完整member set；成功commit才同时激活reservation、开放collection permission并完成continuation。Pending失败必须释放reserved并revoke/quarantine，不能回旧token、把transfer-pending伪装clean或attach；
- quota=0、任一required scope missing/unverified、account binding漂移或bucket busy时返回transient deferred：pre-submit claimed清claim回原`resume_ready_detached(next_action=joint_collection)`；post-capture suffix_ready保持原state/holder并继续有界heartbeat，或显式detach回handoff。两者都不bump token、不创建半条reservation；
- session continuity要求handoff冻结的quota subject/binding/root session/verified successor session/正式account/browser与新grant全部一致。Admin rebind/drain把所有上述live continuation状态计入release blocker；未终结handoff不能cutover。若策略改选其他账号/browser，必须先安全终结旧handoff并detach/release或quarantine，再走普通grant；不得拿旧账号captcha证据或会话却扣新账号quota。

Continuation claim的精确语义：resume Activity先用独立claim-only事务CAS`resume_ready_detached -> claimed`，验证旧owner detach/barrier receipt并写单调claim generation、随机token、holder session、next action和短expiry。后续capture adopt或joint grant只接受完全匹配且未过期的`claimed(holder,token,generation,next_action)`；prepare成功后进入相应`*_credential_pending`，只有install receipt+activation后才capture_held或completed/collection active。Pre-submit joint grant的quota/control失败若发生在token/owner改变**之前**，同一事务清claim回resume_ready_detached；capture prepare同理。一旦pending commit，fence token/owner已经改变，任何失败都不能清claim回旧状态，只能read-back、完成activation或revoke→quarantine并结算reserved。Commit outcome unknown必须先按claim token/owner transition/read-back：看到pending恢复同一credential operation，看到held/completed恢复同一capture/grant，看到同claim仍claimed可重试prepare，看到resume_ready_detached按pre-transition deferred处理，绝不能直接建新generation。只有仍处于claimed且证明不存在pending transition/capture attempt/reservation/quota effect或fence token/owner变化时，claim-expiry reconciler才能CAS回resume_ready_detached；credential_pending/capture_held/suffix_ready expiry一律按物理owner不确定进入quarantine，不得让第二worker并行adopt。

合法转换为：

```text
collection held
-> detach_pending(same token, cleanup-only)
-> captcha_pending(new token)
-> assist_credential_pending(new token, effect none)
-> assist_held(same token, install receipt activated)
-> [pre-submit: context cleanup -> clean]
-> assist_detach_pending(same assist token, cleanup-only)
-> resume_ready_detached(next_action, new handoff token + barrier receipt)
-> claimed(same handoff token, DB claim only)
   -> collection_credential_pending(next token + reserved grant, clean context, effect none)
      -> collection held(same token + install receipt + activated grant)
   -> capture_credential_pending(next token, no quota, effect none)
      -> capture_held(same token + install receipt)                   # post-submit root
         -> suffix_ready(same capture token)
            -> same holder: collection_credential_pending + context transfer pending
               -> collection held(same token + barrier/install + transfer activated)
            -> other holder: context cleanup -> clean
               -> capture_detach_pending -> resume_ready_detached     # 显式释放后等待quota
         -> context cleanup -> completed -> deterministic release     # 无 suffix
```

Collector 的 stale finalizer只能得到 rowcount=0，不能释放新 handoff。任何 capture/assist写动作也要验证 current lease/gateway connection credential，不能只靠后台 heartbeat。

上述规则适用于所有跨holder owner change：DB token+1之前必须有旧holder detach + exact gateway forwarder/connection barrier ACK；无gateway时必须有可信detach且旧execution scope/socket已物理退出，否则quarantine/restart。Gateway credential issuer在看到DB新token时还必须验证该token transition引用有效direct predecessor receipt，不能让延迟到达的barrier event在新credential已开放后才生效。Capture→collection仅在**完全相同holder session/process scope/context/connection**时可走显式same-holder purpose-barrier优化，但仍严格经过`collection_credential_pending(effect none) -> purpose barrier receipt -> new install receipt -> activation`；不满足则统一detach handoff。任何情况下旧capture purpose与新submit都不能并行。

Post-submit captcha 不得为 root 题自动创建新 quota reservation或重发。已 accepted 的 root quota item先 settled_consumed、unknown root settled_unknown，header 可以 terminal；request item 的 result projection 进入 `capture_pending`。Assist 后用 `capture_continuation` purpose 创建新的 `collection_capture_attempt`，保存新 capture lease/token/boot，成功后写 staging/capture effect；绝不把原 quota item从 terminal改回 captured，也不覆写 dispatch provenance。Unknown root 不自动重发；late capture若证明 accepted按 §6.7 写 adjustment。只有人工明确批准的新 submission generation 才能再次提交并形成新 debit。

Pre-submit captcha 若发生在 `begin_submission` 前，assist 后复用原 not_started operation、只建新 grant generation；若已 dispatching 且原 holder可证明 submit 未调用，则原 operation terminal_not_sent，按策略创建新 submission generation。Root capture/结算完成后，suffix 必须经 `acquire_execution_grant_from_handoff()` 另行申请完整 quota grant。

当前 workflow 会从 captcha 根题本身重采，因此 `collection-execution-grant-v1` patch 在 captcha 分支是强制项。`CaptchaPause` 末尾新增带默认值的 `continuation_manifest_pub_id/root_request_item_pub_id/root_operation_pub_id/root_reservation_item_pub_id/disposition/resume_slice_hash`。Assist 正常超时且能确认 detach 时可以 normal release；只有 ownership/detach 不确定才 quarantine/restart。

### 9.6 直接 CDP 路径清理

全仓搜索并处理所有：

```text
connect_over_cdp
resident_cdp_url
GEO_*_CDP_URL
browser_lock
acquire_browser_fence
```

生产可执行的 probe、drill、OTP、captcha、维护工具必须：

- 在有 gateway 时同时取得 purpose 明确的 lease并经 gateway，二者缺一不可；
- 在尚无 gateway 的 cooperative 阶段，只有持有效 purpose lease 才允许 direct CDP；发生失租、异常接管或 ownership 不确定后必须 quarantine+restart；
- quarantined browser 的检查只能由 privileged recovery role 在 `recovering` 状态和 expected quarantine token/boot 下执行，不能借健康 probe 绕过 lease；
- 无法满足上述条件时在 production mode 明确拒绝运行。

Maintenance/captcha/OTP/recovery lease 只允许 purpose 对应动作，不分配 collection quota，也不得调用 `begin_submission()` 或公共 submit guard。任何 handoff adopt 失败、token 不符或 mobile input strong check 失败都必须停止写入并 quarantine；不能退回匿名 direct attach。

Enforced production 的正式账号不得使用“未配置 resident CDP 就临时 launch 一个新浏览器”的旧 fallback 绕过绑定/fence。Ephemeral launch 只允许显式隔离的开发/测试资源，且仍不能绕过 quota/submission ledger。

特别审计已有直接 attach 的 probe/drill 脚本。不能只修五个主 adapter 后留下旁路。

## 10. Temporal 兼容策略

### 10.1 保持现有 Activity 名称可执行

优先让以下现有 Activity 内部调用新 coordinator：

```text
collect_doubao_batch
collect_deepseek_batch
collect_tongyi_batch
collect_yiyan_batch
collect_yuanbao_batch
collect_with_adapter
```

Activity 内部变化本身不改变 workflow command history，但它可能返回旧 workflow从未处理过的 typed deferred/captcha envelope。Activity 名称相同不等于协议兼容。上述名称用于 fresh v1 execution 和 completed v0 history 的 Replayer 兼容；contract/enforce 后不得让任何 pre-marker/v0 非终态 workflow领取并执行真实 v1 Activity。所有这类 workflow 必须先按 §10.5 终止、结算或迁移清零。

### 10.2 稳定幂等身份

从 Temporal Activity info 取得：

```text
workflow_id
workflow_run_id
activity_id
attempt
```

Batch request key 不包含 attempt，因为 attempt 2 必须复用 attempt 1 的逻辑 request：

```text
execution-grant-v1:{workflow_run_id}:{activity_id}:{chunk_ordinal}
```

Contract hash 包含 adapter、规范 region、mode、ordered business keys、query hashes、协议版本。相同 key 但合同变化必须 non-retryable。`chunk_ordinal` 来自原输入稳定 start ordinal，不是“本 attempt 第几次成功拿到 grant”。

Request key 标识一个稳定 Activity 输入 segment；同 request 内由数据库分配串行 `grant_generation`，用于 partial grant 后缀、pre-submit rollover，或旧 generation 已安全 terminal且 operation仍 `not_started` 后的资源安全重选。ACK/响应丢失本身绝不是创建新 generation 的理由：caller 必须先以同一 request、holder/acquisition identity read-back原 generation；只有查明旧 generation 已按状态机安全终结、所有权已隔离且 operation未发送，才能在 request行内创建下一代。Grant generation 不进入 Temporal idempotency key，也不得被误当作外部发送可以重试的证据。Captcha/continue-as-new continuation 必须从 manifest 显式传回原 request/request-item ID，不能按新 Activity ID 复制同一 operation。

题级 logical operation key 还必须独立于 workflow run/activity，建议基于：

```text
tenant + business run + canonical business_key + platform + adapter + region + mode + submission_generation
```

这样 continue-as-new、captcha 新 Activity 或 workflow 恢复不会把旧 operation 当成新题重发。Temporal request identity 用于恢复 batch 协调；operation identity 才是外部发送去重真源。

### 10.3 Dataclass 兼容

新增字段必须放在 dataclass 末尾并有默认值。例如 result 可增加：

```text
reservation_pub_id: str | None = None
reservation_item_pub_id: str | None = None
execution_request_item_pub_id: str | None = None
submission_operation_pub_id: str | None = None
platform_account_pub_id: str | None = None
submission_disposition: str | None = None
submission_generation: int = 0
capture_attempt_pub_id: str | None = None
result_staging_pub_id: str | None = None
result_content_hash: str | None = None
terminal_manifest_staging_pub_id: str | None = None
task_materialization_command_pub_id: str | None = None
```

这些字段不是提示性元数据。`persist_collection_result()`只能消费matching materialization command，并绑定Activity结果明确携带、且DB/CAS读回验证同operation/generation/hash的那一版staging；不得在persist时查询“latest”替代，因为并发late capture可能已经把projection推进到另一代，与workflow history中的answer不一致。Success answer必须三项capture/result字段齐全，neutral/wall必须有terminal manifest staging；persist policy要求Task时必须有command ID，suppress时必须read-back suppression receipt。

`CollectionBatchInput` 末尾增加带默认值的 `execution_protocol_version/execution_request_pub_id/continuation_manifest_pub_id/resume_slice_hash`；result 同样携带 protocol version。首次 v1 执行由 workflow input中的 frozen run assignment建立 request；patched workflow 因 pause/captcha/受控 deferred 重新 schedule 新 Activity 时，必须显式传回原 request/manifest ID，不能因新 activity_id 创建第二套 operation。

Workflow顶层`GeoCollectionInput`末尾也要增加兼容默认字段（必须进入每个新Workflow run的`WorkflowExecutionStarted` payload，不能只塞进batch Activity input）：

```text
execution_protocol_version: int = 0
protocol_assignment_pub_id: str | None = None
execution_policy_hash: str | None = None
expected_worker_release_pub_id: str | None = None
expected_worker_release_contract_hash: str | None = None
workflow_definition_release_pub_id: str | None = None
workflow_patch_set_hash: str | None = None
workflow_versioning_behavior: str | None = None
compatible_workflow_definition_release_set_hash: str | None = None
workflow_routing_revision_pub_id: str | None = None
workflow_routing_member_set_hash: str | None = None
expected_workflow_task_deployment: str | None = None
expected_workflow_task_build_id: str | None = None
workflow_chain_generation: int = 0
chain_intent_pub_id: str | None = None
chain_intent_version: int | None = None
chain_intent_nonce: str | None = None          # DB仅存hash，普通日志必须redact
continuation_manifest_hash: str | None = None
chain_input_contract_hash: str | None = None
waiter_transfer_pub_id: str | None = None
waiter_transfer_contract_hash: str | None = None
```

Initial generation 0的intent字段为空，但definition release字段及`workflow_routing_revision_pub_id/member_set_hash`必须由frozen assignment填充；v1 CAN successor的chain intent、definition release和routing revision/member字段全部必填；只有history-budget parked CAN填写waiter-transfer两字段。`prepare_*_continue_as_new` Activity结果也必须显式返回这两个routing字段（或含它们的canonical routing envelope），并把它们纳入next-intent/input contract hash preimage；Workflow sandbox不查DB，必须用payload字段本地重算hash后才调用CAN。Predecessor workflow只能从已prepare的DB intent/transfer结果构造下一次Continue-As-New input，不得自行生成nonce/version/hash、选择current build或删减compatible-set/routing contract。Workflow-start outbox只能从DB assignment填充首代字段，并按§6.1.1在Temporal start前校验、start后写receipt。Successor bootstrap Activity把input逐项与intent/generation/routing revision/member及可选transfer行比较，同时从Temporal describe/history验证actual run的start cause确为Continue-As-New、continued-from run等于predecessor、input payload/hash和actual Workflow Task deployment/build一致；Reset/Retry即使复用了旧started payload/nonce也因cause/run关系不符被拒绝，abort/rearm后的旧nonce因version/hash不符被拒绝，错误build不得借Activity poller匹配获得effect权限。

Temporal workflow sandbox不能查询PostgreSQL：workflow入口只做确定性的字段完整性/内部hash自洽检查，不能宣称与DB compare。首个及每个execution Activity必须在任何grant/attach/CDP/外部副作用前按`run_pub_id`读取DB assignment与control最低版本；gen0再校验initial start receipt+active chain gen0，CAN successor先用独立bootstrap Activity绑定intent，后续execution Activity校验actual Activity run ID对应的active generation及其input/manifest hash。若确需workflow主动查DB，只能在对应的、已登记且不可复用的patch branch调无副作用validation/bootstrap Activity。Deadline具体数值仍以DB assignment/service clock为真源，history中的policy hash只用于检测漂移，不能授权调用方改deadline。

当前`collect_with_adapter`的per-task `CollectionTaskInput`拿不到外层`tenant_pub_id/run_pub_id`，不能直接生成run-scoped operation key。必须在dataclass末尾加兼容默认字段，或新增只供v1的wrapper（字段语义不得减少）：`tenant_pub_id/run_pub_id/execution_protocol_version/execution_request_pub_id/execution_request_item_pub_id/continuation_manifest_pub_id/original_ordinal/request_contract_hash`。Patched v1 workflow从已冻结的`GeoCollectionInput/CollectionBatchInput`和DB assignment显式构造这些Activity参数；Activity逐项compare，禁止回查可变“当前run”或用空tenant/run生成key。v0 completed payload仍可反序列化，但enforce下字段缺失fail-closed。

Captcha新Activity的input通常只是原segment子集，不能拿子集重算后与原全量request contract直接比较。它先验证assist detach/barrier receipt，再用manifest ID+slice hash CAS`resume_ready_detached -> claimed`，验证输入ordered key/query hash恰等于continuation item的eligible slice，再映射回原request item/operation。Activity只返回该slice的等长结果，workflow按original ordinal与已完成prefix合并。Claim ACK丢失按manifest claimant session read-back；nested captcha新建predecessor manifest。

新 enforced execution 必须具备这些字段，并与 DB run assignment逐项比较；v1 payload缺失时 fail-closed。只有 DB assignment明确为 0 的 completed history 才进入 legacy反序列化/回放分支，不能由字段缺失自动判定 legacy。

### 10.4 Workflow patch 使用范围

`collection-execution-grant-v1`只包住首次发布时已经冻结的captcha resume/root、pause deferred/wait及execution-grant基线command trace。**生产出现首条带该marker的history后，任何新增unknown分支、Activity、timer、wait、signal、CAN、cancel或结果解释变化都必须使用新的唯一patch ID，不能继续扩写这个marker。** 每个patch至少添加以下真实Replayer/rolling测试：

- 保存该patch出现前、出现后的真实history，并覆盖history中patch marker absent/present两种路径；
- 用当前release和候选release分别replay所有仍可能被reset/retry/reopen或尚未超过保留期的history；断言无nondeterminism且command/event序列符合registry冻结trace；
- 候选release首次非replay执行只记录自己的新marker，不补写、改义或删除旧marker；同一patch ID不同代码语义由registry hash/CI拒绝；
- 直接覆盖“已有v1基线history→v1后续build”，而不只测v0→v1；至少包含Activity result已进history但下一Workflow Task未提交、parked wait、captcha、normal/cancel、hard-abort和CAN边界；
- 普通continue-as-new前无live reservation/handoff/waiter泄漏；history-budget分支恰有一条prepared waiter transfer。两者都已有下一authorized chain intent，新run bootstrap绑定actual run ID并按需接管waiter；
- rolling环境同时启动old/new Workflow Task worker并制造sticky eviction、worker crash和non-sticky replay。无经验证version pin时，两边definition都必须能replay全部活history；有pin时，Temporal describe/history必须证明旧run始终到旧compatible deployment/build，新run才到current/ramping build；
- `deprecate_patch()`或删除旧branch不属于普通清理。只有visibility+archive+reset/retry权限清单证明再无任何可重放的pre-patch history，且离线archive corpus仍由独立legacy replayer保留时，才能另写ADR和分阶段发布；证据不足就永久保留。

建立代码内静态patch registry和CI：patch ID全仓唯一；release manifest冻结`workflow_definition_release_id + patch_set_hash + source digest + SDK version`；CI拒绝删除/重复/改义patch，拒绝未附pre/post history fixture的command-sequence diff。参考已有`tests/workflows/test_collection_mode_segment_patch.py`和Replayer风格，但不能只靠mock `patched()`返回值。

Worker Versioning若要替代某个patch，必须另过capability gate：记录Temporal Server版本、`temporalio==1.15.0`实际API、deployment/build注册、Workflow Type的PINNED/AUTO_UPGRADE行为、sticky/non-sticky routing、Activity routing、CAN successor升级规则及回滚可达性。PINNED必须证明整个单run只由同一兼容version处理；AUTO_UPGRADE仍强制patch。任何一项未证明，继续采用unique patch方案并保持采集停止。

### 10.5 不能混跑旧 worker

当前没有可依赖的 Worker Versioning 安全隔离，旧 worker 能绕过 reservation，并且失租只打日志。Enforce 的硬策略固定为：**数据库中 v0/pre-marker 非终态 workflow 数必须为 0**。Worker Versioning/新 queue仍用于路由 fresh v1，但不能替代这条清零门槛。在证明版本路由和 inventory 清零前，不得新旧 worker 混跑并打开 enforce。

发布步骤必须具体到 run assignment、queue/build 和每条旧 history：

1. 在 pause 状态把所有 run显式回填/冻结为 v0 assignment，导出全部非终态 workflow、scheduled/started Activity、attempt、activity/task queue、worker deployment/build identity、最后发送证据；清单与 DB/Temporal 时间点存入验收记录。
2. Completed v0 history只用于 Replayer/查询兼容，不再执行新命令。对任何仍非终态的 v0 workflow，不论此刻是在 timer、signal wait、scheduled Activity 还是 captcha wait，都不能仅靠“换成新 Activity 实现”继续。
3. 能证明 Activity从未 started且没有 submit side effect 的旧 pending工作：先 terminate/cancel原 workflow并写 closure审计，再创建带 predecessor/supersedes link、明确 version=1 的新 run/workflow，从原稳定 business item重新开始；不能原地翻 protocol version。旧 workflow不能消费 v1 typed deferred。
4. 已 started、attempt>1、worker失联或发送边界不明的旧工作：先停止原 workflow，按现有证据将已发送部分人工/保守落 accepted或unknown，未发送部分经业务审批迁入新 v1 replacement；未经对账不得自动重发。Captcha/continue-as-new同样逐 item迁移，不按整个 batch猜测。
5. 反复 inventory 直到 Temporal 与 DB 都证明 v0非终态=0、旧 queue无 pending/started collection task、v0 workflow-start outbox无 pending/claimed。旧 worker之外，所有能创建run/assignment/start-outbox的API、scheduler、cron、CLI和outbox consumer也必须已停止或升级到protocol-aware build，旧DB role的v0写权限随后撤销。不得保留一个“新代码 drain 旧 queue 并真实采集”的后门；旧 history兼容只通过离线 Replayer和已完成结果查询验证。
6. 同时枚举并停用所有绕过DB start-outbox的v0 Temporal生产者：server-side Schedule、Workflow Cron、workflow retry chain、未纳管continue-as-new自动链和可对completed v0执行Reset/Retry的操作入口。旧Schedule/Cron必须pause/delete并保存server receipt；旧retry链终止后建立显式v1 replacement。Temporal namespace RBAC撤销普通用户/服务的直接`ResetWorkflowExecution`/Retry能力，只允许受控代理先校验DB assignment/control并创建审计过的v1 replacement；completed v0不得原地reset成nonterminal。直接Terminate权限也必须撤销，所有正常hard terminate经§6.1.1的durable hard-request/assignment gate、准确actual-run RPC及physical/item manifest协议；RPC可提前协作停止，但chain完成仍强制等待物理隔离和settlement。V1 Continue-As-New只允许走authorized chain intent。
7. 新queue/Worker Versioning只接frozen assignment=1的fresh workflow。记录artifact release/deployment/version/routing rule、compatibility set、expected digest/config与实际boot/poller identity；worker领取后再次compare assignment/control最低版本，不匹配即无副作用拒绝。旧queue永久不配置生产poller，并对任何新poller registration立即告警。
8. Enforce后持续把Temporal visibility与DB assignment对账；任何新出现的v0/pre-marker nonterminal、旧queue task、未授权reset/retry/schedule tick都立即触发global pause/emergency、无副作用terminate/quarantine和审计，绝不能等它自然执行。
9. Shadow worker不得轮询任何生产collection queue；使用独立queue、fake activity或离线重放。Enforce前投递无副作用v1 probe，证明只有expected approved release能领取，并用负向probe证明旧queue/build/digest不能执行。

### 10.6 Retry policy

有 item ledger 和 staging 后，Activity 可以 retry，但 retry 的意义是“恢复状态和继续未开始项”，不是重发 dispatching/accepted 项。

在 ledger 完成前，应把 post-submit unknown 类错误列为 non-retryable，禁止现有 batch `maximum_attempts=2` 把整段重新发送。

Pause/busy/暂时无容量不能靠Activity自动retry热循环。定义typed`collection_execution_deferred`（包含request ID、durable waiter ID/resume generation、slice hash、reason、not-before domain/value、observed control epoch，且不含bearer token）：Activity先durable finalize/release local lock并登记§6.3.1 waiter，再以non-retryable application outcome交给patched workflow；workflow捕获后用durable signal+有界watchdog wait，新Activity必须先claim waiter才能恢复原request。它在Temporal UI可见为受控等待，但不得创建失败`CollectionTask`。若选择batch envelope而非exception，也必须用Replayer证明旧/新payload兼容和命令序列稳定。

## 11. Admin、scheduler 和 global pause

### 11.1 账号管理

账号patch必须使用expected version，但不同字段不能都从account锁起步。以下修改在live grant时默认409：

- browser rebind；
- region rebind；
- platform/phone identity 变化；
- quota 下调到 `debited + reserved` 以下。

如确需修改，先进入 drain，等待或显式撤销 grant；撤销后 unknown item 仍保守扣额。Outcome 永远使用 grant snapshot，不随绑定变化。

接口锁图固定如下，外部probe/restart都在事务外：

- region rebind：先无锁解析old/new region ID、immutable target proposal和已有operation ID；若需要外部验证，先以独立claim-only事务领取operation并在无业务锁时执行。每次prepare/apply/cutover短事务固定`control(如需) -> old/new collection_region按ID排序 -> old/new identity按ID排序 -> account -> source/target binding-session rows(实际触及时) -> browser/health/context/fence集合(实际触及时) -> rebind operation final expected-version/claim-token CAS`，锁后重读header current binding revision的region，漂移则409/retry。Target revision只能在该受控apply事务内按确定key插入/read-back，不能在operation锁前另开一个未纳管写窗口。随后按统一drain/cutover状态机切revision，不原地改region。不得account→region；region health flip与grant使用相同region→identity→account前缀；
- relay probe prepare/claim/apply：prepare只锁单一region分配observation generation后立即提交，claim固定`region -> attempt -> claim`，HTTP在锁外；apply固定`region -> attempt -> claim -> receipt/event`，manual override同样先region。它们不锁identity/account/browser，grant的region共享锁后才进入identity/account，因此无region↔identity/account反向边；
- browser rebind：apply前只无锁解析old/new identity、account、session、browser、context、fence和operation的immutable ID，不据此作授权结论；随后按`old/new external identity按ID排序 -> account -> source/target binding-session header/revisions按ID排序 -> old/new CollectionBrowser按instance key排序 -> 全部browser-health/exact-policy/current-context rows按browser/context排序 -> old/new BrowserFence按instance key排序 -> rebind operation final expected-version/claim-token CAS`一次性取得集合锁。Prepare若只追加prepared immutable binding/session snapshot并关闭account grant gate，可以跳过确实不读取/不修改的后序集合，但operation仍最后CAS，且该事务随后不得反锁browser；需要验证browser/session或最终cutover时必须走完整序。Drain把live reservation/permit以及所有captcha/handoff continuation算release blocker。最终cutover要求target session current revision=`verified`且evidence完整/未过期，target health=`ready`、boot/context/current readiness receipt与binding evidence匹配、context clean、无live browser recovery/handoff，两个fence均free/clean，且目标browser不存在另一verified/draining binding；再原子retire old binding+session、verify target revision、切current pointer和写completion receipt。不得原地UPDATE snapshot，也不得锁old browser→old fence后再锁new browser/health；health quarantine/recover与cutover只有一个winner；
- quota patch/policy activate：apply前无锁解析immutable IDs，短事务按`external identity/quota subject -> account -> quota config gate -> subject governance(实际触及时) -> old/new policy -> stable scopes/policy-scope revisions按ID -> 全部重叠或受影响buckets固定序 -> quota cutover operation final expected-version/claim-token CAS`，锁后复核account expected version/current binding revision仍指同subject；有限quota下调到exposure以下只写受影响scope blocked/audit，不破坏counter，也不把整个config gate永久堵住。Policy永远挂subject，rebind不能借机复制/切走当前bucket；
- platform/phone/canonical identity验证或变更：受控verifier在事务外按platform identity policy从会话证据提取official stable subject，并只解析immutable target/region ID；不得跨事务保留任何xact advisory lock。Apply事务严格先锁old/new相关region行（ID排序），再对stable-subject hash及全部alias hash按规范key取得identity-claim advisory xact locks，随后锁old/new`collection_external_platform_identity`（ID排序）→account→source/target binding-session rows排序→全部browser排序→全部health/exact-policy/current-context排序→全部fence排序→identity/rebind operation final expected-version/claim-token CAS。尚无业务region的新identity必须先锁专用的稳定`identity_region_unassigned` sentinel行；本事务不能跳过step 5后又补锁真实region，后续绑定真实region另走region-first操作。Verify执行`(platform, stable subject hash)`全局unique、alias→subject FK、`external_identity_id` verified/draining-binding partial unique及resident-browser partial unique；创建verified target还必须核对session evidence与browser readiness/boot/context，调用方不能选择scheme。任何冲突都把候选置conflicted并返回明确409，不靠scheduler去重。若同时触region/browser，使用这条最长前缀；复杂换绑通过append-only revision与`rebind_pending/draining` operation分阶段完成，不在一个事务任意锁多个账号/实例。

§7.1“一事务一个instance”对正常grant仍成立；唯一例外是admin rebind的prepare/apply/cutover短事务，它必须依次锁region(如需)→identity→同一account→全部session rows→全部browser rows→全部health/policy/current-context rows→全部fence rows，最后才以claim token/expected phase锁并CAS同一rebind operation，各集合内部稳定排序。Operation可在独立claim-only事务先领取，但claim事务不能再拿业务锁；apply绝不能先持operation再等待session/browser。长时间drain不持任何这些锁。禁止跨两个account做原子swap；使用drain后的分步受控操作。Identity/binding/rebind事务不得创建或重置quota/governance policy/bucket；它们只追加binding/session revision并原子切current pointer。必须以真实PG并发覆盖cutover×operation reclaim×browser health recovery三向竞态，证明没有环、旧claim无法提交、且任一winner后另一方只重读或安全重试。

### 11.2 浏览器管理

- release/revoke 必须 expected lease/token CAS；
- restart 改为 durable outbox：quarantine → privileged restart → boot ID verify → health probe → recover；
- UI 展示 holder、purpose、token、lease age、heartbeat、reservation/run、quarantine reason；
- 不提供“清空 fence 行”按钮。

### 11.3 Scheduler 和 run-now

- scheduler可在锁schedule前无锁读取global pause做早期跳过，但这不产生授权；Fresh v1是否允许mint Run的权威线性化点是下述launch-evaluation事务，Activity grant仍是发送前最终授权；
- Scheduler先在schedule自己的锁域只确定immutable scheduler tick、series、business occurrence group/origin-intent/required-member IDs并释放schedule锁；自动producer先按`series_key`合并到唯一pending attempt，再调用统一`evaluate_and_mint_collection_run(launch_key)`。函数在事务外只从DB解析各member current candidate-pool revision的**完整候选ID集合**；这一阶段不得做账号登录、HTTP、CDP或其他外部I/O，也不得据缓存选winner。随后以短事务固定按`series/launch key advisory -> control -> launch series/current pending attempt -> required regions去重排序 -> candidate-pool headers/current revisions/members排序 -> 全部候选external identities/quota subjects跨member去重排序 -> accounts -> binding sessions -> quota config/global+mode governance/policies/stable scopes -> browsers -> health/exact policy/current contexts -> current buckets -> campaign -> formal legs/primary slots -> origin intent -> launch attempt/evaluation final -> Run -> protocol assignment/items -> gen0/start operation/outbox`加锁并全部重读；pool pointer/epoch/member hash漂移或checked-at current bucket集合变化则从入口安全重试，不得沿用残缺候选集或后补锁。它对每个platform/region/mode/formal-leg的全部候选要求region current与immutable projection event一致且derived ok/fresh，并逐候选检查verified且不冲突的正式binding、current session verified且未过期、quota config及本mode required scopes open/current/有可用量、subject/mode governance允许、browser health ready、current context clean、readiness有效；每个member从全门禁eligible候选中按冻结rank+stable ID选择winner。只有某member全部候选失败才把该member blocked；任何required member失败时整次只提交幂等blocked evaluation及确定性reason，**不mint/freeze配置、不建Run/assignment/gen0/start operation/outbox**。Run-now返回同一attempt/reason；绝不走env/旧adapter fallback；
- Launch snapshot不是opaque摘要：每个formal-leg/mode member及其required quota scope、DB-now current day/week/year bucket都写规范行，冻结region event、binding/session/gate、governance、browser/context/readiness，以及bucket ID/边界/baseline source+state/blocker/limit/reserved/debited/available/ledger version；四级count/hash均从这些行的固定序列重算并做双向anti-join。相同事实只能read-back同evaluation；quota release/reset、limit/baseline/blocker变化必须因bucket ledger version和规范行变化得到同attempt的新generation。全member eligible才允许消费；豆包normal或deep_think任一腿缺正式绑定时整次主计划零Run，不能把另一腿先建出来；
- Eligibility与Run mint同事务受region/health/context等writer行锁线性化：writer先赢则本次blocked且零Run；mint先赢则至多该一个Run在翻转前合法提交，随后Activity最终gate阻断发送，后续相同occurrence tick只read-back已consumed attempt而不再造Run。相同blocked snapshot的多tick/双击只read-back一条evaluation；恢复后snapshot变化才可在同一attempt追加新generation并恰好消费一次origin intent。不要在持有schedule锁时再锁control/region/account/campaign；
- `run-now` 同时补客户端 `Idempotency-Key`，避免双击创建两个真实 run；
- 普通 run 创建的 unique 冲突要 reload+contract compare，不返回 500；
- campaign launch plan先为每个formal leg冻结权威primary slot及带完整leg成员的canonical-main origin intent；scheduler创建主计划Run只能消费该intent。普通run-now/top-up创建明确supplemental origin intent，即使更早提交/完成也不得占primary slot；UI若要手动执行主计划，必须使用slot授权intent的专用幂等入口，不能把通用run-now改名为main；
- scheduler/run-now在创建assignment时从稳定sampling campaign/policy/origin intent冻结run execution item set、formal-leg role和segment manifest；受控函数逐项比较intent-leg membership、slot revision、lineage/version/occurrence/run class。Primary失败只可经audited slot replacement授权新intent，不能按创建/完成先后、样本数或锁竞争重判；同run同formal leg的多个mode segment共享一个primary/supplemental role；
- canonical launch-plan、audited replacement、generic run-now/top-up使用不同DB role/function。Scheduler普通身份没有创建canonical-main intent、切换slot revision或直写role的权限；专用主计划入口也只能CAS消费已经冻结且授权给该schedule occurrence的intent，不能现场造一个“main”身份；
- reservation、grant、Task、run resolution、capture revision或mode segment数量绝不能参与formal-leg primary识别和sampling expected/observed cells。Current policy中豆包normal补采/deep_think映射同cell；genuine dual-mode才按verified mapping拆formal legs。

### 11.4 Pause 状态机

建议：

```text
open/draining -> pause_requested -> paused
paused -> open（epoch+1）
任意 -> emergency
emergency -> paused（仅privileged recovery，epoch+1）
```

Pause API 事务只更新 control（epoch+1）和 durable pause operation/signal outbox，不在持锁期间调用 Temporal 或浏览器。接口语义必须明确：

每条 pause/cancel signal outbox 必须绑定 `dispatch_control_operation_id + control_epoch + target holder session + expected lease_id/fencing_token/browser_boot_id + command_id`。Activity、gateway 和 supervisor 接收端在执行前比较当前 epoch/lease/token/boot并写幂等 ACK；resume 已把 epoch 推进、lease 已换代或 holder 已结束时，迟到 signal 只能记录 audited stale no-op，不能取消/释放新 owner。Outbox claimant 服从 §7.1 两阶段 claim/apply，不能先持 signal 行再锁 control/fence。

- 首次请求和仍在排空时返回 `202 Accepted + operation_id + state=pause_requested`；可提供有界同步等待，超时仍返回 202；
- 只有 reconciliation 完成下列证明并提交 `paused` 后，查询/接口才返回 200 paused，UI 不得提前显示“已暂停”；
- 重复 pause 使用幂等键/expected epoch，不制造多个相互竞争的 pause operation；
- pause_requested到达的未开始Activity返回带durable waiter的typed deferred，patched workflow进入signal+watchdog wait；不写`account_quota_unreserved`/failed task，不靠无界Activity retry热循环；
- resume提交`open + epoch+1`时同事务写epoch-change outbox；它幂等唤醒所有观察旧epoch的waiter。Parked workflow即使没有holder也会经signal或watchdog重新调度，resume Activity claim winner携原request/slice重评；pause→resume不能永久丢题、并行推进同request或改变batch顺序。
- 首版禁止 `pause_requested -> open` 直接反悔；先完成 quiescence进入 paused，再显式 resume。否则一部分 holder 已停止、一部分仍持旧 permit，语义难以审计。
- emergency绝不能直接转open，也不能靠裸UPDATE脱困。只有durable privileged recovery operation以expected emergency epoch/state执行，证明全部holder/fence/permit/live grant/handoff为0、所有tainted context已有reset/restart receipt、recovery/outbox backlog=0并保存quiescence snapshot后，才能CAS`emergency(e) -> paused(e+1)`；随后另一次正常resume才可open。Recovery commit/ACK丢失按operation+epoch read-back，旧emergency operation在新epoch只能stale no-op。

从 `pause_requested` 转为 `paused` 的证明至少包括：

1. 没有新 grant；全部 submit permit 已过期或由匹配 holder 证明未执行，或者 gateway pause barrier 已使其 holder connection 无法再转发 effectful message；
2. 所有 dispatching item 已转为 accepted/released/unknown 的 durable 状态，不存在悬空发送窗口；
3. 所有 collection/captcha holder 已 ACK stop+detach，或者对应 fence 已 quarantine 且 gateway/supervisor 给出物理隔离证据；仅 holder ACK 不足以证明 raw CDP 已断开；
4. unsent reservation 已 release，accepted capture 已完成/诚实终止并 staging，handoff 已安全停驻或 quarantine；
5. pause operation 的 quiescence snapshot、control epoch、匹配 token/boot 和审计事件已持久化。

`draining` 允许已 accepted 的 capture/结算自然完成；`emergency` 请求立即 revoke/quarantine。无论哪种模式，已经转发的外部命令都不能宣称被撤回，只能按 accepted/unknown 结算。

## 12. 代码落点

实施前重新搜索当前树，但至少审阅和修改：

```text
api/geo_platform/collection/account_models.py
api/geo_platform/collection/models.py
api/geo_platform/collection/account_governor.py
api/geo_platform/collection/leases.py
api/geo_platform/collection/account_admin_router.py
api/geo_platform/collection/schedule_router.py
api/geo_platform/collection/router.py

workflows/activities/browser_router.py
workflows/activities/resident_browser.py
workflows/activities/collection.py
workflows/activities/captcha_assist.py
workflows/activities/doubao_adapter.py
workflows/activities/deepseek_adapter.py
workflows/activities/tongyi_adapter.py
workflows/activities/yiyan_adapter.py
workflows/activities/yuanbao_adapter.py
workflows/definitions/collection.py
workflows/workers/main.py
workflows/workers/outbox.py 或新的专用 worker

tools/resident_browser.py
所有直接 CDP probe/drill 工具
migrations/versions/<基于真实 head 的新 revision>.py
```

推荐新增小而清晰的模块，避免继续膨胀 `account_governor.py`：

```text
api/geo_platform/collection/execution_grants.py
api/geo_platform/collection/quota_buckets.py
api/geo_platform/collection/region_health.py
api/geo_platform/collection/governance_outbox.py
api/geo_platform/collection/fence_recovery.py
api/geo_platform/collection/sampling_cells.py
api/geo_platform/collection/worker_attestation.py
api/geo_platform/collection/worker_runtime_scopes.py
api/geo_platform/collection/browser_supervisor_client.py
workflows/activities/execution_coordinator.py
workflows/activities/fenced_submission.py
workflows/activities/temporal_chain.py
services/browser_supervisor/（新的最小权限node-local服务、fsync journal与startup reconciler；按项目实际包结构落位）
deploy/production/geo-platform-v2-browser@.service
deploy/production/<新的browser-supervisor unit及worker scope/transient-scope模板>
```

## 13. 分阶段实施计划

每一阶段都应保持可审查、可测试、fail-closed。不要在一个巨型提交中同时改 schema、五个平台 DOM 行为和生产部署。

### 阶段 A：基线、ADR 与 schema expand

1. 重新审计 current worktree、部署 snapshot、Temporal SDK 版本和 Alembic heads。
   1a. 在与生产同版本的Temporal Server+Python SDK上先做能力实验并保存机器可读证据：显式`request_id`重放、REJECT_DUPLICATE+FAIL、server accepted/ACK loss/快速终态/retention+archival NOT_FOUND、Workflow Task scheduled-event→producer-WFT路由关联、sticky/non-sticky与可选per-run pin。实验失败的能力不得写成假设；start幂等若不能证明就把durable start gateway列为本阶段阻断项。
2. 写 ADR 固化额度语义：旧 `used_*`=success，新 debit/reserved=平台额度 exposure。
3. Expand migration 添加新表；对现有 browser/account 表只加 nullable 列或旧 binary 可写的兼容默认。此时不得加入会让旧 acquire/heartbeat/release 立即失败的 NOT NULL、跨字段 CHECK、强制 trigger 或权限撤销。
4. 新 ledger 自创建起可以带自身局部 CHECK/UNIQUE/FK；涉及 old/new 双写字段的完整约束留到 contract。写出每条目标约束在 expand 还是 contract 生效的清单。
5. Current bucket 把有任务证据的 legacy `used_*` 回填为 success/captured 下界，并令 debit 至少等于该下界以满足守恒；debit baseline 没有可信证据时仍标 `unverified`，所有 finite scope grant=0。不得因三者机械相等就假装知道平台剩余额度。
6. 对每个 account × quota scope × day/week/year 记录 baseline source：可信平台 probe、人工核准、自然 reset 或 conservative full。未核准 current bucket 不得 canary。
7. Seed browser fence 行；旧 held/expired 行不能凭时间回填 free，必须在 drain/物理断连证据后 free，否则 quarantined。
8. 按node+service unit seed稳定worker runtime scope，记录当前InvocationID/worker boot证据但默认非open；先导入/核对supervisor旧OS action inventory与raw-port门禁，不能把“当前无DB recovery行”回填成ready。
   8a. 审计现有relay/manual/scheduled probe入口、状态字段和更新时间证据；只把可证明的最新**已应用有效观测**作为automatic projection baseline，无法排序的历史结果标unverified/disabled并要求新probe，不按最后commit时间猜。Legacy `collection_region.state='arrears'`及等价人工欠费/硬封禁拥有绝对优先级：expand migration在同一事务创建`force_blocked` baseline override、递增/seed config+override+health epoch、保留原note/actor/time/evidence并写baseline event；即使`last_probe_ok=true`也不得回填effective ok。它只能经显式clear后由新generation连续success恢复。Seed monotonic `next_probe_generation/terminal_attempt_high_watermark/applied_projection_generation`、config/override/health epoch和append-only baseline event；历史invalid/contract/barrier/diagnostic completion只能进入terminal high-water，不能被回填成已应用投影。
   8b. 用平台会话证据提取/验证canonical external subject，建立全局identity registry、append-only binding/session revision，并把quota policy/scope/bucket及平台账号级governance state/mode state迁到稳定`quota_subject_id`。Verified binding缺可信session证据时session state为expired/unverified、grant=0，不能从cookie文件存在推断verified。对同platform+canonical identity重复账号、同resident browser多verified账号或仅靠手机号/实例名猜身份的行全部置conflicted、grant=0；不自动挑winner。冻结duplicate group清单，按immutable operation/effect/治理receipt key对账历史exposure；当前wall/muted/rate-limit/hard block取不弱于所有来源的保守并集，不能因选winner清零，歧义bucket/governance保持unverified/conflicted。只有人工核准、两个partial unique、subject ledger/governance守恒及verified session均可VALIDATE后才允许canary；account旧字段降为带subject/version的只读兼容投影。
   8c. 对每个browser迁移current boot/context与health baseline。任意legacy `error_streak != 0`、异常值或无唯一来源，尤其已知值41，均建立migration health effect并置fence quarantined/context tainted；不得清零后开放，也不得让旧字段永久阻断。只有物理隔离、restart/reset、新boot+clean context+readiness probe receipt齐全才recover-ready。准备绝对projector并列出所有旧`error_streak += 1`与admission reader，contract阶段全部撤销。
9. 给每个既有 run 写显式 protocol assignment=0；`CollectionRun`、workflow-start outbox、task link 的新 provenance列保持兼容 nullable。新v1 run必须在同一事务冻结assignment=1、规范化assignment-item成员及count/ordered hash、Activity worker release、Workflow definition release/patch set/versioning behavior/compatible release set/queue/build/digest/config，禁止按payload字段缺失猜版本或只留不可逆hash。建立launch attempt/evaluation/member/normalized quota-scope+bucket snapshot、gen0 intent/start operation/RPC attempt/start receipt、assignment-terminal receipt、可选且一旦建立就永久的统一termination root/intent/alias/escalation，以及rootless post-terminal intent receipt schema，但feature保持off；quota bucket补单调authorization ledger version。Migration不得给v0历史伪造launch eligibility，也不得给已正常完成的历史assignment凭空补effectful root。
10. 建立immutable worker artifact release registry并记录当前部署digest/config/queue/protocol证据；未attest release不能进入v1 assignment。
11. 审计当前sampling policy、6个formal legs、136个canonical queries、campaign launch plan/schedule lineage及豆包normal/deep映射；shadow生成816 cells、每leg唯一primary-slot/revision候选和带规范化leg成员的canonical-main origin-intent候选，用双向anti-join/hash对比现有接口/历史主批次。同步建立prebind multi-leg intent partition operation/member/receipt schema，但不对历史失效intent猜分区。无法证明权威lineage/occurrence时只保留shadow审计结果，campaign不得freeze、不得创建可消费的权威slot/intent，v1主计划Run阻断；绝不按首个Run回填primary，也不改进度。既有v0历史若无证据，UI明确标`legacy_primary_unverified`而不是猜主。
12. 部署能读写 expanded schema 的新代码做 shadow/backfill，但 feature off；旧 binary 若尚存仍必须能在 expanded schema 上安全运行。
13. 此阶段不撤销旧角色完成旧协议所需的权限；先建立全量definer/trigger函数批准manifest、每能力独立NOLOGIN/NOBYPASSRLS owner、固定trusted `search_path`/`row_security=on`/全限定对象、默认撤销PUBLIC与runtime direct DML/private helper EXECUTE，并准备catalog/恶意search-path/同名operator-cast/RLS攻击测试。真正REVOKE放在old worker全退后的contract migration，但任何新v1函数从首次创建起即遵守该合同。

验收：migration upgrade、数据守恒查询、baseline 清单，以及 `old binary + expanded schema`、`new binary + pre-contract schema` 双向兼容测试通过。Schema downgrade 仅在隔离库验证；生产回滚不依赖 destructive downgrade。

### 阶段 B：execution grant repository 与 PostgreSQL 并发保证

1. 实现 control gate、QuotaCalendar、bucket 原子 reserve/debit/release。
   1a. 实现subject级quota config gate、永久stable scope、policy-scope revision、policy cutover/overlap transfer和scope-specific block/recovery operation；同calendar换policy复用exposure，config gate prepare/final各推进epoch，mode scope不误伤其他mode。
2. 实现 request item/submission operation、稳定 idempotency、contract drift、partial grant、grant/submission generation 和一个账号一条 live reservation。
3. 扩展 fence lease_id/state/boot/quarantine，联合取得 grant+fence。
4. 所有 repository 使用统一锁顺序、DB time、lock_timeout 和有界 retry。
5. 实现region automatic/effective双投影、probe generation/attempt/claim/receipt/event、manual override epoch和精确hysteresis受控函数；HTTP完全锁外，stale/expired claim及stale observation只审计，force-blocked下auto/diagnostic永不改变admission。Grant/begin-submission冻结并比较health epoch/effective freshness。
   5a. 实现external identity/quota subject registry、verified binding两条partial unique、binding-session header+append-only revision、composite snapshot FK和受控verify/rebind/login recovery；quota policy/bucket按subject唯一，不能通过复制/revoke/recreateaccount重置额度。Grant/activation/heartbeat/begin冻结并比较subject global/mode gate与session version/epoch/evidence/expiry。
   5b. 实现verified governance transition registry、typed control operation和唯一gate-effect apply；dispatch冻结policy，terminal同步apply critical gate一次，outbox只做非critical投影/通知。实现admin/expiry/session verifier的expected-version CAS和全部root/suffix真值表。
   5c. 实现唯一`evaluate_and_mint_collection_run()`：先释放schedule锁，在统一region→subject/account/session→quota policy/scope→browser/health/context→bucket→campaign/slot/intent锁序中生成可重建的launch snapshot。Blocked只写幂等evaluation，零Run/assignment/gen0/start operation/outbox；全员eligible才在同一事务消费attempt/origin intent并建立完整Run链。每个quota bucket授权相关变更推进ledger version，恢复后同attempt可以产生新evaluation但最多消费一次。
6. 增加 sweeper/reconciler，只在隔离环境运行。

验收：真实 PostgreSQL 双连接/多线程测试全部通过，无 lost update、无 quota oversell、无 deadlock。

### 阶段 C：显式 BrowserFenceLease 与物理恢复

1. `platform_browser()` adopt execution lease，不再二次 acquire。
2. heartbeat 同时续 reservation/fence，并传播 suspect/lost/cancel。
3. normal detach→release；异常→quarantine。
   3a. 实现browser health effect/projection和legacy `error_streak/activity`绝对projector；v1 admission只认matching boot、clean context、readiness/recovery receipt和health gate。Legacy非零baseline必须经过真实恢复，不能人工归零。
4. admin release 改 revoke/expected CAS。
5. 实现 deterministic release operation/read-back、durable context taint/reset receipt，以及按物理resource set串行的supervisor executor；`adopt/physical-seal`都必须等待所有相交旧generation OS job终态，不能只做per-instance队列。把physical seal与logical free拆成两个DB阶段：前者只能留下recovering/quarantined和append-only isolation receipt，后者必须等全部release blockers terminal。
6. 把 browser-owning Activity放入可精确终止的 per-attempt subprocess/systemd scope；若暂时保留共享 worker，则实现runtime-scope DB gate、同事务冻结完整blast member/release-blocker set、唯一scope recovery owner和scope级unit job，并落`collection_worker_scope_physical_isolation_receipt`。扫描期间并发grant、两个instance同时恢复同一unit，以及hard closure physical→logical release闭包必须有确定且无环的线性化结果。
7. 实现 browser restart/boot ID verify/recover outbox，并完成 token-aware gateway + raw-port isolation；若分期，先用完整 blast-radius 的“kill holder + restart browser”conservative profile，仍不得异常抢占。禁用/门控 `Restart=always`，boot commit/seal前 raw listener对 worker不可达。
8. 实现supervisor fsync command journal和startup reconciliation barrier；在D-Bus提交/响应/job终态/receipt/DB ACK每个边界重启都能对账，未ready时拒绝adopt/seal/destructive command并保持raw端口不可达。
   8a. 建立immutable supervisor/gateway release与journal reader-member registry。Rollout严格reader-expand→writer-enable；boot attestation冻结release/digest/reader set。新writer corpus必须被所有保留reader解析，未知record/version/terminal进入`incompatible_journal`而不是skip；partial gateway/supervisor rolling和旧binary rollback先做负测。
9. 清理全部 raw CDP bypass，并分别测试 strict-gateway 与临时 cooperative profile 的启动门禁。

验收：SIGSTOP/SIGCONT、DB partition、stale token、force revoke 和 restart 故障测试证明系统不再授权旧 writer；gateway/restart ACK 后其后续 CDP 消息被物理拒绝。已经转发的命令仍按 unknown/tainted 处理，不能声称被撤回。

### 阶段 D：逐题 submission ledger 与五平台接入

1. 实现公共 fenced submission guard 和三态 submit result。
2. 每个平台传递 lease/item permit，删除危险的二次发送 fallback。
3. 点击前 dispatching/debit，accepted/capture/staging/unknown 全部 durable。
4. batch 保存已完成前缀和当前真实 disposition；未开始 suffix 回到 waiting并可安全重授权，只有 terminal policy 才生成 neutral aborted。
5. Activity retry 从 ledger/staging 恢复，不重复发送。
6. 证据文件名加入 run/submission operation，以及获 grant 时的 reservation item identity。

验收：fake-browser 对每个 logical operation 记录 `send_count <= 1`；全部 killpoint 测试通过。

### 阶段 E：governance outbox、task 关联和 governor 锁化

1. 移除 task commit 后 best-effort governor 作为权威路径。
2. 建立verified governance transition policy/classification registry、typed durable control operation与统一gate-effect apply函数。Outcome在submission operation/request item terminal事务中以唯一event key写gate effect，insert winner同步应用subject/mode/session authoritative gate一次，再写引用该effect的durable outbox；quota effect同时引用实际reservation item。Admin clear、session verifier、expiry、baseline/reconciliation各用自己的typed source operation和expected-version CAS，不存在裸UPDATE。
3. Consumer在一个事务中校验claim token，按顺序锁subject/account/session/governance/browser及全部目标bucket，read-back已应用gate effect，再插入唯一delivery receipt；只有insert winner做通知子outbox、legacy绝对projection和非critical派生，并在同一commit标delivered。Consumer绝不再改critical gate/streak；suffix neutral、late capture/task followup为零delta。
4. Terminal/staging事务写连续generation的materialization command或suppression receipt；workflow persist与后台materializer共用同一consumer，append-only revision/selection、lifecycle/materialization receipt和command delivered同一Run-domain事务。
5. 实现immutable task-success command，冻结selected revision/version及其operation/reservation/capture/staging/bucket set；success consumer不锁Task/Run，insert唯一receipt winner才更新历史bucket。
6. 实现run-item resolution receipt和两阶段Run closure。Suppress terminal不锁Run；独立projector按Run-domain计数。`mark_collection_run_terminal`改为execution terminal→materialization complete→customer terminal，缺receipt或poison不得提前完成。
7. `persist_collection_result()` 只关联 immutable submission operation、可选 reservation item，以及成功答案所选的 capture attempt + verified staging/content hash，不按 mutable browser key重查；同一Task事务写sampling candidate command，analytics晚到由同一command唤醒，杜绝best-effort空窗。
8. 实现campaign/formal-leg/cell权威policy、launch-plan run-origin intent及其规范化leg成员、每leg primary slot/revision、run-leg/segment映射及candidate/completion/cell-lifecycle/selection ledger；role由slot contract计算，补采/run-now先到不能抢primary。实现未绑定multi-leg intent子集替代的原子partition：replacement与remaining continuation对old member set做不重不漏分区并同commit推进全部slot；已绑定后才允许目标leg独立replacement。把initial/replacement/partition/generic producer拆成最小权限DB角色和受控函数，撤销worker/UI/direct SQL改写身份字段的权限。Run resolution不直接推进采样，`completed_samples`按eligible candidate、`observed_cells`按cell唯一receipt。
9. Admin patch、expiry recovery、session login/captcha verifier、lazy reset和wall路径统一锁纪律；blocked-until不自动放行，只有durable operation/effect可恢复。

验收：terminal/effect/outbox、task/outbox/consumer任意crash/retry都不漏、不重；同一platform root的critical gate/streak delta恰一次，suffix/late capture为0；Task success最多一次；persist/suppress均由唯一run resolution receipt推进，现有run/task计数锁无反向边，customer run终态不早发；136×6 campaign恰有816 cells，补采/换版不重复observed。

### 阶段 F：Temporal/captcha/pause 集成

1. 保留 Activity 名称，接入统一 coordinator。
2. 为fresh v1首次协议分叉加冻结的baseline marker；建立patch registry。首条v1生产history之后每次command-sequence变化使用新的唯一patch ID并保留旧branch，逐版加入pre/post及“已有v1 history→候选build”Replayer/rolling测试。只有经Server+SDK实验证明的per-run Workflow Task pin才可替代相应patch；Activity poller DB gate不算版本隔离。Completed v0只回放，非终态v0在enforce前逐条终止/迁移至显式replacement run并清零。
   2a. 实现assignment routing revision/member与同事务building→frozen循环FK创建、gen0/CAN逐代冻结、producer-WFT event-link receipt和rollback corpus/negative-marker门禁。Revision扩展不改active generation，edge expiry只阻止新冻结，revoke走pause/drain；sticky/non-sticky、ACK loss、错actual member和CAN successor都必须验证。
3. 实现captcha continuation、owner capability/短heartbeat、三条owned-dirty白名单边、cleanup/purpose-handoff/same-holder transfer operation，以及logical-action一次性effect permit+gateway tri-state journal；明确区分grant/submission/capture/permit generation，并保证post-submit与ACK unknown不重发。
4. 实现数据库 pause/drain/emergency 与 worker 最终 gate。
5. 实现可信worker boot/activity poller/routing receipt，以及bootstrap_gen0/bootstrap_continue/readback-only/execution capability ceiling；错误producer Workflow Task build没有effect，`bootstrap_pending/active -> closing`后同attempt只能cleanup。
6. Continue-As-New前active→closing并断言无本generation live grant/handoff；普通分支waiter=0，history-budget分支原子prepare唯一waiter transfer。写带version/nonce的authorized next-generation intent；新run validation绑定actual Temporal run ID并接管transfer，Reset/Retry run无intent必须拒绝。Normal/cancel/受控terminate同样先closing，终态read-back后completed。
7. 实现assignment-terminal receipt、在非终态窗口内创建后永久的统一termination root、append-only intent/alias/escalation join、rootless post-terminal intent receipt，以及business deadline/terminal policy/cancel intent与start三边界；pre-start可证明未发走no-run materializer，ACK unknown用同request ID安全resolve，bootstrap_pending直接closing。User cancel/deadline/admin hard/out-of-band watcher在assignment尚未terminal时共享root但保留customer disposition，strength与expected sets只增不减；正常receipt先赢后的第一次late request/watcher只追加post-terminal审计并返回原终态，绝不创建root/owner/RPC/resource set。Active已有cooperative closing时不换owner，靠actual workflow abort-transfer、Temporal non-CAN terminal→watcher或合法CAN successor bootstrap direct hard-closing收敛，严禁history absence takeover。强制取得gateway barrier或scope-exit/worker-scope physical-isolation证据与完整settlement；Temporal RPC终态不能单独完成chain。撤销所有直接权限。

验收：v0、v1 baseline及每个后续patch absent/present history replay，新history、old/new Workflow Task rolling、cancel、terminate、heartbeat timeout、captcha resume全通过；候选build不能靠同一个旧marker改变command trace。

### 阶段 G：shadow、canary 和渐进恢复

1. 新 schema/reconciler 先上线但不发送。
2. Shadow 同时计算launch eligibility与would-grant，不写活动 reservation、不mint Run；对比旧resolver，并证明地域down/stale、豆包任一正式binding缺失、session/quota/browser/context任一gate失败时would-mint=0。相同blocked snapshot重复tick只能命中同一evaluation identity，不得预测配置行持续增长。
3. 全局pause/drain，完成Temporal inventory；停/升级所有旧worker、API、scheduler、cron、CLI和start-outbox producer/consumer，pause/delete旧Temporal Schedule/Cron/retry chain并收回direct Reset/Retry权限，证明旧binary/role/server-side producer不能再创建v0 run或领取旧queue；v0/pre-marker非终态workflow、旧queue pending/started collection task及v0 start-outbox pending/claimed都必须为0。
4. 补齐 backfill 后执行独立 contract/enforce migration：加入目标 NOT NULL/跨字段 CHECK/monotonic trigger，撤销 direct UPDATE/DELETE 和旧协议权限。Task provenance使用 protocol-conditional约束，不能要求v0历史伪造operation/capture。先在生产同构隔离库验证锁时长与失败恢复。
   4a. Contract前后、migration失败回滚及备份restore后都运行SECURITY DEFINER catalog manifest/ACL/RLS/search-path攻击套件；新增、漏配、owner漂移、PUBLIC EXECUTE、runtime role membership或definition hash变化任一项使enforce失败。
5. 运行 `new binary + contracted schema`、supervisor/gateway retained-journal reader兼容和Temporal frozen-corpus routing测试并重跑全量 invariant；Enforce 后先做 fake/admission-only 演练。
6. 每个 current account/mode scope 的额度 baseline 人工/平台证据核准后，经用户授权按单账号、单题、单模式 canary；再扩 batch 1→4。
7. 建议顺序：豆包北京 normal → deep_think → 上海 → 其他平台。
8. 每级观察完整额度 bucket、submission、fence、outbox 和 sampling progress，不只看答案是否出来；出现任何 unknown、projection drift、raw bypass 或 duplicate send 立即重新 pause，停止扩容并完成对账。

## 14. 必需测试矩阵

现有 SQLite/fake Session 单测不能证明 PostgreSQL 行锁和唯一约束的并发语义。以下测试是恢复门禁，不是“以后补”。

### 14.1 Schema 和 migration

- 从生产同构 schema upgrade，保留所有现有账号、browser fence、events 和 used 值；
- current day/week/year 以及 mode scope bucket baseline 守恒；无法证明 debit 的 finite bucket 为 unverified 且 grant=0；
- 历史 used 已高于新 quota 时迁移成功、available=0；
- NULL unlimited；quota=0；负数/非法状态被 CHECK 拒绝；
- partial unique live reservation 生效；
- account scope的NULL canonical_mode不能利用PostgreSQL NULL distinct语义插入两行；mode scope缺mode、account scope带mode及同账号两个active policy均被约束拒绝；
- outcome/request/operation unique 生效；
- region probe attempt key/generation唯一；claim generation单调，receipt按`(attempt, claim generation)`唯一且同键不同evidence hash冲突fail-loud，每attempt最多一个terminal winner和一个非none projection action。Current合法claim的diagnostic/barrier/stale/contract no-op也关attempt；只有stale/expired claim不占terminal winner。只有current、未过期、token/worker/contract匹配的claim，且observation更新、admission-barrier/config/override/policy snapshot匹配时可改automatic projection；普通no-op delta=0。`0 <= applied_projection_generation <= terminal_attempt_high_watermark <= next_probe_generation`可由terminal receipt与projection event分域重建，且业务stale只读applied projection；health projection event不含terminal high-water，invalid/diagnostic/barrier no-op后last projection event仍可供grant引用。Effective projection等于configured/auto/manual纯函数。Disable/enable、force-block/clear/expiry与policy change单调推进相应管理epoch，auto probe不得推进admission barrier或直接UPDATE auto/effective/override。相同barrier下低代success先改变health epoch后，高代hard failure仍必须apply而非barrier-noop；较高代invalid/contract/barrier/diagnostic terminal先完成也不得让较低代valid hard failure变stale；migration baseline source enum/ID/evidence缺失被CHECK拒绝，arrears baseline不能伪装成config change；
- legacy region `arrears`即使同时有healthy probe也原子迁移为force-blocked baseline并保留note/evidence；迁移后effective ok计数为0，只有显式clear+新generation达到恢复阈值才可授权；
- external identity`(platform, stable platform subject hash)`全局唯一且不可删除；scheme/canonicalizer alias只能FK到既有subject，不能生成第二个quota subject。Stable account header只能以同account composite FK指向append-only binding revision，grant FK到旧revision在rebind后仍有效。Verified/draining revision的subject与resident-browser两个partial unique、prepared rebind target唯一、verified字段NOT NULL、platform一致和composite snapshot FK生效；非法原地改snapshot/current version、跨account current pointer、跳跃state均被拒绝。同一subject撤销/重绑后仍使用原quota ledger；重复历史行迁移为conflicted/unverified且不能grant，不能通过新account行创建第二套active policy/bucket；
- verified/draining binding revision恰有session header/current append-only revision；跨binding pointer、缺session row、verified state缺evidence/已过期、原地改session revision或旧version回写均被拒绝。Reservation对binding session、subject global/mode gate、governance transition policy及browser health的composite snapshot FK完整，后续revision仍保留历史FK；
- governance transition policy的global NULL域与mode域version unique、各自active partial unique生效；exact mode无active、同时两active、classification/action anti-join或hash不符均fail-closed。Gate effect的typed source XOR/composite FK、source generation/root critical unique、policy/action FK和audit-only零delta CHECK生效；terminal、session verifier、admin、expiry、baseline、reconciliation不能互相伪造source；
- browser health effect source唯一、boot/context错链及audit-only非零delta被拒绝；legacy nonzero/异常`error_streak`迁移后fence=quarantined且context非clean，不可能仅UPDATE旧字段恢复ready；
- FK RESTRICT 阻止删除有 ledger 的账号/browser；
- 全部definer/trigger function inventory与批准manifest双向anti-join为0；owner是专用NOLOGIN/NOBYPASSRLS角色，固定trusted `search_path`和`row_security=on`，PUBLIC execute/可写schema/业务table direct DML/private helper execute均为0；geo_api/geo_worker的DELETE及相邻phase调用被拒绝；
- fencing token 下降、直接 DELETE、非 recovery role 修改 current boot/recover 均被数据库拒绝；状态事件与 token 变化同事务；
- expand 阶段旧 binary 可写旧字段，新 binary 可运行 pre-contract schema；contract 后目标 NOT NULL/跨字段 CHECK/权限全部生效；
- task/operation/reservation link 的 tenant、run、business key 不匹配被拒绝；
- reservation以`region_projection_event_id`复合FK冻结immutable health event中的region health epoch/`region_applied_projection_generation`/effective freshness/policy，绝不FK到可变region current行；后续probe UPDATE current成功且历史reservation不被阻断/CASCADE改写。Reservation schema或授权函数引用`terminal_attempt_high_watermark`必须由catalog/static test失败。Begin比较applied projection使用`current >= frozen`而非相等：grant后benign success refresh推进generation/延长freshness仍可发送，人工/迁移制造current generation回退必须拒绝并告警。Region、identity、binding/session、gate、policy、browser boot/context/readiness错链，以及activation/begin-submission在epoch/version变化、不fresh、session纯时间过期或subject漂移后继续dispatch均被受控函数拒绝；
- staging 允许同 operation不同 capture generation 的 append-only terminal manifest/verified answer并存；相同 `(operation,generation,result_kind)` 不同 hash被拒绝；task capture/staging/hash错链被拒绝；
- v1 Task(building)→result revision→selected/committed在同一事务与deferred约束下可首插；任一killpoint不留下对外可见building Task，跨task/request-item循环FK被拒绝；
- assignment/request的task-persistence policy/hash一致；每个terminal item恰有materialization command或suppression receipt。Persist command的Task/revision/receipt/delivered原子，suppress路径永不误补Task；
- materialization gen1先被claim时等待gen0 receipt；gen0 poison/retry不会让gen1倒序覆盖，修复后按source generation应用。一个request item只有一条run resolution receipt，manifest→late answer、多success revision并发及command ACK丢失都只让run resolved计一次；class change只写净delta；
- task-success command通过复合FK冻结同一task/item/selected revision/selection version/operation/reservation/capture/staging/hash和完整bucket set；错链、非成功revision、同item第二条command及consumer事后改读当前selection均被拒绝；
- waiter的claimed/consumed/cancelled条件CHECK和live partial unique生效；claim snapshot缺request/effect/fence/continuation任一版本不能提交，非claimed残留claim credential被拒绝；transferring/transferred与transfer/next intent/routing generation复合约束生效，旧新waiter可保留同resume generation但live只有一个，错slice/readiness/deadline不能bind；
- captcha continuation每个跨holder状态有matching phase的append-only detach/barrier/scope-exit/install receipt；credential pending effect必须none，held必须matching current install。错predecessor kind、continuation/reservation/version/holder/lease/token/boot/context/purpose/connection/sequence的FK/CHECK均拒绝；
- request item(building)→初代operation→current/committed首插可执行；current operation跨item、generation不等于next-1、并发新operation跳号均在commit/CAS被拒绝；
- 所有既有 run有显式 protocol=0 assignment；v1 run/start-outbox/request/task版本、queue和expected worker release一致。Worker artifact registry同一deployment/build不同digest/config被唯一约束拒绝，assignment FK/冗余hash漂移被拒绝；v1 task缺operation或成功答案缺capture/staging被条件约束拒绝，v0历史NULL link仍合法；
- Fresh v1 assignment以复合FK绑定同一launch attempt/evaluation，deferred trigger证明evaluation=`eligible_consumed`且attempt/evaluation consumed run、campaign/origin intent、required member set与Run完全相同；每条eligible evaluation恰反向拥有一套Run/assignment/gen0/start operation/outbox。Blocked evaluation挂任一上述child、`blocked+非NULL consumed run`、`blocked+reason=none`、eligible只建半套、同attempt消费两个Run、assignment借另一attempt evaluation都在commit拒绝。Launch member→quota scope→current bucket规范行与header count/hash双向anti-join，bucket snapshot显式冻结ledger version、baseline source/state、blocker、limit/reserved/debited/available；删一scope/bucket、伪造opaque hash、同counter却改变blocker、finite available公式不符或evaluated-at不在bucket边界内均拒绝；
- assignment的sampling campaign/policy/run-origin intent、formal-leg role/segment manifest及run execution item count/hash冻结且不可由后续grant/task更新。Origin intent的规范化leg成员与header count/hash双向anti-join一致，Run不得包含成员集外leg，且intent最多绑定一个Run；canonical-main成员集只要有一个leg不匹配current slot revision就整次创建回滚。Primary assignment缺matching current revision、canonical-main class、lineage/version/occurrence/policy任一字段被复合FK/constraint trigger/受控函数拒绝，同campaign+formal leg两个frozen primary继续由partial unique兜底；
- assignment normalized item member按ordinal及`business_key+canonical_execution_mode`唯一，header count/ordered hash双向anti-join；漏项、重排、payload/query hash漂移及outbox/normal/terminal materializer使用另一份列表均拒绝。同一query同时含quick/normal与expert/deep_think时两条execution item都能冻结、start、终结；sampling policy映射同cell时不重复observed，genuine dual-mode映射不同cell时两cell各自存在，不能再因business-key-only unique漏掉专家腿；
- workflow chain generation从initial start起连续；`bootstrap_pending/active/closing` partial unique、predecessor与actual run约束生效；每个closing恰有一个live closing operation FK/kind/contract，裸closing和第二owner被拒绝。Gen0 intent、start operation、唯一RPC attempt与start receipt的完整start-lineage约束生效：A start operation指B gen0/receipt、A receipt指B gen0、A no-run借B attempt，以及同assignment但借generation 1、另一workflow/run/input/routing hash全部在commit拒绝；operation/attempt/receipt snapshot逐项相等且gen0必须`chain_generation=0, transition_kind=initial_start`。Child存在⇔parent pointer、state/completion-kind⇔attempt/receipt/no-run的双向矩阵成立；child已有但pointer NULL、state=started无receipt、pre-send带receipt、started与no-run并存均拒绝。循环首插与receipt ACK-loss可在同一事务/deferred FK下成功并read-back。Request ID/workflow ID/input/routing hash/显式reuse+conflict policy不可改写，started receipt才可bind为bootstrap_pending；bootstrap pending只有matching routing bootstrap变active，或termination/watcher原子变closing。CAN intent只能bind一个真实continued-from run ID并同事务写唯一bootstrap receipt，Retry/Reset run ID无intent不能伪造；rejected intent只能以新version/nonce受控rearm，旧input不能bind。`(closing_operation_id, closing_kind)`复合FK让hard-closure header只接受hard/watcher kind；cooperative owner不能原地插入。Supersede receipt只允许immutable non-CAN terminal/no-successor→watcher，old/new assignment/generation、双向链接、old=superseded、new=唯一live owner、chain FK/epoch及expected sets由deferred trigger原子核对；bootstrap_pending无旧owner的watcher是direct-close而非伪造supersede。Continue-As-New、NULL terminal、错误predecessor或history boundary漂移均拒绝。Termination request在bootstrap_pending/active时原子建hard owner，在cooperative closing时只建assignment gate/manifest；history缺CAN不能建hard successor。Actual workflow abort-transfer receipt或matching successor bootstrap才能建立hard owner，且request/manifest/epoch/intent/transfer逐项受约束。Assignment terminal receipt三条path XOR与各自FK/NULL/Temporal/item/materialization合同成立，normal_without_root不能引用root，termination路径必须引用同assignment唯一root；receipt提交后不可修改/删除；
- Assignment父行的root/terminal两个pointer、obligation epoch与state version单调；pointer通过同assignment复合FK、path XOR deferred trigger与child UNIQUE一致。任何root/receipt child存在而父pointer缺失、pointer跨assignment、`normal_without_root`同时有root、root路径receipt不引用该root、epoch/version倒退均被拒绝。Normal effect只查父级权威pointer且RR/SERIALIZABLE并发修改只允许serialization-retry，不能用旧ORM snapshot或child-absence继续；
- 每assignment最多一个termination request header，且只能在assignment terminal receipt尚不存在时首次创建；request header固定在chain与terminal receipt之后、scope/item之前锁，worker claim独立且operation-final。Request physical/item manifest与最终证明集合双向anti-join。RPC attempt的ordinal和target actual run不可改写/复用，predecessor continued后只有matching bootstrap或termination-reconciler successor-bind receipt可推进current target；reconciler receipt缺continued/start event、intent nonce/input hash或试图bind active均被拒绝。Completed request按target-kind XOR：unstarted必须有no-run receipt、gen0 rejected、start cancelled/completed、无actual run/RPC/closing owner、physical=0及完整neutral materialization；chain target不得有no-run receipt且必须由owned/satisfying closing operation及全部required-set/四轴receipt覆盖。全局termination ingress key只解析到一个同assignment分支：resolved root-alias在root未终态时允许terminal receipt为NULL并返回202，完成后只可NULL→matching receipt；resolved post-terminal从首次提交起必须有matching receipt。Rootless post-terminal intent不能引用/创建root、owner、RPC/claim/resource member或改变effect/obligation epoch；同ingress key同hash read-back、异assignment/异hash拒绝，alias与post-terminal跨表双占同key也被deferred trigger拒绝。逐一故意把A的intent挂B root、alias带另一root intent、escalation引用异root intent、ingress/alias结果指B receipt、terminal receipt引用B generation/closing/no-run，或让A的no-run借B的start operation/gen0/confirmed-not-forwarded attempt/root，全部必须由复合FK/deferred trigger在commit拒绝；
- worker boot/poller receipt唯一且不可由普通/旧build角色伪造或改写；actual release/queue/deployment/build/digest/config/protocol与assignment不符时matched receipt不可建立；poller receipt缺chain generation或control/chain/worker-scope三级effect epoch被拒绝；
- worker runtime scope稳定键唯一、boot/InvocationID代际匹配；每scope最多一个live recovery owner，gate关闭与完整blast member/release-blocker set满足count/hash/双向anti-join，同scope重开epoch单调且旧poller不能normal。Scope physical-isolation receipt只有在旧unit/holder exit、child new-boot/context/gateway seal、resource/member集合和supervisor reconciliation全部matching时可插入；operation ID或child投影不能代替receipt。Receipt存在时fence仍只能recovering/quarantined；任一hard/settlement blocker未terminal时DB free、child completed和scope open均被拒绝。Child browser recovery不能脱离matching scope operation独自完成共享unit kill/seal；
- supervisor command journal append-only，resource-set hash与规范顺序不可漂移；accepted/in-flight/outcome-unknown缺terminal/reconciliation receipt时supervisor ready/seal receipt和DB free/open约束均拒绝；
- `target_kind=chain_generation`的hard-terminate completed必须同时有matching Temporal terminal ref、完整physical-target receipts和完整item-settlement receipts；三者任一缺失、wrong token/boot/scope epoch或expected-set anti-join非零都被约束/受控函数拒绝。`target_kind=unstarted_assignment`只能走no-run XOR，不能伪造Temporal/physical轴；
- run expected item count/hash、execution terminal与materialization complete分层；缺materialization/lifecycle或suppression receipt、存在pending/poison时customer completed约束拒绝；
- sampling campaign在frozen前双向anti-join生成verified formal legs×canonical queries，136×6恰为816且count/hash一致；同时formal-leg×primary-slot为一一全覆盖且每slot有revision 0/verified launch-plan intent，intent-leg成员集合完整。Slot current revision通过同slot复合FK指向append-only revision，revision N只能接同slot N-1；`awaiting_run/replacement_pending/fulfilled/closed`与当前matching assignment一致。Frozen后增删cell/slot、换policy、直改current revision或原地改intent被拒绝；replacement/partition只能由专用角色追加revision并保留predecessor/evidence。未绑定multi-leg intent的partial replacement必须有唯一partition receipt，old member set恰等于replacement与continuation成员的不交并集，每个old slot恰推进到对应successor且current slot不指向cancelled/superseded intent；`R=M`时continuation严格NULL。已绑定intent的subset replacement只supersede目标run-leg assignment，未受影响slot/assignment保持fulfilled。Generic scheduler/run-now、worker、projector和UI尝试创建canonical-main intent/replacement/partition或直写role均被ACL拒绝。同campaign同revision跨两个cell、cell/leg/campaign policy错链、source mode mapping错腿、candidate/receipt cell不一致均被复合约束拒绝；
- 同run+formal leg只有一个role assignment，多个segment可共享；supplemental assignment不能携slot revision，错误caller role不能覆盖DB计算。Task/revision commit必有唯一candidate command，analytics晚到、consumer ACK丢失仍可恢复；ineligible/degraded不能插cell lifecycle receipt；
- control服务时钟只在open增长且单调；多次pause/drain/emergency冻结、resume续走，状态转换重试/ACK丢失不重复累计；DB重启和模拟DB时钟回拨不让tick下降或提前terminalize；
- migration 重跑/失败回滚不留下半张表或错误 GRANT；
- Alembic 单 head，不能制造旁支。

### 14.2 Quota unit tests

- 同一external identity/quota subject的账号全局与mode等全部scope × day/week/year取最小容量，bucket lock顺序稳定；
- 一个或多个 quota NULL；
- requested=0、partial grant、zero grant；
- deterministic prefix；
- mode block 和 account state；
- deep_think policy 缺 mode scope、混入别账号 scope、scope/period 冗余值与 bucket 不符时，anti-join/composite FK fail-closed；仅对错误子集算 hash不能通过；
- 豆包/其他平台缺verified正式账号绑定、canonical subject证据缺失、binding version漂移、region/browser不匹配时scheduler预检和Activity最终grant都阻断；不会回退env账号/CDP URL/临时browser。同一subject复制为多个account、撤销后重建、跨region/tenant重复登记仍共用一个额度ledger，conflicted group始终grant=0；
- subject级muted/rate-limit/wall/hard-error及mode block在account revoke/recreate、browser/region rebind、新binding revision后仍原样生效且streak不归零；仅有matching evidence的session/captcha状态随binding revision隔离。Legacy account投影漂移不影响grant，subject receipt重复投递只改变一次；
- current binding缺session row、session非verified/evidence漂移/verified-until已过期时grant=0；grant后captcha/session-expired/credential-tainted flip、block→verified ABA或没有writer的纯时间越界都使activation/heartbeat/begin失败。Reservation/lease/permit deadline不跨session expiry；受控login/assist新revision后只能新grant，旧credential不复活；
- governance真值表逐项验证：success清failure、failure清success并在`new_failure >= threshold`同effect block；explicit wall/block不重复加failure；capture/DOM/storage/unknown/fence/infra、pause/cancel/deadline/ungranted/neutral suffix、task-success和同root late capture的subject/mode streak delta均为0。同root多capture/suffix、跨submission generation及success/failure交错序列符合registry；
- browser legacy `error_streak=41`只能得到quarantine/tainted baseline；完成physical isolate+restart/reset+new boot/clean context+probe receipt后health recover-ready并可新grant。重复outcome不双增browser infra streak，平台wall/neutral/aborted/subject failure不污染browser health；旧generic/manual resolver无法用`activity/error_streak`绕过或永久阻断；
- 稳定scope/bucket身份与policy revision解耦：同一calendar contract下v1日内已debit=5，切到相同limit的v2后current exposure仍为5、available不增加，policy-scope revision只换合同指针而不创建第二套bucket；
- mode requirement新增/删除时，既有account-global及其他mode stable scope/bucket/exposure不变；被删mode不再进入新grant requirement但旧operation late settle仍回原冻结bucket，新mode缺verified baseline时仅该scope grant=0；
- quota calendar 的上海午夜、ISO 周、跨年和 DST 无关性；timezone/reset/period合同变化只能在共同边界无live exposure时切换，或通过`conservative_overlap_transfer`把reserved/debited/captured/success/unknown全量带入规范重叠bucket；mapping不完整保持config gate draining/blocked，绝不能新桶从0授权；
- policy cutover在prepare、draining、overlap transfer、pointer switch、final open及commit ACK丢失每个killpoint都恢复同一operation/effect；config gate `open(epoch e) -> draining(e+1) -> open(e+2)`后，冻结e的旧reservation/permit即使其他snapshot相同也不能activation/heartbeat/begin，证明无ABA；late old-bucket adjustment仍只修改原bucket并由transfer/reconciliation唯一传播，不污染新period或重复exposure；
- deep_think限额下调至exposure以下只把deep_think stable scope置blocked并推进其scope gate epoch，normal required scope仍open且可grant；global/account scope下调才阻断所有mode。恢复必须按scope独立进行，不能靠把subject config gate整行open清掉局部block；
- `policy_limit_below_exposure`在下一日/周/年current bucket rollover后分别由scope recovery operation重算并open；人工ledger adjustment、baseline approval与calendar reconciler并发只有expected epoch winner。Recovery receipt/operation commit ACK丢失只read-back，同一scope epoch只推进一次；timezone/overlap歧义不得被普通rollover清除；
- reservation 过 bucket end 时搬桶/重新授权；
- pre-submit 午夜 rollover 释放旧 reserved 并建立新 grant generation；submit/settle 后跨午夜仍只更新 dispatch 时冻结的历史 bucket；
- quota 下调、提高、reset；required-scope集合、scope gate epoch/hash、config gate state/epoch/current-policy任一变化都使旧grant及captcha handoff joint grant失效，normal与deep_think互不误伤；
- duplicate request 同 hash 返回同 grant；
- duplicate request 不同 hash fail-loud；
- 同 request 只存在一个 live grant generation；旧 reservation 不复活；安全重授权产生新 grant generation；
- accepted/unknown 不自动增加 submission generation，confirmed-not-sent 或人工批准才能产生新事实；
- accepted 但 capture 失败只 debit、不增加 unknown；dispatching 且原 holder证明 submit 未调用时准确撤销 debit；
- unknown 对账为 accepted/confirmed-not-sent 时 effect ledger 的补偿唯一且 `success <= captured`、`captured + unknown <= debit` 始终成立；
- task-success receipt 延迟到午夜后，只更新 item 冻结 bucket；legacy `used_*` 由当前 bucket绝对 SET，不发生跨日 `+=`；
- 完全没有 success event 的午夜/周界/年界仍由 query-time projection与calendar rollover服务把 legacy `used_*`切到新 bucket；服务停摆会告警并阻断恢复门槛，不依赖下一条采集事件触发；
- effect group 缺一个 bucket、多一个 bucket、重复 bucket、baseline 冒充 item source 均整体回滚，counter 不产生部分更新；
- normal capture与late capture竞争时同 operation最多一次 captured promotion；旧 terminal manifest、terminal reservation item和dispatch provenance保持不变；
- `terminal_not_sent`允许新 submission generation时，旧 operation只有审计，request item继续waiting且不生成最终task/manifest；最终 policy/deadline winner才生成一次neutral结果；
- 同一request item经人工批准出现多个submission operation/capture revision时仍只有一条Task和一个task-success receipt；selected revision可换版但`success +1`最多一次，显式reconciliation撤销另有审计；
- partial grant + captcha continuation 保持原 request item 的 ordinal 和 durable cursor 顺序；
- 所有 disposition 到 debit/success/release/unknown 的真值表。

### 14.3 真实 PostgreSQL 并发测试

用至少两个独立 connection/session 和 barrier：

1. quota 余 1，两个不同 Activity 同时申请，累计 grant 恰为 1；
2. quota=k，N≫k 并发申请，`sum(granted) <= k`；
3. 同 request key 20 并发，只产生一个 live reservation/lease generation；
4. batch 请求 4、容量 1，只授权确定性 1 个 item；
5. 两个不同账号/browser 可以并行，不被 region 行、open worker-runtime-scope行或全局大锁串行；仅scope recovery排他关门；
6. 同账号 active partial unique 只有一个 winner；
7. reserve 与 quota reset 并发；
8. begin_submission 与 sweeper 并发，只有一个终态；
9. settle、wall、admin quota patch、account rebind 并发，无 lost update；
   9a. region health apply/override×region rebind×identity/platform verify×grant服从region→identity-advisory→identity-row→account无死锁/漂移授权；verify不得先持identity advisory再等region，未分配region走稳定sentinel。不同账号/平台在同一ok region持共享锁可真实并行，probe/identity外部验证不持DB锁。双向browser rebind按identity→account→session→全browser→全health/policy/current-context→全fence→rebind-operation-final排序且unique binding只有一个winner，禁止跨account原子swap；cutover×operation reclaim×browser health recovery三向并发无环且旧claim不能提交；
   9b. Scheduled/manual/startup/half-open probe并发乱序：generation 1 healthy慢返回、2 failure快返回时最终由2决定且1 stale-observation-noop；反向完成顺序仍由较新的**non-none projection**决定。Claim 1超时被claim 2接管后，claim 1迟到healthy只能stale-claim-noop，不能占terminal/projection winner或覆盖claim 2 failure；timeout reconciler与current worker只有一个winner。Diagnostic、override-after-dispatch、newer-generation先terminal和contract reject都生成唯一terminal winner并关闭attempt，不被reconciler反复claim；只有projection action改变健康投影，matching half-open无论成功/失败/inconclusive都清active generation。新增精确反例：同一management barrier下g1=`valid hard_failure`慢、g2=`invalid_evidence`或`contract_rejected`快；g2只把terminal high-water推进到2且applied projection保持原值，g1随后仍必须apply并open/block。把业务stale错读terminal high-water、让g2压掉g1、或让g1事件把terminal high-water从2回退到1都必须由真实PG并发测试抓住；对照用例g2=`half_open_fail_closed`这类non-none projection先赢时才可合法使g1 stale。另构造valid projection后更高invalid/diagnostic/barrier no-op：terminal high-water可前进，但last projection event仍指向原有效投影，grant/begin不得因此阻断。Manual force-block夹在dispatch/completion之间，或auto probe在force-block后取得相同override epoch，结果都只能override/manual-blocked-noop，auto/effective/streak/freshness均不变；block期间diagnostic success也不能授权。Clear/expiry后projection为degraded+no freshness，必须由新generation达到success阈值才能ok。Disable→enable、policy change夹在HTTP期间时旧healthy以config/policy/admission-barrier epoch no-op关单，不能恢复；legacy arrears baseline始终manual blocked。必须专门构造degraded且success streak=N-1，g1 success与g2 hard failure冻结同一个admission barrier：g1先apply可把auto恢复ok，随后g2仍必须在**当前**auto投影上apply并立即open/block，而不是因g1推进health epoch就`admission_barrier_epoch_noop`；自动probe改变health/gate不得推进admission barrier。逐一验证ok下第1..N-1个soft failure仍只使用未过期的旧success freshness、阈值次转degraded、后续open、half-open success只到degraded、恢复阈值次才ok、hard failure立即open；`success×N -> failure×degrade threshold -> single success`仍degraded，证明连续streak已重置。Attempt/claim/receipt commit ACK丢失、worker崩溃和reclaim竞争都只有一个projection action/streak delta；open cooldown的system consumer与operator都必须写同类authorize operation，并在一个commit完成open→half_open、event/last pointer和唯一attempt；每个killpoint无half-open孤儿，二者并发只有一个winner，真实grant始终为0直到fresh ok。另构造grant冻结applied=g、随后success-to-success推进到g+1且health epoch/policy不变、freshness延长：begin必须成功；把current投影回退为g-1的故障fixture必须fail-closed/P0。Health/config/override/policy/admission-barrier epoch flip与begin-submission竞争要么旧submit先线性化并纳入settlement，要么flip先赢阻断发送；success-to-success仅刷新freshness不误撤销在途grant；
   9c. 两个session并发create/verify相同platform+stable official subject只有一个identity row和一个verified binding revision winner；即使分别携带不同alias scheme/canonicalizer/手机号证据，只要official subject相同也只能追加到同一subject，不能产生两套quota。相同resident browser绑定两个identity也只有一个winner。Verify与revoke/rebind/grant/session verifier/quota或governance-policy activate/down-patch、subject wall/mute/mode-block effect、canonicalizer升级、跨region复制账号并发时都服从subject→account→session→governance→policy顺序，无死锁、无空窗授权、无policy/governance挂错subject，且grant snapshot只引用一个verified immutablebinding+session revision。保留指向old revision的历史grant并执行rebind：prepare/rebind-pending、draining、target verified-session evidence、cutover事务前后及commit-ACK丢失各崩溃点都不破坏历史FK、不出现两个current/verified owner；cutover前新grant=0，cutover后只有target revision+session可新grant，retry只read-back同operation，旧subject block/streak仍生效。复制同一外部账号、撤销后重建账号或跨tenant创建管理行都仍命中同一quota subject/current bucket和governance gate，不能获得第二套limit或清空wall；只有alias且拿不到official stable subject的并发verify全部保持pending/conflicted、grant=0。历史duplicate迁移在conflicted/unverified时grant=0，人工决议后subject effect/governance anti-join与counter守恒；
   9d. Governance terminal root与admin clear、captcha terminal与session verifier、expiry claimant与重领者、terminal与policy cutover并发各只有expected-version winner。Blocked-until到点但expiry effect未提交时grant仍0；claim/apply/commit ACK丢失恢复同operation/effect。两个session并发activate同platform NULL-global或同mode policy只有一个active；policy cutover先赢则未dispatch reservation失效，begin先赢则operation冻结旧policy且terminal/ACK retry始终按旧版；
   9e. Grant/activation/begin与session captcha/expired/tainted flip、verified recovery及纯DB-time expiry并发：要么dispatch先按旧有效snapshot线性化并保守settle，要么gate/时间边界先阻断；不存在state恢复后旧grant ABA发送。Browser health quarantine/recover与同样三条路径也服从browser→health/exact-policy→current-context→fence；legacy 41恢复前grant=0，完成真实physical isolate/restart/reset/readiness后在同一logical-release CAS变ready/clean/free且streak=0，恢复后仅新snapshot可发送；随后第1..N-1个新infra failure从0计数且不block，第N个才block；
   9f. Quota policy cutover/down-patch/calendar rollover/scope recovery与grant/activation/heartbeat/begin并发服从subject→account→config gate→governance→policy/stable scopes→buckets→operation-final，无deadlock和半套授权。Prepare draining前旧grant先线性化则只能在冻结发送边界内settle；draining先赢则未发item停止。Final open后旧config epoch不能ABA。Deep_think scope block/recover期间normal真实并发grant继续，反向亦然；account-global block才同时阻断。Cutover或scope-recovery claim过期重领、commit ACK丢失、旧bucket late adjustment与新calendar grant并发都只有一个effect，exposure守恒；
   9g. `evaluate_and_mint_collection_run`与region config/health flip、binding/session gate、quota reserve/release/cutover/rollover及browser health/context flip逐一设置真实PG barrier并做三向压力。Writer先锁定并提交不可用事实时，launch只产生同snapshot的一条blocked evaluation且`CollectionRun/assignment/gen0/start operation/outbox`新增均为0；launch先提交时只允许该occurrence恰一个当时合法Run，随后Activity最终gate因新epoch/version阻断发送，重复tick绝不mint第二Run。特别覆盖quota available=0 blocked后reservation release或自然reset推进bucket ledger version：同attempt追加一个新evaluation并恰消费一次；未改变ledger/snapshot的数百次tick仍read-back原blocked evaluation。Region down/stale、豆包normal/deep_think任一腿缺verified binding、session过期、quota scope blocked/耗尽、browser unready/dirty均做“一个member失败→整批零Run”用例；所有member恢复后同attempt只mint一次。并发mint×Activity grant×quota cutover/browser-health recovery使用统一`...policy/scope -> browser/health/context -> buckets...`锁序，循环中`40P01=0`、无半套evaluation/Run；同一ok region下不同账号/平台/browser仍能在短region共享锁后并行，不能被地域行长时间串行；
10. Terminal effect/outbox重复调用和consumer重复投递只应用critical gate/streak一次；consumer只做非criticalprojection。Terminal/effect/outbox commit ACK丢失read-back同三联，suffix aborted、同root capture/late capture、task followup的critical delta=0；
11. pause commit 与 begin_submission 竞争具有确定线性化结果；
    11a. emergency只能在完整quiescence/recovery receipt后以expected epoch转paused；ACK丢失read-back同operation，旧emergency op不能覆盖新epoch，任何emergency→open被拒绝；
12. 全路径压力循环无 PostgreSQL `40P01` deadlock；
13. lock timeout 有界，失败可安全 retry，不把 DB 连接长期占满。
14. 同 request 在 pause→resume、fence busy→free、finite bucket reset 后可重新取得 grant，不留下永久 rejection/failed placeholder；
15. pre-submit rollover 与 begin_submission/sweeper 竞争，旧新 bucket effect 各自守恒且最多一次发送；
16. task persist 持有 run 锁时与 quota settlement/outbox consumer 并发无锁环，task link 仍幂等；
17. terminal operation/gate effect/critical projection/outbox及delivery receipt/noncritical projection/outbox delivered任一位置崩溃都整体回滚或read-back同winner；绝不出现“receipt已有但副作用永远缺失”，也不以补救为名再应用critical delta。
    17a. task materializer在Task/Revision/receipt/command-delivered任一位置崩溃整体回滚；workflow persist与后台reconciler并发只有一个winner，run/task计数只增一次。
    17b. 同request item的gen0 terminal manifest、gen1 late answer、gen2人工operation并发/乱序claim，receipt与selection严格按source generation；低代重试不回退高代，completed始终按distinct request item。
18. claim-only consumer 与 apply事务并发覆盖 pause/recovery/captcha closure/governance：没有 operation→control/account/browser 的反向锁环，stale claim-token不能推进phase。
    18a. Initial termination-request prepare/retry、过期termination work-claim重领、pre-start terminal materializer与同item settlement四方并发，全部严格`assignment -> chain -> assignment terminal receipt -> termination request header -> resources/items -> work claim final`；真实PG压力下无`40P01`，没有item→terminal/request反向边，旧claim不能提交，request/item/manifest count/hash只有一个完整winner。
    18b. 同一**未终态**assignment的user cancel、deadline、admin hard与out-of-band watcher用不同/相同idempotency key并发，只产生一个创建后永久的termination root；intent/alias均可审计，closure strength按冻结join policy单调升级，customer disposition不被admin hard偷换，expected physical/item/captcha sets只做并集不缩小。Root完成后新key只read-back同terminal receipt；同key不同合同fail-loud，不能创建第二root或重开run。
    18c. 用真实PG把rootless normal terminalizer与“该assignment第一次”late user cancel、admin hard、deadline及watcher分别在assignment锁前后设barrier并全排列，同时在terminal commit ACK丢失、late-intent commit ACK丢失和两端重试处kill：normal先赢时恰有一个`normal_without_root` assignment receipt、零termination root/owner/RPC/work claim/resource member/新scope blocker，late入口各自只写或read-back同一post-terminal intent并返回原customer disposition；request先赢时恰有一个root，normal terminalizer不得再写rootless receipt而必须按root分支收口。数据库中`normal_without_root receipt + termination root`裂脑计数、terminal receipt后effect/obligation epoch增长、已satisfied blocker复活和late watcher发Temporal RPC的计数均为0；同late key不同hash必须fail-loud。
    18d. 统一termination ingress在“root创建commit后但assignment尚未terminal”及其ACK丢失处重试，必须read-back同一resolved root alias并稳定返回202，不能因terminal receipt仍NULL回滚/另建root/post-terminal child；root完成后同ingress只补matching receipt projection。用同一全局ingress key分别并发请求两个assignment、以及在normal-terminal与root分支两侧重放：只有第一份完全matching contract可生效，其余fail-loud，alias/post-terminal child总数恰为1。另以READ COMMITTED等待父行、RR/SERIALIZABLE旧snapshot冲突两组真实PG测试证明后者只serialization-retry，不能看见旧空pointer并提交裂脑。
19. captcha joint resume 与普通 grant、quota config cutover/scope down-patch/recovery、pause、fence revoke并发；handoff acquire严格锁`binding session -> quota config gate -> governance -> policy/stable scopes -> browser/context -> buckets -> fence`。Config gate draining/epoch漂移或本mode任一required scope blocked/epoch/hash漂移时continuation/token不变且没有半条reservation；不相关mode scope变化不误阻断。成功时完整gate snapshot、quota预占与fence owner/token同一commit。
20. request在pause等待期间跨 business deadline、收到cancel以及closure reconciler竞争时只有一个terminalization winner，deadline不因Activity retry延长。
21. v0 run/assignment/start-outbox producer与protocol enforce commit竞争：producer先提交则enforce因inventory非0回滚；enforce先提交则producer被最低版本约束拒绝；不存在切换后新v0 outbox。
22. 多个terminalizer与pause/resume并发读取同一service clock version；service deadline只产生一个winner，DB时钟回拨时保守延后而不是提前终结。
23. 同一run延迟创建多个segment/request、Activity retry和continue-as-new时全部复制assignment中同一个service deadline tick；不会按request创建时刻重新获得完整预算。
24. pause resume signal与watchdog同时触发两个resume Activity时，waiter claim只有一个winner；claim ACK丢失恢复同token，非origin workflow/错误slice/只持request ID均不能推进。
25. workflow-start consumer与protocol enforce/pause并发服从control→assignment→intent顺序、无死锁；Temporal RPC期间不持DB锁。Start成功后首Activity抢先运行且consumer崩溃时只返回receipt-pending，reconciler read-back补receipt后安全继续且只启动一个workflow。
26. Waiter claim前已有旧reservation/effect但claim后无变化时可安全回ready；claim后grant已commit但ACK丢失、admin bump fence token/boot、continuation转owner或cancel closure任一发生时，冻结version/snapshot阻止回ready并进入consumed/cancelled/quarantine的唯一正确分支。
27. `prepare_continue_as_new`把active转closing与旧Activity的grant/begin/capture竞争：prepare要么等旧effect事务先提交后排空，要么先赢使僵尸effect失败；closing cleanup heartbeat不能无限续租。Prepare已commit而下一Workflow Task尚未发CAN时，reconciler不得因暂时查不到successor而reopen。
28. 来自同一predecessor的CAN、Reset和Retry actual run并发bootstrap只有matching intent version/nonce/continued-from的CAN winner可bind；`bootstrap_pending/active/closing` partial unique始终成立。显式abort/rearm后旧nonce不能bind，双active和跳generation被约束拒绝；closing→abort→active后旧Activity poller epoch仍只能cleanup，新Activity登记新epoch才可normal。
29. Normal return、cancel、受控terminate与旧`to_thread` Activity并发：active先封为closing，同一execution Activity attempt从normal单调降级为cleanup_only并可settle/detach，但新submit立即被拒。普通return/cancel满足各自closure manifest后唯一completed；hard terminate即使Temporal先终态也必须等physical target和item settlement集合完整。原run僵尸不能再grant/attach/submit，且hard terminate完成前已被gateway/supervisor物理切断。Bootstrap Activity调用execution函数、caller把cleanup参数伪装成normal及cleanup新建grant/permit均被DB函数拒绝。
30. Task-success consumer与task materializer、revision selection、run closure、item兼容projection并发无`40P01`；consumer不锁Task/Run且唯一receipt最多`success +1`。Persist materializer持Run与suppress terminal持request item并发无反向锁；独立run projector只让一个resolution receipt计数。启用真实PostgreSQL FK trigger验证Task selection持行锁与same-cell sampling projector持cell锁并发，跨域command设计不触发Task↔cell隐藏`KEY SHARE`锁环。
31. Workflow提交materialization command后立即正常结束/cancel/terminate，后台延迟、ACK丢失或poison时Run只到execution terminal/materialization pending；全部persist lifecycle或suppress resolution receipt按冻结item count/hash齐全后才发布客户终态。全suppress run正确完成，late revision不重复resolved/改变分母。
32. Worker boot/poller登记、protocol enforce和Activity effect并发：错release/queue/deployment/build/digest/config、revoked/stale boot、旧build DB role及同attempt不同boot/hash全部无副作用拒绝；同build label不同digest无法登记，digest变化滚动部署必须新release。登记/Activity receipt ACK丢失按唯一键恢复，不能伪造matched。
33. 受控Terminate closure operation与`begin_submission`/gateway permit barrier竞态：要么submit先线性化并进入冻结item set保守settle，要么closing先赢使submit为0；Terminate RPC ACK丢失按同operation read-back。注入“Temporal先terminated、gateway barrier延迟/ACK丢失、旧`to_thread`/raw连接恢复并尝试写”：chain保持closing/quarantined、API只返回202、browser不能free/regrant，旧写被barrier或scope kill拒绝；matching gateway/scope-exit/scope physical-isolation receipt与settlement双向anti-join完成后才一次completed。Scope recovery允许先在recovering内完成restart/physical seal，但不得提前free；普通主体/脚本绕过两阶段直接调用Temporal Terminate被RBAC拒绝；模拟namespace管理员越权时watcher立即closing+quarantine且不自动重发。
34. 同一campaign+formal leg中，supplemental run-now/top-up先于canonical schedule提交并完成时仍只能supplemental、slot保持`awaiting_run`；随后slot授权的canonical-main intent绑定Run才成为primary并把slot置`fulfilled`。错误lineage/version/occurrence、intent成员集外leg、caller伪造primary、两个Run争同intent均被拒绝/幂等reload，不以锁赢家决定role。构造至少3-leg未绑定canonical-main intent，以`R={1 leg}`并发执行partial replacement与original schedule bind：bind先赢时partition整体回滚并转已绑定subset替代；partition先赢时old bind整体回滚，replacement intent可为R绑定primary Run，continuation intent可为其余2+ legs绑定另一primary Run。每个killpoint/ACK重试都满足old members=`replacement ⊎ continuation`、全部old slot current revision指向有效successor、零slot引用cancelled/superseded intent、零部分primary；漏掉其余leg continuation或只更新目标slot必须被deferred constraint拒绝。再覆盖`R=M`无continuation、连续二次partition、以及已绑定Run替一leg时未受影响leg仍fulfilled且old intent不被取消。主Run失败后既有supplemental不自动扶正；只有专用角色先提交audited replacement/partition revision及新intent成员才可进入`replacement_pending`并产生新primary，且与并发普通run-now只有确定winner。豆包normal补采/deep_think候选乱序到达同cell，eligible completion可累计但observed只0→1；genuine dual-mode按policy落两个cells。任一segment先完成、quota少、Task晚到、degraded答案或late revision均不误改role/formal leg/分母。
35. Task/revision commit与analytics/projector崩溃并发：candidate command与Task同commit；analytics先到/后到、两个projector、claim expiry和ACK丢失均只产生一个candidate/completion receipt。同campaign同revision尝试映射第二cell整体拒绝；selection换eligible candidate不重复observed。用两个真实connection和完整FK trigger循环压力证明Task域事务从不等待cell，sampling域事务从不因FK/trigger取得Task/Run锁，无deadlock/livelock/poison。
36. Activity持旧poller receipt经历pause→resume或control reconfigure后，即使当前又是open且chain active也因control effect epoch不匹配只能cleanup/重新schedule；旧attempt申请grant/permit/owner adopt均失败，新attempt冻结新epoch后才可normal。Downgrade receipt一旦存在永不回退。
37. 多日pause/resource unavailable用deterministic退避逼近history soft budget后，只生成一个waiter-transfer/next intent并CAN；resume signal、watchdog、prepare transfer、abort和successor bootstrap并发不丢ready事实。旧run/routing generation不能claim，successor ACK丢失read-back同transfer，跨多次CAN仍是同request/slice/deadline且history不触硬限。
38. CAN prepare、normal/cancel terminal prepare与hard terminate三方竞争只有一个deterministic closing operation owner；active→closing commit后下一写之前崩溃可按operation恢复。Hard request晚于cooperative owner时只建立assignment termination gate和冻结manifest，不冒领。注入Workflow Task已经取得prepare Activity结果、停在RespondWorkflowTaskCompleted/CAN command前：并发hard proxy即使连续读到history无CAN也不能reject intent/supersede；若actual workflow先执行abort-to-hard Activity，old abort+intent/transfer reject+new hard owner+epoch+receipt同commit且workflow不再发CAN；若Temporal CAN先提交，successor bootstrap直接hard-closing并继承request，无active/grant窗口；若successor worker离线，termination reconciler凭immutable continue/start/input证据closing-only bind并与迟到bootstrap只有一个winner；若Terminate先赢造成non-CAN终态，只有history封口/no-successor后watcher接管。ACK/describe不明保持原owner+request pending。
    38a. Live hard request commit与active generation的grant/activation/begin_submission/owner-adopt竞争服从assignment行锁：normal事务先提交则其事实进入request冻结expected set并随后cleanup；hard request先提交则所有normal函数在scope/account/browser前失败。Request与successor bootstrap并发只有“bootstrap先active、request立即active→hard closing”或“request先、bootstrap直接hard closing”两序，任何采样重复循环中successor normal permit/grant均为0。
39. Shared-worker scope gate与grant竞态：recovery扫描instance X期间，同boot的instance Y申请grant；Y要么先在线性化点提交并恰好进入冻结member set，要么scope emergency先提交使Y在reservation/fence/attach前失败。Member count/hash双向anti-join无漏项，旧poller scope epoch在scope重开后仍只能cleanup。
40. 两个instance recovery并发命中同一worker service unit：只有一个live worker-scope recovery owner和一个规范unit stop/start序列；supervisor按相交resource set串行，任一instance child不能独自seal/free，独立物理scope的第三个instance仍可并行。
41. Supervisor在prepared journal、D-Bus调用、job ID返回、job in-flight、OS terminal、terminal receipt及DB ACK各边界崩溃重启：startup reconciliation期间raw端口、adopt/seal/destructive command和grant全部关闭；旧job逐项reap/证明terminal，outcome unknown不重发，任何旧动作不能在新seal/new holder后生效。
42. Out-of-band Temporal终态撞上既有CAN/normal/cancel closing owner：非CAN terminal且history/no-successor证据完整时，old→superseded、next intent/transfer reject、new watcher owner、hard-closure expected sets、chain FK/contract和effect epoch在同一commit完成，只有一个winner；receipt/commit ACK丢失read-back同successor。Temporal cause=Continue-As-New、matching successor晚到或history/ACK不明时supersede为0，保持原closing并由bootstrap/人工恢复；不得因暂未看到successor误终结chain。
43. Hard closure与shared-scope recovery闭包无环测试：scope gate/blast set→unit kill/old-holder exit→child new boot/context reset/gateway sync→physical seal receipt提交后，hard target可以`isolated`，但fence仍recovering、child/scope未completed且新grant=0；即使settlement与Temporal先后乱序，也只在四轴齐全时hard completed。随后member hard/recovery/materialization blockers才转satisfied，logical release唯一事务把child free/completed，最后一个member后scope open。反向故障注入证明：仅scope operation ID、unit stopped、child boot committed或seal投影任一单项不能推进physical；physical receipt缺任一member/exit/new-boot/context/gateway/journal证据被拒绝；hard未完成时free被拒绝；hard完成但blocker集合不全也被拒绝；全程无互等、无提前regrant，ACK丢失只read-back同receipt/operation。

测试结束后执行 invariant SQL，而不只断言 API 返回值。

### 14.4 Fence 并发和 ABA

- 空表/seed 行首次并发 acquire 只有一个 winner；
- acquire commit 成功、响应丢失：同 holder session 能恢复未 activation lease；不同 worker/attempt 不能复用 active lease；
- fresh grant严格走fence held_unactivated+reservation reserved_unactivated→gateway install receipt→原子双activation；prepare/install/activation三个ACK-loss边界只恢复同一token/receipt，pending无attach/write且超时release reserved+quarantine；
- attempt 1 的 `to_thread` 僵尸与 attempt 2 并存时，attempt 2 不继承旧 holder/token，物理隔离前最多一个合法 writer；
- stale heartbeat/release 在 token bump 后 rowcount=0；
- admin expected token 过期时返回 409，不修改新 lease；
- force revoke、heartbeat、normal release、acquire 四方竞态；
- expiry 进入 quarantine，不能直接给新 holder；
- detach 失败进入 quarantine；
- restart outbox 重试幂等，只有新 boot ID 才 recover；
- recovery claim A 过期、B 以更高 supervisor claim generation 完成并让新 holder acquire 后，A 的迟到 stop/kill/start 全被接收端拒绝；
- 低 generation systemd job已经通过precheck并阻塞时，高 generation adopt不能ACK；只有旧job终态/取消/reap和实际unit状态归档后才能继续，新boot不受迟到job影响；
- `seal` 前存在同 generation未终态child/systemd job时拒绝；禁止任何 fire-and-forget OS动作；
- 共享 worker同时持有两个instance时，恢复其中一个必须先以worker runtime scope行关闭新normal effect，并在同一线性化事务枚举/冻结/quarantine同worker boot的全部lease再停unit；扫描中并发grant不能逃出member set。漏一个即fail-closed。per-attempt scope模式只终止准确cgroup，不影响另一instance；
- shared-scope physical seal与logical release严格拆开：unit/old-holder终态、全部child新boot/context/gateway seal和journal reconciliation齐全时只生成一个append-only isolation receipt，fence仍recovering且normal raw/gateway credential不可用；hard closure和全部规范化release blocker未terminal前任何free/completed/open CAS为0，完成后按child再scope顺序唯一释放；
- 两个instance各自发起recovery但命中同一worker unit时，唯一scope owner和相交resource-set executor只产生一条unit命令序列；per-instance seal不能越过scope seal，不同物理unit仍并行；
- supervisor在D-Bus accepted前后、job ID持久化前后、in-flight/terminal receipt/DB ACK边界重启时，startup reconciliation恢复所有旧boot journal；未清零前adopt/seal/raw listener/grant全部fail-closed，`job not found`不被无证据当成功；
- browser进程从start到DB boot commit/gateway sync/seal的窗口内，worker无法连接raw listener；意外 `Restart=always` 不会自动恢复可写能力；
- normal release commit ACK丢失后按release operation read-back：原event恢复成功、旧token精确重试、或新generation stale no-op；任何分支都不误释放/误quarantine新holder；
- resume/new lease后迟到的旧 pause signal因operation/epoch/lease/token/boot不匹配只审计no-op；
- browser supervisor 在 held lease 中自动重启会 token+1/quarantine，不能把新 boot 当成旧 lease继续用；
- force revoke 的伪造、过早、旧 generation holder/gateway ACK 均不能 free；
- unknown/tainted item 即使 detach 返回成功也不能直接 free 原 page/context；
- same instance 串行，不同 instance 并行；
- DB clock 与故意偏移的 worker clock 不影响判定；
- local lock acquire + DB joint acquire + `platform_browser` adopt 不发生二次 acquire/release。
- 同 request 并发预选不同 local instance，loser 释放错误 local lock，绝不 adopt winner 的另一实例。

### 14.5 Adapter/fake-browser 发送测试

Fake browser 必须独立记录每个 logical operation 的真实 submit 调用次数：

- strong validation 失败时 click/Enter 均为 0；
- click 明确未调用时允许唯一一次备选发送；
- click 已调用但抛异常时，不得 Enter fallback；
- composer 未清空但发送结果未知时不得第二次 click；
- accepted 后 capture 失败不重发；
- accepted 后 capture 失败计 consumed，unknown 不增加；
- dispatching 后同 holder 能证明 submit primitive 未调用时只补偿一次 debit，retry/sweeper 无权做该推断；
- staging 完成、Activity ACK 丢失，attempt 2 从 staging 返回；
- unknown terminal manifest先落库、post-captcha generation+1随后落verified answer时两份payload并存；result projection只CAS选择新答案，旧quota item/dispatch字段不变，task冻结正确capture attempt/staging/hash；
- batch 第 N 题 lost：前缀保留、当前状态正确、suffix waiting 可由 retry安全继续；到 deadline 才 neutral terminal，最终结果等长；
- 同一 operation 所有 Temporal attempts 合计 `send_count <= 1`；
- 人工明确授权的新 submission generation 可以再发，并产生独立 debit/audit。
- partial grant 遇到 pre/post-submit captcha 时，root、已完成前缀、安全 suffix 和未 grant tail 顺序/代际完全可恢复。

五个平台都必须运行同一组 contract tests，不允许只验证豆包。

### 14.6 故障注入 killpoints

至少在以下边界注入进程异常、DB 异常或 ACK 丢失：

```text
grant insert 前
region probe attempt/generation commit前后、HTTP response前后、receipt/event apply及manual override commit前后
Temporal start成功/首Activity先运行/consumer在receipt前崩溃
worker boot attestation/poller receipt commit前后
CAN prepare active→closing commit后、Workflow Task发CAN command前
hard-termination request/gate+manifest commit前后，尤其Workflow Task已拿prepare结果但RespondWorkflowTaskCompleted尚未返回
actual workflow abort-to-hard transfer事务前后、Activity ACK前后
hard proxy Terminate RPC与in-flight CAN Workflow Task在Temporal服务端排序的两侧
CAN command后、successor bootstrap bind前
successor bootstrap继承hard request并直接bind hard-closing事务前后
successor bootstrap bind/receipt DB commit后、Activity completion前
normal/cancel terminal prepare前后、Temporal terminal read-back前后
out-of-band terminal watcher supersede old closing→new hard-recovery owner事务前后
waiter transfer prepared后、CAN command前；successor waiter bind后、Activity completion前
bucket reserve 后、事务 commit 前
grant commit 后、Activity 收到响应前
local lock 后、DB grant 前
DB grant 后、CDP attach 前
preparing 后
dispatching commit 前
dispatching commit 后、click 前
submit permit 本地 guard consume 前/后（实现 per-effect gateway authorization 时再覆盖其 consume）
click 调用中
click 后、accepted 写入前
accepted 后、capture 前
capture staging commit 后、Activity return 前
Activity return 后、workflow persist 前
terminal item/materialization command commit 后、workflow terminate 前
CollectionTask commit 前/后
sampling candidate command与Task revision同commit前/后
analytics answer commit后、candidate/completion/cell lifecycle receipt前
suppression receipt commit后、run-item resolution projector前
run execution terminal后、materialization closure receipt前
task-success command/receipt/effect/command-final-CAS各边界
governance outbox claim 前/后
receipt insert 后、projection/outbox delivered commit 前
task link command commit 前/后
normal detach 前/后
fence release commit 响应丢失
worker-scope emergency gate commit前后、blast member snapshot commit前后
supervisor command prepared fsync前后、D-Bus send/response前后、job终态/receipt/DB ACK前后
hard terminate effect revoke后、physical barrier ACK前后、item settlement set完成前后、Temporal terminal先到/后到
finalize release/quarantine read-back 后、Activity return/task persist 前
```

每个 killpoint 断言：

- quota 不超售；
- item 只有一个终态；
- dispatching unknown 不重发；
- stale holder 不释放新 lease；
- Activity在release commit ACK丢失时恢复同一release operation；没有durable released/quarantined/stale-winner终态前不得返回给workflow持久化task；
- durable staging 可恢复；
- outbox 不漏不重；
- persist policy最终有唯一Task+selected revision+materialization receipt+必要task-success，或有唯一suppression receipt；workflow terminate不留下无解释Task缺口；
- Run可以暂处execution terminal/materialization pending，但不得在缺resolution receipt或存在poison时发布completed；后台恢复后count/hash精确收口一次；
- Candidate command不会因Task/analytics/projector任一崩溃丢失；eligible candidate可重建completed samples，同cell observed最多一次，ineligible/degraded不推进observed；
- chain在CAN/return/cancel/terminate空窗最多保持closing，不会恢复旧effect或出现双active；
- chain closing始终绑定唯一kind/operation/contract，prepare/bootstrap ACK丢失能幂等read-back；多日waiter transfer不丢ready或重复claim；
- watcher不能给cooperative kind硬插hard closure；有完整non-CAN terminal/no-successor证据时只做一次audited old→new owner supersede，CAN successor可能/不明时不误终结；
- shared-worker scope gate提交后不会漏掉并发新grant；相交resource set中任何旧supervisor/systemd job未终态时不得seal或开放新holder；
- hard terminate的Temporal终态、physical isolation与item settlement三者缺一时chain仍closing/quarantined，旧raw/to_thread writer无法越过最终completion；
- 后缀未执行题不扣额、不增加 streak。

### 14.7 进程与网络分区演练

- heartbeat 返回 false；
- heartbeat DB timeout；
- DB 单向网络分区，旧 worker 仍能访问 CDP；
- worker `SIGSTOP` → lease expiry/quarantine → browser restart/recover → 新 worker acquire → 旧 worker `SIGCONT`；
- 断言 recovery ACK 后旧 generation 的后续 CDP 消息被 gateway 拒绝或因 browser restart 物理断线；不得把已经转发的命令断言为“已取消”；
- revoke commit 到 gateway ACK 之间注入旧 CDP 消息，系统保持 quarantine，不能提前给新 holder；
- 在 gateway ingress/内部队列/raw socket write 三个位置放 barrier：ACK 前旧 frame 要么记录 dropped，要么记录 forwarded；ACK 后不得再出现任何旧 generation raw write；
- submit frame已经forwarded、随后一个无关frame被barrier dropped时，题仍为unknown/consumed且不得退款/重发；只有经过独立验证的per-effect frame binding才能把准确submit frame的未转发提升为confirmed_not_sent；
- click 已转发后立即关闭 gateway socket，验证页面请求仍可能继续，item 进入 unknown/consumed 且 page/context tainted；
- worker SIGKILL；
- Temporal heartbeat timeout；
- workflow cancel 和 terminate；
- outbox worker crash/restart；
- browser supervisor在旧boot存在accepted/in-flight/outcome-unknown D-Bus job时restart；新boot必须先reconcile journal/ListJobs/实际unit与cgroup，期间adopt/seal/destructive command、raw端口及grant全部fail-closed；
- 先由新supervisor/gateway writer写每一种新journal record与terminal kind，再尝试启动缺对应`journal_reader_member`的旧binary；orchestrator必须在boot前拒绝，或binary进入`incompatible_journal`且raw端口关闭。未知schema/version/terminal/hash不能skip、清空或降版后报告ready；
- Gateway先升级一半、supervisor先升级一半及交叉新旧peer滚动：reader-expand阶段所有旧新reader先登记并通过retained corpus，writer开关才可启用；任一peer不接受writer/control协议时保持旧writer或全局paused。Supervisor terminal receipt commit ACK丢失后立刻回滚binary，旧reader必须幂等read-back同record/DB mirror，不能重做kill/start；
- 冻结release/reader inventory后并发产生新journal record或切换gateway/supervisor release，rollback manifest CAS必须失败并重新inventory；restore演练从备份恢复DB+journal corpus后，只有release digest、boot attestation、reader count/hash、network-isolation contract与manifest全部匹配才能startup-ready；
- cooperative profile的共享worker unit被停时验证worker-scope gate已先关闭且完整blast-radius member set在同一线性化协议冻结、全部lease已quarantine/settle；扫描期间同boot新grant被纳入或拒绝。两个instance同时恢复同unit只产生一个scope级unit job；自动重启browser的raw端口在boot登记/隔离门禁完成前不可达；
- hard terminate RPC先返回Temporal terminal而gateway ACK/holder kill延迟或丢失；旧sync线程/SIGCONT/raw socket尝试继续写必须被物理拒绝，chain/API/browser保持closing/quarantined/202直到isolation+settlement receipts齐全；
- gateway restart/cache 丢失会关闭全部连接，完成 DB/supervisor resync 前 fail-closed；
- raw `/json/version` URL 泄漏、raw endpoint reconnect、新 target/session 绕过均被拒绝；
- worker UID 即使知道 raw 地址也不能建立连接；仅绑定 localhost 的实现测试必须失败。

### 14.8 Captcha tests

- pre-submit captcha：未 debit，suffix release；begin_submission 前只换 grant generation，confirmed-not-sent 后才换 submission generation；
- post-submit captcha：debit，自动 resume 不重复提交；
- post-submit captcha在root quota item已terminal后创建独立capture attempt/generation和capture lease；late capture不复活item、不覆写dispatch lease，unknown resolution effect最多一次；
- captcha_pending handoff 期间其他 run 无法接管 browser；
- collector→assist→assist_detach_pending→resume_ready_detached token单调且expected CAS；旧collector/assist的detach、execution-scope exit和gateway barrier receipt缺一不可；
- assist/capture/collection每次owner/purpose adopt都先进入credential_pending且effect none；分别在prepare DB commit后、gateway install/barrier ACK后、activation DB commit后崩溃，retry只恢复同一token/operation/receipt。Pending绝不能attach/write，超时不回旧token而quarantine；
- Owned-dirty跨purpose/owner只白名单三条边：`collector -> captcha_assist`、`captcha_assist -> capture_root`、以及完全相同holder session/process/scope/gateway connection的`capture_root -> collection`。前两条必须purpose-handoff pending→direct barrier/install→activation并原子重绑context；第三条必须专用same-holder owner-transfer/member barrier。穷举所有其他“第四条边”（collector→capture、assist→collection、capture→不同holder collection、collection→assist回跳、跨run/boot/context等）均fail-closed；跨holder恢复collection只有先以完整cleanup receipt把context变clean后走普通grant；
- install receipt与detach/purpose-barrier/recovery-seal的direct lineage逐项匹配；错predecessor kind、continuation/reservation、transition、holder/process/lease/token/boot/context/purpose/connection/sequence或覆盖current projection均被拒绝。Same-holder capture→collection无purpose barrier时不能直升；
- handoff adopt 的 run/business key/generation/token 任一不符时拒绝并 quarantine；
- `acquire_execution_grant_from_handoff()`遇quota config gate非open/epoch或current policy漂移、缺任一required scope/scope epoch/hash漂移、quota=0、bucket busy或binding漂移时，pre-submit claim回resume_ready_detached、post-capture保持suffix_ready/同holder，fence token均不变且不创建reservation/effect；成功时完整config/scope snapshot、全部scope bucket预占与collection owner/token切换原子完成；
- 两个worker并发claim只有一个winner；matching claimed holder的joint-grant commit ACK丢失按原claim恢复。Claim过期在零reservation/effect/fence变化时才回resume_ready_detached，有任何不确定即quarantine，第二worker不能趁expiry并行adopt；
- post-submit路径严格走`claimed(capture_root) -> capture_credential_pending -> capture_held -> suffix_ready`；credential pending/capture_held/suffix_ready过期不回resume_ready_detached而quarantine，同holder quota deferred不重复root capture；恢复collection仍须pending/install/activation；
- suffix_ready主动释放holder时严格走capture_detach_pending并验证capture phase receipt/token/boot/context/barrier后才到新的resume_ready_detached；错phase复用assist receipt、迟到capture ACK或旧capture holder继续写均被拒绝；
- assist detach gateway event延迟、伪造或ACK丢失时不签发新credential；旧assist在新claim前后恢复并尝试写入会被barrier/已退出scope拒绝。Cooperative共享线程无法证明退出时必须quarantine+restart；
- assist 手机输入在 lost/revoke 后立即拒绝；
- 每个effectful captcha动作使用稳定logical action slot与一次性permit，分别在permit签发前、gateway journal dispatching前、frame forwarded后ACK丢失、outcome unknown和terminal receipt commit后崩溃。只有`confirmed_not_forwarded`允许同action slot签下一permit generation；forwarded或unknown永久封口该slot，retry/read-back不能产生第二次点击/输入。两个并发successor permit只有一个predecessor CAS winner；旧generation、过期permit、错owner capability/heartbeat generation、绕过continuation manifest直接请求permit及直接raw CDP frame全部拒绝；
- Permit `expires_at`必须非NULL并clamp到owner capability/fence/continuation/health/scope边界；在dispatching期间到期不能被当confirmed-not-forwarded，gateway tri-state journal必须收敛为forwarded/confirmed-not-forwarded/unknown。Permit、gateway terminal和DB receipt三处ACK丢失均恢复同一action/attempt/hash，不能靠“再点一次看看”；
- assist 启动失败、超时、cancel、hard pause；
- 70 分钟模拟至少经历多次短`heartbeat_captcha_owner()`：每次递增owner heartbeat generation并追加capability/heartbeat receipt，旧generation续租rowcount=0，旧capability和已签permit expiry不延长。Heartbeat与pause request、hard/cancel termination、health quarantine、scope emergency及credential activate并发，gate先赢则只能有界cleanup/settle且不签新permit，heartbeat先赢也不能越过下一短expiry；ACK丢失只read-back同generation；
- Session verifier apply与新的captcha terminal、governance/quota-policy cutover、binding/session revision、continuation claim expiry四向竞态严格subject/account/session-first且verifier operation final；只有同一root high-watermark/policy/claim snapshot winner可写verified revision，late captcha或cutover先赢时旧evidence不得挂到新session，claim也不能回ready后被旧verifier推进；
- quota bucket 跨界时不长期保留旧 reserved units。
- partial grant + captcha continuation manifest 在 retry/continue-as-new 后保持原序；post-submit root 只做 capture continuation，绝不重新 reserve/send。
- continuation header/item、resume slice hash、original ordinal和claimant session在ACK丢失/nested captcha后完整恢复；相同generation不同payload fail-loud。
- 在assist/capture/collection credential pending、held、permit issued/dispatching/forwarded/unknown及detach各phase注入owner失联并转quarantined；quarantined始终占live unique且不能被直接视为terminal。验证固定顺序`quarantine -> gateway/holder physical seal(fence仍recovering) -> continuation closure逐项settle action/permit/item/owner/materialization -> terminal_cancelled/terminal_recovered -> browser logical free -> scope open`；缺closure receipt时free/chain/run terminal均被拒绝；
- Continuation closure在成员冻结、permit三态settlement、item/materialization、physical receipt、terminal receipt commit/ACK每个killpoint重试只恢复同一operation/receipt。Physical seal前closure不能完成，physical seal后不等browser free即可完成；两个reconciler/旧claim只有一个winner。Clean-detach路径无需restart但必须完整barrier；错误physical member/token/boot/context、漏一个historical permit/action/transition或把unknown当not-forwarded全部fail-closed；

### 14.9 Temporal replay

- patch 前真实 history 用新 definition Replayer 成功；
- patch 后 fresh execution 走新分支；
- Workflow definition release/routing revision首次创建的assignment↔revision deferred循环FK在同一事务成功；漏member、错hash、错误current pointer及任一commit killpoint整笔回滚，外部永远看不到`building`半套；
- Gen0 start按四个边界故障注入：RPC发送授权前cancel/hard/deadline走no-run；DB已`rpc_dispatching`但调用gRPC前kill后以同Temporal request ID/envelope和显式REJECT_DUPLICATE+FAIL恢复；server accepted后ACK丢失只绑定同run；workflow在receipt前极快非CAN终态由start resolution后direct watcher/hard closing。超过实测dedup/retention/archival安全窗或NOT_FOUND不确定时保持quarantined，绝不新request ID启动第二run；
- Pre-start terminal materializer在request/item/initial operation building、逐itemneutral manifest、persist/suppress command及no-run receipt每个killpoint可恢复；它没有reservation/quota/browser/fence effect。Normal ensure与terminal ensure并发只生成同一完整request/item集合；user cancel、deadline、policy terminal与admin hard保留各自customer disposition但共享start CAS；
- Start receipt后、bootstrap receipt前，hard request、首个bootstrap Activity、Temporal非CAN terminal watcher三方按assignment锁只有三种合法winner：active后立即hard-closing、直接bootstrap_pending→hard-closing、或无旧owner的bootstrap_pending→watcher-closing；任何顺序都没有normal grant窗口或永久pending。仍running但bootstrap worker crash只retry compatible bootstrap或走durable termination request，不凭absence watcher；
- 错误Workflow Task deployment/build调度出`bootstrap_gen0` Activity时，scheduled-event→producer-WFT routing receipt mismatch使其保持effect-none bootstrap_pending/quarantine，不能创建execution request、grant或把gen0 active；
- Activity attempt 2 复用 request，不重发；
- pause_requested 的 workflow durable park，resume 后同 request 继续且不生成失败 task；
- parked workflow没有holder时，resume epoch outbox仍把waiter置ready并signal；signal ACK丢失/consumer crash由重投或watchdog恢复，waiter receipt最终一致；
- deferred 后新 Activity ID 通过 input 复用原 execution request，contract hash 相同且不复制 operation；
- deferred resume新Activity只有持matching waiter claim才能复用request；signal/timer双唤醒、stale generation和另一workflow/run/activity均被CAS拒绝；
- 普通continue-as-new前无live grant/handoff/waiter，history-budget分支只有唯一transferring waiter；next chain intent/可选transfer已durable，新run验证真实continued-from并bind/接管。来自同一predecessor的Reset/Retry新run没有matching intent时不可执行；
- CAN prepare Activity已完成但下一Workflow Task尚未发CAN command时保持closing，reconciler不误reopen；CAN/Reset/Retry竞争只有matching nonce的真实continued-from bootstrap可bind；显式abort/rearm后旧intent replay失败；
- CAN/terminal prepare的DB closing-operation commit后Activity result丢失，retry read-back同operation；successor bind+bootstrap receipt commit后Activity result丢失，retry从active successor或matching hard-request direct-closing successor+receipt只读返回，不误拒绝或重复transition；
- Workflow Task routing receipt在写DB前/commit后/Activity ACK前丢失都按scheduled event+producer WFT+revision/member唯一键恢复；sticky execution、sticky eviction后的non-sticky replay和worker crash均必须关联真实producer WFT，不能拿正确Activity worker掩盖错误Workflow Task build。Verified lifetime pin路径单独用真实Server能力证明整个run，而非payload自报；
- Routing revision扩展与gen0 start/CAN prepare并发：已冻结generation永远保留旧revision/member set，只有尚未绑定的新generation可采用new current revision；prepare/approve/current-pointer ACK丢失只产生一个完整revision。Compatibility edge到期只阻止新冻结，不改旧generation；显式revoke则pause/drain引用代。Actual WFT member不在冻结revision、member-set hash错、edge kind/expiry或definition release不匹配均保持effect-none；
- normal return/cancel先active→closing，原Activity同attempt只能cleanup；普通return/cancel在各自closure manifest与Temporal终态receipt齐全后completed。Hard terminate入口先冻结durable request/physical/item manifest和assignment gate：active时直接hard-closing；已有CAN/normal/cancel时不凭absence换owner。同workflow abort-to-hard、Terminate赢得non-CAN terminal→watcher，以及CAN赢得successor→bootstrap direct hard-closing三条history都可重放；RPC ACK丢失可恢复，但即使Temporal先终态也要等物理隔离与保守settlement后才能completed；
- out-of-band non-CAN terminal撞上既有normal/cancel/CAN owner时，只有immutable terminal/no-successor证据可replay同一supersede receipt并切到watcher hard closure；Continue-As-New successor晚出现/已存在及ACK不明history不能被误supersede；
- CAN successor input逐项冻结routing revision/member-set hash/definition release/patch set；predecessor只能使用prepare Activity返回的canonical routing envelope。CAN commit、successor start、routing receipt和bootstrap ACK任一丢失均绑定同一intent/run/member；assignment current revision后来扩展、edge到期或rollback切current不能让successor追随“最新版”；
- Scope blocker freeze、hard request创建、CAN successor bind按三种提交顺序全排列：blocker始终跟assignment terminal obligation及current satisfying closure，不因原generation continued提前satisfied；hard四轴完成前logical free=0，hard完成但materialization/admin blocker未齐仍free=0，最后一个blocker齐后child→scope无环开放；
- 多日pause跨history soft budget时，waiter transfer的CAN replay保持同request/resume generation/slice/deadline/readiness，旧routing signal无效，新run可被同一resume epoch唤醒；
- old dataclass payload 缺新字段可反序列化；
- old completed Activity result 不被重新执行；
- DB assignment=0的completed history可回放；v1 payload缺字段不能被误判成legacy；
- captcha history replay；
- pre-marker混合mode history保持原命令序列可回放；fresh v1按冻结manifest确定性拆normal/deep_think segment，重放/CAN后segment ordinal、campaign/formal-leg assignment role及item ordinal不变；
- Rollback corpus至少包含一条使用当前最新marker/command branch的history；对删除该branch的旧candidate Replayer必须确定失败，orchestrator据此拒绝routing。正向candidate对live/resettable/retained corpus、sticky eviction、non-sticky、worker crash和CAN successor全部通过；“旧build能启动/API兼容”不能替代该负测；
- fresh assignment=1 workflow记录marker并由expected approved worker release继续运行；
- effect Activity拥有matching actual worker boot/poller receipt；bootstrap_continue与execution ceiling不可互换，execution在chain closing后只降级cleanup_only；
- v1 `collect_with_adapter` per-task input从外层冻结tenant/run/protocol/request identity；attempt retry、deferred新Activity和continue-as-new复用同operation，旧payload replay成功但不能进入enforced发送；
- pre-marker/v0非终态 inventory在enforce前为0。旧pending即使从未started也先终止并建立显式v1 replacement，旧workflow不执行新Activity；ambiguous attempt先unknown/人工对账；
- v1管理性pause跨原wall-clock预算时服务时钟冻结，resume后继续剩余预算；显式absolute deadline可在pause中终结并使用独立原因。Cancel signal、workflow terminate与closure reconciler竞争后只产生一次等长terminal结果；
- 新queue/build正向routing与旧queue/build负向probe都通过，生产不存在真实采集drain worker。
- completed v0被尝试Temporal reset/retry、旧server-side Schedule/Cron在enforce并发tick或之后tick、旧workflow retry chain触发时，RBAC/worker DB gate阻断全部effect并告警/终止；不会出现新v0 nonterminal或旧queue真实执行。

### 14.10 安全与旁路测试

- production mode 下 governance off 启动失败；
- production mode下正式账号binding缺失/未verified时schedule/grant失败且给出`account_binding_missing`，豆包normal/deep_think均无adapter旧路；
- 错误/未attest/revoked worker boot不能登记matched poller receipt或调用v1 execution函数；payload/env伪造expected release/build无效，旧build DB credential已撤销；
- production mode 下 local fencing + resident CDP 启动失败；
- 无 grant 的 adapter attach/send 被拒绝；
- strict-gateway production 下 probe/drill 直连 raw CDP 被拒绝；临时 cooperative profile 只有有效 purpose lease 才允许，匿名/失租/错误 purpose 一律拒绝；
- gateway upgrade/每条消息都校验 generation，旧 pause/token epoch 的 connection credential、错误 purpose/audience、重放 nonce和过期 credential 被拒绝；
- `/json/version`、日志、UI 和异常信息不泄露 raw CDP endpoint；所有 target/session 继承同一 lease；
- token/lease 不出现在普通日志、客户 API 或公开证据；
- tenant task 不能关联另一个 tenant/run 的 operation/reservation item；
- admin revoke/recover 权限和审计完整。
- 普通probe/scheduler/API角色不能直接UPDATE region auto/effective state、streak/freshness/epoch或伪造manual override；只能prepare/claim attempt、提交带matching claim token/evidence的completion，受控apply决定applied或各类no-op；force-blocked和diagnostic路径无admission写权限；
- 普通worker/scheduler/API不能直接把account置verified、改canonical external identity、改resident browser或创建quota subject/policy/bucket；只能由验证/换绑角色调用受控函数，且全局identity unique、两个verified partial unique及subject ledger约束不能被`SECURITY DEFINER`绕过；
- Temporal直接Reset/Retry与Schedule/Cron管理权限已从普通producer/operator撤销；受控代理对v0只允许审计迁移到新v1 replacement，不能原地复活旧history。
- Temporal直接Terminate权限也从普通producer/operator/运维脚本撤销；受控代理必须先提交durable hard request/assignment gate及冻结physical/item manifest，再对request当前准确actual run发RPC。Temporal终态绝不代替gateway/scope isolation和保守settlement；越权终态由watcher建立同等closure、告警并quarantine。

## 15. 可观测性和持续 invariant

至少暴露以下 metrics，并按 platform/region/instance、masked account 聚合：

```text
execution_grant_requested_total
execution_grant_granted_total
execution_grant_partial_total
execution_grant_deferred_total{reason}
execution_grant_rejected_total{reason}
execution_grant_active
execution_grant_expired_total
execution_grant_unknown_total
quota_reserved_units
quota_debited_units
quota_success_units
quota_unknown_units
quota_projection_drift
quota_calendar_rollover_lag_seconds
browser_fence_acquire_wait_seconds
browser_fence_heartbeat_failure_total
browser_fence_lost_total
browser_fence_quarantined
browser_fence_stale_write_blocked_total
browser_restart_recovery_total
browser_supervisor_inflight_os_actions
browser_supervisor_adopt_barrier_seconds
browser_supervisor_startup_reconcile_pending
browser_supervisor_os_action_unknown_total
worker_runtime_scope_state
worker_scope_recovery_active
worker_scope_blast_set_drift
browser_raw_listener_blocked
browser_context_tainted
browser_gateway_epoch_ack_seconds
browser_gateway_resync_fail_closed
browser_credential_pending_age_seconds{purpose}
browser_credential_lineage_violation_total{reason}
submission_dispatching_age_seconds
submission_unknown_total
capture_staging_recovered_total
capture_projection_conflict_total
governance_outbox_lag_seconds
governance_outbox_failed_total
execution_waiter_live
execution_waiter_wakeup_lag_seconds
execution_waiter_stale_claim_blocked_total
execution_waiter_transfer_total{state}
temporal_history_budget_ratio
collection_region_probe_lag_seconds{region,producer_kind}
collection_region_probe_receipt_total{region,apply_result,outcome}
collection_region_probe_claim_reclaimed_total{region}
collection_region_probe_stale_claim_total{region,reason}
collection_region_health_epoch{region}
collection_region_half_open_age_seconds{region}
collection_region_manual_blocked{region}
collection_account_binding_conflict_total{platform,reason}
collection_quota_subject_duplicate_rows{platform}
collection_quota_config_gate{state}
collection_quota_scope_gate{scope_kind,mode,state,reason}
collection_quota_cutover_age_seconds{phase}
collection_quota_scope_recovery_age_seconds{scope_kind,mode,state}
temporal_workflow_patch_replay_failure_total{workflow_type,definition_release,patch_id}
temporal_workflow_definition_routing_mismatch_total{workflow_type,expected_release,actual_release}
workflow_start_operation_total{state}
workflow_start_retry_safety_seconds
workflow_start_resolution_age_seconds{state}
workflow_chain_closing_age_seconds
workflow_chain_bootstrap_rejected_total{reason}
workflow_termination_ingress_total{state,resolution_kind}
workflow_post_terminal_intent_total{kind}
workflow_assignment_terminal_pointer_drift
workflow_late_intent_effect_violation_total{effect_kind}
workflow_hard_termination_request_age_seconds{state}
workflow_termination_root_intent_total{kind}
workflow_termination_escalation_total{old_strength,new_strength}
workflow_hard_request_normal_effect_violation_total
workflow_hard_request_follow_successor_total{outcome}
workflow_terminal_closure_lag_seconds
workflow_hard_terminate_physical_barrier_lag_seconds
workflow_hard_terminate_settlement_lag_seconds
worker_scope_physical_isolation_receipt_lag_seconds
worker_scope_release_blocker_pending{kind}
worker_scope_physical_sealed_not_released
worker_boot_attestation_rejected_total{reason}
activity_poller_gate_rejected_total{reason}
task_materialization_lag_seconds
task_materialization_poison_total
run_materialization_pending
run_resolution_projection_lag_seconds
sampling_candidate_command_lag_seconds
sampling_candidate_command_poison_total
sampling_expected_cells
sampling_observed_cells
sampling_completed_samples
sampling_cell_projection_drift
sampling_primary_slot_unfulfilled
sampling_primary_role_drift
sampling_prebind_partition_drift{reason}
sampling_primary_slot_invalid_intent_total
captcha_handoff_detach_barrier_seconds{phase}
captcha_handoff_stale_ack_rejected_total{phase}
captcha_owner_heartbeat_age_seconds{purpose}
captcha_effect_permit_total{dispatch_state}
captcha_logical_action_sealed_total{reason}
control_plane_incompatible_journal{component,release}
control_plane_retained_reader_gap_total{component,schema_version,record_kind}
collection_pause_operation_seconds
collection_submit_permit_active
```

告警至少包括：

- unknown 新增立即告警；
- region probe长期无新applied receipt、freshness过期、stale/expired claim或stale-observation/contract-rejected突增、open/half-open超时、同region多个half-open attempt、force-block期间出现任何projection delta、manual override临近过期未复核，或auto/effective/override/health epoch与event ledger漂移；
- expired live reservation；
- held_unactivated/held fence heartbeat或credential activation期限过期；
- fence/reservation token mismatch；
- quota finite bucket exposure 超限；
- quota projection drift；
- quota config gate长期draining/blocked、cutover/recovery claim过期、scope gate与policy requirement漂移、deep_think局部block误伤normal、calendar rollover/reconciler停摆、overlap transfer不守恒或legacy projection bucket key落后；
- account running 无 live reservation；
- active reservation对应quota subject/account/browser binding漂移；canonical identity或resident browser verified重复、conflicted binding被选入、同subject出现多套active policy/bucket立即P0告警；
- outbox lag/poison；
- waiter wakeup lag、claim过期却因snapshot变化无法安全回ready；
- parked workflow history接近soft/hard budget却没有prepared/bound waiter transfer，或transfer长期closing/transferring；
- workflow start长期`rpc_dispatching/outcome_unknown/waiting_start_resolution`、retry安全窗临近耗尽、pause snapshot漏记start operation、同assignment出现第二termination root或strength/set倒退；assignment父级root/terminal pointer、obligation epoch与child漂移，统一ingress缺/多child或同key合同冲突，rootless normal terminal后出现owner/RPC/resource/epoch delta立即P0；chain长期`bootstrap_pending/closing`、无授权bootstrap、Temporal已终态但chain仍`bootstrap_pending/active`；termination request长期waiting/following且未收敛到准确actual run/owner、request后出现normal effect、合法CAN successor未继承request，或hard terminate已Temporal终态却physical barrier/item settlement未齐；任何Workflow Task实际definition release/build与assignment/Temporal pin不符、候选build Replayer nondeterminism、已登记patch ID hash改义或旧branch被删除同样立即阻断发布；
- worker boot attestation/poller receipt不匹配、已撤销boot仍领取Activity；
- run execution已terminal但materialization长期pending/poison、expected item count/hash与resolution receipt漂移；
- sampling policy/campaign cell、origin-intent leg成员或primary-slot count/hash漂移，slot state/current revision/授权origin与当前primary assignment不符，current slot指向cancelled/superseded intent、prebind partition成员有漏/重叠/continuation孤儿、未受影响leg因partial replacement长期awaiting、supplemental被误标primary、generic producer越权尝试、candidate command lag/poison、observed cells不等于唯一lifecycle receipt、ineligible/degraded候选被计数；
- captcha任一detach/barrier ACK超时、迟到或direct predecessor错链，owner heartbeat/capability过期，permit dispatching长期不终结，同logical action出现多forwarded/unknown successor，或白名单外dirty owner/purpose边尝试；
- quarantine 未恢复；
- supervisor/gateway release或reader inventory与boot attestation漂移、retained record无reader、`incompatible_journal`、未知terminal/schema、超时未终态OS job、旧boot journal未reconcile、outcome_unknown、adopt/seal barrier卡住，或startup/quarantine/recovering期间raw listener对worker可达；
- worker runtime scope长期emergency/recovering、blast member/release-blocker count/hash漂移、同一scope出现多个live recovery owner，或scope gate后仍签发normal grant/permit；physical seal长期无isolation receipt、receipt后hard/blocker未收口、hard未完成却出现free，或全部blocker完成后logical release/scope open长期卡住；
- held_unactivated/credential_pending超时、held缺matching install receipt、detach/purpose barrier/install direct lineage断裂；
- tainted context没有reset/restart receipt却准备free，或capture projection出现同generation hash冲突；
- pause 状态下出现新的 dispatching timestamp。

管理页需要能回答“为什么这题没有数”：未获 grant、明确未发、已发送未捕获、unknown、wall、quota block、fence lost、staging 恢复等原因不能混成一个 `failed`。

### 15.1 必须落地的 invariant 检查

不要只在文档写公式。新增可重复运行的只读 SQL/命令（建议 `scripts/verify_collection_execution_invariants.py` 和对应 SQL fixture），输出检查名、违规数、有限样例 ID 和执行时间；正常值全部为 0。至少覆盖：

1. **Quota effect 守恒**：每个 bucket 的 reserved/debited/captured/success/unknown 等于该 bucket 全部 baseline/effect delta 之和；所有计数非负，`success <= captured` 且 `captured + unknown <= debited`。
2. **授权容量**：verified finite bucket 满足新 grant 条件。`reserved + debited > limit` 只能是带审计的 admin-down 历史 exposure，必须有 `grant_blocked_reason/blocked_at`，且 blocked 后不存在正向 reserve effect。
   2a. **Region/relay observation 单调性**：每个region满足`0 <= applied_projection_generation <= terminal_attempt_high_watermark <= next_probe_generation`，current claim generation、config/override/admission-barrier/health epoch和auto projection version单调；terminal receipt的old/new重建terminal high-water，projection-action receipt与health event的old/new重建授权投影，二者不混用。Current `last_projection_event_id`逐项等于immutable event的new授权投影（明确不比较terminal high-water）；每个reservation复合FK到其grant时immutable event且后续current更新不改写历史。所有terminal winner都只单调推进terminal-attempt high-water，只有`projection_action != none`才推进applied projection；任何业务stale判断读取terminal high-water的计数必须为0。Admission barrier只由config/manual/policy管理边界推进，自动probe成功/失败及其health epoch/gate变化推进barrier的计数必须为0。每个`completed/timed_out` admission attempt恰有一个terminal-winner receipt，每attempt最多一个非none projection action；current合法claim的stale observation、diagnostic、barrier mismatch和contract reject也必须关attempt，只有`stale_claim_noop/expired_claim_noop`不是terminal winner且不能关闭。`stale_observation_noop/stale_claim_noop/expired_claim_noop/manual_blocked_noop/admission_barrier_epoch_noop/config_epoch_noop/override_epoch_noop/policy_epoch_noop/diagnostic_noop/contract_rejected`的projection action为none，auto/effective/streak/freshness/epoch delta恒为0；`half_open_inconclusive_closed`只能fail-closed转open。Auto projection可由projection-action receipt与config/manual/policy/baseline health event共同重建，effective projection严格等于configured/manual/auto纯函数。每个half-open窗口至多一个active probe，matching half-open terminal winner后active generation必为NULL。Force block期间effective必为manual_blocked且任何auto delta=0；clear或disable→enable后无fresh ok，必须由新barrier epoch的generation重新恢复。每条live reservation的region policy/health epoch/applied projection来自grant时有效ok/fresh projection；begin只要求current applied generation不回退，不要求相等；health epoch变化后新dispatch为0，旧dispatch只settle。对同一admission barrier的g1 success后到g2 hard failure，g2必须在current auto projection上应用；因g1自动转换推进health epoch而把g2 no-op的计数必须为0。对同一barrier的g1 valid hard failure晚于g2 invalid/contract/barrier/diagnostic terminal时，g1仍必须应用，g2不得通过审计关单取得supersession权。不存在旧healthy覆盖新failure、无效高代压掉有效低代、旧claim覆盖reclaim结果或manual block被任何auto/diagnostic completion解除。
   2b. **Quota subject治理连续性**：每个external identity恰一条subject global governance projection、每个subject+canonical mode恰一条mode projection，均可由唯一receipt/event重建。Platform wall/mute/rate-limit/hard block与streak在account revoke/recreate、binding revision/browser/region切换前后subject ID/version连续；切换后任何无evidence归零、较旧receipt覆盖或把subject级block降为session级的计数为0。Account legacy字段只等于subject当前绝对投影，不被授权路径读取。
   2c. **Quota配置、稳定scope与calendar连续性**：每个quota subject恰一条config gate和一个current verified policy；gate的每次`open -> draining`与`draining/blocked -> open`都各推进epoch，旧reservation/permit跨任一次切换仍可normal的计数为0。Stable scope业务身份不含policy revision；每个current policy requirement与所需stable scope/policy-scope revision集合双向anti-join为零，非required mode scope不会误阻断本mode。相同calendar contract换policy后bucket ID/exposure连续且delta守恒；calendar合同变化有共同边界零exposure证据或唯一conservative overlap transfer，old/new重叠bucket的reserved/debited/captured/success/unknown均守恒，late old-bucket effect恰一次进入reconciliation。每个blocked/unverified scope只能由matching expected-epoch recovery receipt开放；day/week/year rollover、人工adjustment和ACK重试不重复推进，deep_think-only block期间normal required scopes仍open。Config gate、scope gate、policy-scope effective-to或required-set hash不匹配的live grant/captcha joint grant normal effect数为0。
3. **Live owner/额度主体**：每个account及每个quota subject最多一个live reservation、每个instance最多一个held_unactivated/held/revoking owner、每个request最多一个live grant generation。同platform official stable subject hash只有一个永久subject，不同alias scheme/canonicalizer不能拆分；每subject只有一个verified binding和一套active quota policy/bucket；同resident browser最多一个verified binding。
4. **Grant/fence 一致**：live reservation 的 account/browser/lease/token/acquired boot/holder/worker runtime scope及scope epoch与当前 fence、holder boot receipt相符；current browser boot、worker InvocationID/boot或scope epoch不同则 fence 必须cleanup-only/quarantined/recovering，不能 held 可写。
5. **Item 状态合法**：每个 granted ordinal 恰有一行；terminal item 有唯一 disposition/settled time；accepted 不得 released/unknown；dispatching stale 最终保守 unknown；未 grant tail 有 verified terminal manifest但无 quota effect。
6. **Permit/pause**：control=paused 时没有未过期 submit permit、dispatching window、可写 collection/captcha holder；pause epoch 后不存在新签发 permit。
7. **Staging/capture**：同 `(operation,capture generation,result kind)`唯一且hash稳定，不同generation可append并存；capture attempt的selected staging同operation/request/generation且verified，DB hash/object head/required manifest一致；operation最多一个captured promotion。缺失/corrupt单独列出且禁止重发，terminal manifest不被late answer覆盖。
8. **Task provenance/materialization**：每个v1 CollectionTask唯一绑定request item；每条revision的operation tenant/run/business key/mode与Task/request item相同，获grant时reservation item也一致；成功答案的capture attempt/staging/content hash与选中verified payload一致；selected revision可来自人工批准的新operation而顶层initial operation仅作审计。Persist policy的command/receipt按source generation无缝连续且selection不回退，suppress policy有suppression receipt；没有“既无Task也无抑制证据”的item。每个request item恰一条run-item resolution receipt：persist来源为唯一lifecycle receipt，suppress来源为唯一suppression receipt；run resolved只计一次，execution terminal不能早于全部item事实，customer completed不能早于全部resolution及零pending/poison。Class transition净delta可重建。Task-success command/receipt按task/request item唯一，冻结selected revision/version及其operation/reservation/capture/staging/hash，bucket set/hash等于该revision item冻结集合。v0历史NULL link与v1缺link严格区分。
9. **Outbox/receipt**：delivered outbox 必有 matching receipt；receipt contract hash 与 event 相同；pending claim 过期可重领；同一 event 不产生两次 projection；poison 单独非零告警。
10. **Fencing 单调/恢复**：event token 单调，数据库当前 token 等于最后事件 token；free不带holder，held_unactivated只有pending owner且effect none，held字段齐全并有matching credential install receipt。Reservation/fence activation同receipt原子；release/recovery completion有匹配old/new boot、holder process identity、gateway或kill/restart证据；supervisor无低generation未终态OS action；free context必须clean且有reset/restart receipt。
11. **逻辑发送唯一性**：同 operation key/submission generation 最多一个 dispatching/accepted/unknown 事实；多个 Temporal attempt 不产生第二次 send receipt。
12. **兼容 projection**：legacy `used_today/week/year` 等于当前 calendar bucket 的 success projection；延迟历史 success receipt 不污染当前桶。
13. **协议隔离/Temporal定义/链终态**：run/assignment/start-outbox/request/task协议版本一致；control最低版本下没有v0非终态workflow或pending/claimed start；expected activity release/queue/deployment/build/digest与可信worker boot/actual poller receipt一致。Assignment/start/CAN input的workflow definition release、patch-set hash、versioning behavior及expected Workflow Task deployment/build与Temporal实际routing一致。Baseline marker一旦产生v1 history便不改义；每个后续command-sequence变化使用全局唯一patch ID并保留旧branch，registry hash与release manifest一致；无证明pin时所有活history对候选build Replayer=pass，有证明pin时每个run lifetime只到兼容build，AUTO_UPGRADE仍有patch。Activity DB gate不被计作Workflow Task兼容证据。每个normal effect的Temporal run ID属于连续、已bound active chain generation；bootstrap只对应matching intent并有唯一bootstrap receipt，cleanup只对应同run active/closing allowlist；Reset/Retry无intent run不存在effect。每个closing chain恰有一个matching kind/operation/contract live owner，prepare ACK丢失可恢复且异kind不能冒领；closing supersede receipt只对应old superseded→watcher hard owner及immutable non-CAN terminal/no-successor evidence。Live hard request存在时所有normal effect为0；它要么拥有当前hard owner，要么明确等待cooperative/Temporal排序，要么被matching CAN successor bootstrap/closing-only termination-reconciler bind直接继承为hard owner。Reconciler bind必须有continue/start/intent/input证据且active bind计数为0。History absence产生hard owner的计数必须为0；abort-transfer必须来自actual patched workflow且intent/transfer rejected、old aborted、new hard owner、epoch/manifest/receipt同commit。Temporal terminal run不得残留active chain，completed/continued generation不得新写effect，closing年龄有界或带人工恢复告警。
    13a. **Assignment终止线性化与幂等**：assignment父行的root/terminal pointer、termination-obligation epoch/state version与同assignment child/event完全一致且单调。每个resolved termination ingress按全局key恰解析为一个root alias或一个post-terminal receipt，同key跨assignment/合同、两类child并存、prepared跨commit均为0；root-alias在terminal前允许NULL结果并稳定返回202，完成后只指向matching immutable assignment receipt。Assignment terminal receipt三条路径严格互斥：rootless normal必须有最终chain/closing/Temporal/item/materialization证明且root pointer为NULL；termination no-run必须有gen0 rejected、start confirmed-not-forwarded/cancelled、零actual run/RPC/physical target及全量neutral materialization；termination chain必须有satisfying cooperative或hard/watcher closure及准确Temporal/item/physical/captcha证明。只要start operation未matching终结、任一chain仍`intent/bootstrap_pending/active/closing`、CAN successor未决或物化集合不齐，receipt数必须为0。Normal receipt先赢后的post-terminal intent不得创建root/owner/RPC/claim/resource/blocker或推进effect/obligation epoch；root先赢时rootless receipt数必须为0。任何normal effect只能发生在父行两个pointer均NULL且chain active的同一锁事务内。
14. **Request closure**：service-clock deadline单调且pause期间冻结；absolute deadline、cancel和terminal policy每个request最多一个closure winner；waiting item未到关闭条件时没有最终manifest/task。
15. **Work claim顺序**：过期或被替换的control/recovery/release/outbox claim不能推进phase；operation apply receipt中的资源version与最终claim-token CAS一致。
16. **Waiter可恢复性/历史预算**：每个request最多一个live waiter；claimed字段与冻结request/effect/grant/fence/continuation snapshot完整，consumed绑定matching reservation/generation，cancelled绑定closure receipt。Expiry回ready前全部snapshot逐项未变；任何变化后的回ready计数必须为0。Transferring waiter恰有唯一transfer/next intent，successor waiter保持request/resume generation/slice/deadline/readiness而routing generation+1；旧route claim=0。History soft budget前触发CAN、hard budget无越界，长期pause不会无限增长history。
17. **Captcha owner/permit/closure证据**：每次collector/assist/capture/collection owner或purpose变化的token、owner transition version与短heartbeat generation单调；captcha_pending、resume_ready_detached/claimed及credential pending引用direct predecessor正确phase的detach/barrier/scope-exit或same-holder purpose-barrier receipt。Owned-dirty跨purpose只存在`collector→assist`、`assist→capture_root`、同holder/process/scope/connection的`capture_root→collection`三类白名单边，任何第四类边为0。Pending effect none，held/active才有matching current install/capability receipt；detach反向引用old install。每个logical action slot的permit generation连续且只有`terminal_confirmed_not_forwarded`允许唯一successor，forwarded/unknown永久封口；issued/dispatching必须在pause/hard/quarantine manifest中，expiry不能冒充未转发。旧holder/purpose在barrier ACK后无forwarded frame，prepare/install/activate/heartbeat/permit ACK丢失不重复bump token或action。Quarantined continuation仍占live unique且绝非业务terminal；只有matching physical seal之后，唯一continuation-closure operation把所有historical transition/action/permit、item/materialization与owner集合双向settle，并写`terminal_cancelled/terminal_recovered` receipt，browser才可logical free、scope才可open。缺closure receipt却free、terminal前新permit、closure把unknown当not-forwarded或遗漏任一成员的计数均为0。
18. **Run解析/终态**：run冻结execution request-item count/hash等于真实ordered集合；每题恰一resolution receipt，resolved/materialized/suppressed projection可由receipt重建；late revision不改变run item set或重复resolved。Customer completed/cancelled/failed发布满足冻结terminalization policy且materialization complete，poison/pending不会伪装完成。该计数不作为sampling observed cells。
19. **正式绑定**：stable account header的current pointer始终以同account composite FK指向唯一current binding revision；每个live/历史grant的binding revision/version、quota subject、official stable subject hash、identity alias/scheme/canonicalizer/hash、resident browser和region与授权时immutable composite snapshot相符。Rebind只追加revision并原子retire-old/verify-new/切pointer；历史grant FK不漂移，header非active或revision非current/verified后不存在新reserve effect。每个platform+official stable subject全局恰一subject，跨scheme alias必须归入同subject；verified/draining subject和resident browser映射均至多一条。Quota policy/scope/bucket/effect与subject级governance全挂subject，撤销/重建账号行后没有新余额、第二套limit或被清空的wall/mute/mode block。
20. **Formal leg/主批次/采样进度**：campaign policy/formal-leg/query/cell集合经双向anti-join且count/hash守恒；当前fixture为136×6=816。Formal-leg与frozen primary slot一一全覆盖，origin-intent leg成员与header count/hash一致，slot current revision以同slot复合FK引用可消费的verified canonical-main intent，current slot指向cancelled/superseded intent的计数为0。Slot state与当前matching assignment一致；每个run+formal leg恰一role assignment；当前primary必须且只能匹配slot revision的origin/membership/lineage/version/occurrence/run class，supplemental先到或先完成不占slot，未经audited replacement不会换主。每个未绑定multi-leg partial replacement都有唯一partition receipt，predecessor成员严格等于replacement与continuation成员的不交并集，每个old member恰有一个successor slot revision；remaining leg无continuation、映射重叠/漏项、old bind与partition双赢、或未受影响slot被孤立的计数均为0。已绑定subset replacement只改变目标leg。Segment/source-mode映射符合verified policy。Candidate command与Task revision一一对应且不跨cell；`completed_samples`等于eligible且non-degraded candidate completion receipts，`observed_cells`等于唯一cell lifecycle receipts，expected cells等于冻结cell set。Run resolution、wall/suppress/ineligible/degraded、grant/Task/segment计数及到达顺序都不能误推进observed或primary；同cell补采/selection换版不重复observed，genuine dual-mode才形成不同cells。
21. **Worker scope/OS动作闭包**：每个worker boot恰好属于一个稳定runtime scope，normal poller/effect冻结scope epoch且只在scope open/current InvocationID+boot时成立。每个live scope recovery owner唯一，冻结member/release-blocker set与关gate线性化时同boot的全部live lease双向anti-join为零。Scope physical-isolation receipt的旧unit/holder exit、resource/member、child new-boot/context/gateway seal、supervisor journal/reconciliation集合全部可重建且零漂移；receipt提交时fence仍recovering/quarantined。每个member blocker terminal后才logical free/completed，全部member释放后scope才能open。Supervisor所有accepted/in-flight/outcome-unknown journal均可对账到terminal receipt；当前ready boot不存在旧boot未终态相交resource action，两个instance不能各自对同一unit完成seal。
22. **Hard terminate三重完成门**：`kind=hard_terminate/watcher_recovery`的每个completed chain都有唯一closure；physical target count/hash、全量未闭合request-item settlement/closure count/hash与真实冻结集合双向anti-join为零。每个target具matching exact clean-release、gateway barrier、scope-exit或经exact scope member绑定的worker-scope physical-isolation receipt；仅scope operation ID、unit stopped或child seal projection计数必须为0。Dispatching/forwarded/accepted item有保守terminal/staging/governance/quota settlement，waiting/never-granted tail有neutral terminal manifest和materialization/suppression command且无quota effect。Temporal terminal event、physical isolation、effects settlement三者缺一时chain只能closing/quarantined，browser不得free/regrant，客户终态不得发布；physical receipt可以先于hard completed产生，但hard completed前member logical free必须为0，hard completed后仍须等其余release blockers才释放。
23. **Control-plane journal/发布可读性**：每个retained supervisor/gateway journal record按component/release/schema/record kind/hash恰有一个可验证terminal或明确outcome-unknown状态，并被当前、canary及rollback manifest中每个可能启动release的精确reader member接受；reader count/hash、boot attestation、DB mirror与持久卷inventory双向anti-join为零。Writer enable只发生在所有保留reader先expand并通过完整corpus之后；unknown schema/record/terminal、checksum损坏、磁盘只读/满、旧boot未reconcile时startup-ready/raw listener/grant均为0。Compaction只删除已具DB terminal mirror且过保留期的记录并留下receipt。任一restore/rollback后，DB schema/definer ACL manifest、control-plane release/reader inventory、journal corpus及network-isolation contract必须与冻结manifest一致；通过清journal、降schema或skip unknown报告ready的计数为0。

每次 migration、部署、canary 扩级和回滚都保存机器可读 JSON 结果。对允许的历史 admin-down exposure 使用显式、有期限/责任人的 waiver 表或审计 ID，禁止在 SQL 中硬编码忽略账号。

## 16. 上线、恢复和回滚

### 16.1 上线顺序

```text
保持全采集停止
-> 备份、记录部署 commit/schema/invariant baseline
-> expand migration
-> 部署新 API/repository/reconciler，feature off
-> 部署/升级node supervisor，保持raw端口关闭并完成旧OS job journal startup reconciliation
-> 部署新 worker，shadow/fake 模式
-> 跑 migration + PG concurrency + replay + killpoint tests
-> 排空/终止旧 Activity，停止旧 worker
-> 确认无旧 live fence/reservation/captcha holder
-> contract migration + DB protocol enforce
-> admission-only 演练
-> 用户批准真实 canary
-> 单题 normal
-> 单题 deep_think
-> batch cap 1 -> 2 -> 4
-> 单地域 -> 多地域 -> 其他平台
```

每一级必须生成结构化验收记录，包括 DB invariant、Temporal history、真实 submit count、quota delta、fence token/boot、task/outbox 和 sampling progress。

### 16.2 恢复采集前硬门槛

全部满足才允许恢复：

- expand/contract schema、old/new binary compatibility、migration 和权限测试全部通过；
- 所有 finite current account/mode day/week/year bucket baseline 已有平台/人工/reset 证据并签字；任何 unverified scope 仍保持 grant=0；
- 每个quota subject的config gate/current policy/stable required-scope成员与policy-scope revision双向anti-join为0，calendar/overlap transfer守恒；任一gate draining/blocked、scope baseline unverified、epoch/hash/effective-to漂移都保持对应mode grant=0，不能靠换policy建新bucket清零；
- 豆包北京/上海normal、deep_think及其他待恢复platform/region/mode组合都有verified正式账号binding、binding version/canonical identity/resident browser证据；缺任一组合即保持scheduler/grant阻断，绝不以env/CDP旧路补齐；
- 所有真实 PostgreSQL 并发测试通过；
- assignment父级root/terminal pointer、统一termination ingress、三条terminal path、gen0 start/no-run/bootstrap-pending和late post-terminal竞态的PG/killpoint/invariant全部通过；不存在rootless normal receipt+root裂脑、同ingress双分支、terminal后新增owner/RPC/resource/obligation或start unknown被另request ID重启；
- 所有待启用region都有新协议生成的fresh success receipt，probe generation/receipt/event与projection invariant为0；scheduled/manual/startup/half-open乱序、ACK丢失和override演练通过，旧probe不能覆盖新结果，HTTP probe期间同region不同账号/platform grant并发性通过；
- 所有发送边界 killpoint 通过；
- 旧 Temporal history replay 通过；
- Temporal 非终态 inventory、task queue 和worker artifact release/build routing已留档；assignment expected release/digest/config与可信boot/actual poller receipt逐项一致，old worker不可能承接enforce任务，ambiguous old attempt已fail-closed处置；
- 所有run/start producer与consumer已升级或停止；旧Temporal Schedule/Cron/retry/reset来源已停用并有server/RBAC receipt；protocol enforce用control行锁线性切换，v0非终态和v0 start-outbox pending/claimed均为0，旧DB role不能再写v0，旧queue出现poller或新v0 history会立即告警/阻断；
- production bypass 被关闭；
- 普通主体的Temporal Reset/Retry/Terminate与旧Schedule/Cron入口权限已撤销；hard request的assignment gate、冻结physical/item集合、active direct-close、actual-workflow abort-transfer、non-CAN terminal watcher及CAN successor bootstrap/closing-only reconciler direct hard-close演练通过，history absence takeover=0；gateway/scope isolation、保守settlement和Temporal terminal三重门通过，RPC先终态或ACK丢失均不能提前completed/free/regrant。Shared-scope路径已证明physical-isolation receipt可在recovering内先形成且足以推进physical axis，但hard四轴前free=0、其余release blocker未齐时free=0，最后按child→scope无环释放；
- 所有 browser 已建立可信 current boot identity；abnormal fence 已 quarantine，并由 gateway ACK 或“旧 holder 已终止 + browser 已 restart”的组合证据阻止旧 generation 后续消息；已转发/tainted page 已 quiesce+reset 或重启；recovery backlog=0；
- 每个browser的current context generation、health/readiness projection、fence token/boot和release receipt逐项匹配；所有owned-dirty只走三条白名单transition，所有曾quarantined captcha continuation均在matching physical seal后有完整closure receipt并已到真正terminal，缺一项时browser仍不得free；
- supervisor按相交physical resource set串行、adopt/physical-seal barrier、pidfd/cgroup holder隔离和OS action receipt演练通过；共享worker有runtime-scope gate、同事务完整blast-member/release-blocker set、唯一scope owner和append-only physical-isolation receipt，扫描并发grant/跨instance同unit恢复均通过；supervisor旧boot fsync journal startup reconciliation为零，outcome-unknown为零；browser自动重启受控，startup reconcile/boot commit/physical seal前raw listener对normal worker不可达，logical release前仍无normal credential；
- 当前/候选/rollback supervisor与gateway release、artifact/config/boot attestation、journal writer schema及每种retained record/terminal的reader-member count/hash均与实际持久卷corpus和DB mirror匹配；未知/损坏/outcome-unknown record为0。SECURITY DEFINER/trigger函数body hash、owner、search_path、row_security、ACL/RLS及runtime role membership与冻结catalog manifest一致，PUBLIC/private helper/direct table DML为0；备份restore演练同时恢复并核对这两套manifest后才允许startup-ready；
- control 当前真实状态是 `paused` 而不是 `pause_requested`；active submit permit、dispatching、live grant、captcha handoff和具有写能力的 fence 均为 0；
- global pause 竞争测试以及 pause→resume 端到端通过，等待题没有被永久写成失败；
- 多日pause的history-budget/waiter-transfer/CAN演练通过，old route signal/claim=0且resume后同request/slice继续；chain closing operation/bootstrap receipt无悬空或异kind冒领。Out-of-band terminal与既有cooperative owner的audited supersede演练通过，non-CAN/no-successor才转watcher hard closure，合法/不明CAN successor绝不误终结；
- control服务时钟与request service deadline审计一致；管理性pause跨wall-clock预算不会误终结，显式absolute deadline仍按policy执行；
- invariant SQL 全为 0；
- sweeper、reconciler、quota calendar rollover、governance outbox、restart outbox 服务已启用且健康；claim lease 正常、lag=0、poison=0，零事件跨日projection演练通过；
- durable staging CAS 的 write/read/hash、ACK-loss 恢复、孤儿 GC 和 backup/restore 演练通过；
- browser credential pending→install→activation以及detach/purpose-barrier direct lineage的ACK-loss演练通过；held_unactivated/pending超时全部quarantine且无effect；
- sampling campaign/formal-leg policy、launch plan、每leg primary slot/revision 0及canonical-main origin intent已freeze并通过双向anti-join；补采/run-now先提交仍supplemental、正确主计划后到仍primary、错误lineage拒绝、已绑定replacement与未绑定multi-leg partial-partition fixture均通过，所有current slot都不指向失效intent且remaining continuation可正常成为primary。当前136×6=816 cells，豆包normal补采/deep_think同cell、genuine dual-mode拆cell的fixture通过，observed/completed samples可由receipt重建且projection drift=0；
- grant ACK 丢失跨 worker、`to_thread` 僵尸、transient denial 重评、captcha resume adopt、receipt 中途 crash、task-link 锁序均有自动化证明；
- canary unknown=0、duplicate send=0、quota delta 精确；
- canary 一旦出现 unknown、projection drift、duplicate send、raw bypass 或 token/boot mismatch，自动/人工立即 pause，停止扩容并完成对账；
- 用户明确同意恢复。

### 16.3 回滚

回滚是：

```text
pause/drain
-> 停止新 grant
-> 继续运行 heartbeat/sweeper/settler/outbox 直到 live=0
-> quarantine 不确定 fence
-> worker runtime scope进入draining；确认supervisor journal无in-flight/outcome-unknown且raw端口保持关闭
-> 冻结supervisor/gateway release、boot attestation、retained journal reader inventory和network-isolation contract
-> 冻结workflow definition/queue/Worker Deployment routing revision并导出全部live/resettable history集合
-> 候选Workflow Task definition对该集合全量Replayer通过，且routing preflight证明不会把history送到不兼容build
-> 停新 worker；保留所有仍被历史需要的compatible Workflow Task worker
-> 只回滚已证明兼容的组件，或保持feature disabled
```

“应用版本可回滚”与“Workflow Task definition可回滚”必须分开判定。每个候选rollback release必须登记自己的`workflow_definition_release_id/patch_set_hash/source digest/SDK version`，包含每条live history以及所有仍可Reset/Retry/reopen、待archive或在保留期内history所需要的全部patch ID和旧branch，并由Temporal Replayer对冻结corpus逐条通过。API schema兼容、Activity输入兼容、DB poller gate通过或候选版本号更旧，都不能证明Workflow replay安全。

回滚控制面先将global置paused，锁定发布操作与task queue/Worker Deployment routing revision，停止自动ramp/current切换，再在同一个机器可读rollback manifest中冻结：history inventory hash、reset/retry权限清单、候选definition/routing revision与required patch/member set、queue/deployment/build路由、仍需保留的旧worker及回退后Activity/API release；同时冻结当前与候选supervisor/gateway release ID+artifact/config digest、boot attestation、control protocol范围、retained supervisor/gateway journal inventory count/hash、每种journal schema/record/terminal reader set、DB receipt/credential/barrier/captcha-effect合同和network-isolation policy hash；还要冻结Alembic head、所有SECURITY DEFINER/trigger函数signature+body hash+owner+`search_path/row_security`、schema/table/function ACL、RLS/FORCE状态、runtime role membership和批准manifest hash。锁后必须再次读取Temporal routing/visibility、DB assignments/boot registry、本地journal inventory与PG catalog；任何漂移使本次回滚CAS失败并重新规划，不能边回滚边让新history、marker、journal schema或definer函数/权限进入集合。

候选supervisor/gateway只有在离线及真实滚动演练中能逐条解析、reconcile并保持幂等地read-back全部retained records/DB mirrors，且其writer协议仍被未回滚peer接受时才可切换。必须包含“新版本已写journal→启动旧binary”“未知terminal kind/record hash”“gateway升级一半”“supervisor commit receipt ACK丢失后回滚”负测；预期结果是候选被orchestrator拒绝，或旧binary保持`incompatible_journal`且raw端口关闭，绝不能把未知记录跳过后报告pending=0/startup-ready。备份restore也必须把DB backup、journal持久卷corpus和上述machine-readable manifests作为一个恢复集合验证：任一receipt引用的journal缺失、任一retained record无reader、或catalog/ACL hash漂移都保持paused/startup-not-ready。无法证明时保留当前兼容supervisor/gateway，只回滚独立证明安全的API/Activity，或继续全局paused。

发布门禁必须包含一个反向演练：取一条已经记录当前最新patch marker/command branch的history，对不含该patch的旧候选build执行Replayer，确认它失败；rollback orchestrator必须因此拒绝把该history的Workflow Task路由到旧build。该负测证明门禁真正根据history兼容性阻断，而不是把“旧build能启动”误当安全。正向则要求候选对全部冻结corpus成功，并在sticky eviction、non-sticky replay、worker crash和CAN successor下仍落入compatible release。

若旧候选缺任何已记录patch或Replayer/routing证据不完整，**不得回滚Workflow Task worker**。此时保留当前compatible Workflow Task workers和其queue/build路由，只允许在接口合同独立证明后回滚无Workflow command语义变化的API或Activity组件；或者继续保持所有新grant关闭。只有已验证的per-run PINNED能力可让特定run继续留在其exact旧release，且必须有Temporal实际routing receipt；DB assignment自报或Activity worker版本不能代替pin。AUTO_UPGRADE/unversioned路径始终要求候选能replay所有可能路由到它的history。

Continue-As-New也属于上述history集合：rollback manifest必须核对每个prepared next intent冻结的successor definition release/compatible set和actual routing。不能让回滚把predecessor留在含新marker的definition、却把successor隐式路由到缺branch的旧build；不能修改已冻结intent来迁就候选。无法证明时保持chain closing/paused并保留compatible worker处理原intent，或在完整终止/settlement后创建显式replacement，不得伪造CAN。

禁止：

- 回到 env fallback 继续采集；
- 在 live reservation 未清空时启动旧 worker；
- truncate/delete ledger 或 browser_fence；
- 重置 fencing token；
- 删除/重置worker scope epoch、supervisor command journal或伪造startup reconciliation ready；
- 把retained supervisor/gateway journal交给registry中没有精确reader member的旧binary，或通过忽略未知record/terminal kind、清journal、降schema version来强行startup-ready；
- 把 unknown 自动改成 released；
- 为了回滚在生产执行 destructive migration downgrade；
- 用 `used_*` 人工归零掩盖差异；
- 仅凭API/Activity/schema兼容就把包含新patch marker的Workflow history交给缺该branch的旧build；
- 先切queue/current build再补跑Replayer，或删除/改写已记录patch、definition assignment、CAN next intent来使旧build看似兼容。

Schema 迁移采用 additive expand 后独立 contract；两者执行后都保留 ledger，不做 destructive downgrade。Contract 已撤销旧协议权限时，应用回滚只能回到仍支持 execution-grant schema/protocol 的安全 build，不能重新启动旧 binary。回滚完成后重新保存Temporal inventory/routing receipt、Replayer corpus结果、DB invariant、definer ACL catalog快照，以及supervisor/gateway release、boot、reader inventory与journal reconciliation证明；任一不为零/匹配就继续paused。

## 17. Definition of Done

开发会话只有在以下全部完成时才能报告“修复完成”：

1. 数据库中存在 region probe/claim/terminal receipt与immutable projection event/override ledger、quota config gate/stable scope/policy revision/bucket、request item/submission operation、reservation/item、durable staging/outbox/receipt、sampling run-origin/primary-slot/prebind partition、强化fence/context generation/captcha closure、worker runtime-scope/scope-recovery/member-release-blocker/physical-isolation receipt、supervisor action/reconciliation/release-reader registry、workflow start operation/RPC attempt/gen0/no-run、assignment父级termination pointers/统一ingress/root/intent/alias/escalation/assignment-terminal receipt/successor-bind及hard-terminate closure schema，约束与权限完整；
2. 一个短事务能原子预占account全部适用quota scopes并取得同一browser fence为unactivated joint grant；只有matching gateway install receipt后的第二短事务才能同时激活reservation/fence；
3. 同账号并发永不超额，不同账号/browser 可以并行；
   3a. Region/relay probe以DB generation和append-only receipt单调应用；旧完成结果、旧manual epoch和ACK重试不能覆盖新健康事实，grant冻结health epoch/freshness；同地域不同账号、平台仍可并行；
4. adapter 显式持有 lease handle；系统停止授权失租/cancel/pause/epoch过期 holder 的新normal操作，gateway/restart ACK 后旧 generation 的后续 CDP 消息被物理拒绝；
5. 每次 submit 前写 dispatching/debit，发送不确定时不自动重发；
6. 五个平台和 legacy 入口都走同一协议；
7. Captcha handoff 不释放给其他 run，也不长期占用未发送 quota；
8. 异常 fence 不直接抢占；旧 CDP 的后续消息被 gateway 拒绝，或由“终止旧 holder + 重启 browser”物理切断；已经转发/未知的命令不被虚假宣称撤回，而是进入 unknown/tainted 并禁止自动重发；
9. governor 不再依赖 task commit 后 best-effort 回调，receipt/outbox 幂等；
10. task provenance以唯一request item和append-only result revision为真源；每个revision绑定自己的immutable submission operation，获grant的结果同时绑定reservation中冻结的account/browser，成功答案还绑定准确capture attempt、verified staging和content hash；一条业务Task最多计一次success；
11. admin release/rebind/quota patch/global pause 都有 expected version/CAS；
12. PG 并发、fault injection、Temporal replay、安全旁路测试全部通过；
13. 生产 invariant/metrics/告警和 runbook 可用；
14. 没有覆盖当前工作树无关改动；
15. 全采集仍保持停止，除非用户在验收后明确要求恢复；
16. Pause API 只在 quiescence/物理隔离完成后报告 paused；pause→resume 能继续 transient waiting 请求且不制造失败占位；
17. 当前 bucket baseline、gateway/raw-port拓扑或mandatory restart方案、worker artifact release/queue/build路由均有可复核证据，不以配置声明代替实测；
18. v0非终态workflow/start-outbox为0，所有run/start producer受protocol行锁与DB最低版本约束；completed v0可回放但不能误收v1缺字段payload；
19. supervisor的adopt/physical seal覆盖规范physical resource set而非仅per-instance；共享worker runtime-scope gate与完整blast/release-blocker set、跨instance unit串行、fsync command journal/startup reconciliation、append-only scope physical-isolation receipt、physical seal→hard closure→logical free→scope open无环顺序、浏览器自动重启和raw listener开放窗口均已纳管；
20. Captcha continuation claim/joint grant、multi-generation capture staging和pause-aware service deadline在ACK丢失/并发/重启测试中可恢复；
21. 所有跨holder/purpose transition经过credential pending→direct predecessor barrier/install receipt→activation；pending无effect，held/active有matching receipt；
22. Temporal chain每个closing有唯一operation owner，prepare/bootstrap/terminal ACK丢失可恢复；hard request撞上既有cooperative owner时不凭history absence冒领，assignment gate立即阻断normal，并由actual-workflow abort-transfer、non-CAN/no-successor watcher或合法CAN successor bootstrap/closing-only reconciler direct hard-close收敛。Out-of-band terminal只能凭immutable non-CAN/no-successor证据audited supersede为watcher，合法/不明CAN successor不被误终结。Old Activity epoch不能在abort、pause→resume或worker-scope重开后恢复normal。Hard terminate只有Temporal terminal、exact physical-isolation receipt和全部item settlement三者齐全才completed；scope operation ID/child投影不算证据，hard completed前不得logical free；
23. Parked workflow在history soft budget经waiter transfer安全CAN，多日pause后仍以同request/slice/deadline继续，旧route不能claim；
24. Run execution closure、权威primary-slot角色与sampling campaign进度分层；origin-intent成员集、slot current revision/state和assignment可由约束重建，generic producer无canonical-main/replacement/partition写权限。补采/run-now即使先到也不能抢主，只有slot授权lineage/occurrence或audited replacement可成为primary。未绑定multi-leg partial replacement已通过原子partition让replacement+continuation不重不漏覆盖全部old legs，old schedule bind竞态只有一个winner且没有slot指向失效intent；已绑定subset替代不误伤其他legs。每个run item只解析一次，但只有eligible且non-degraded答案生成sampling candidate；当前136×6=816 cell合同通过，补采/换版不重复observed；
25. 豆包normal/deep_think及所有恢复组合都有verified正式账号binding；缺绑定时scheduler和Activity均fail-closed，无env/CDP fallback。
26. Quota policy换版不创建第二套stable scope/bucket或重置历史exposure；config gate与required scope gate/cutover/calendar overlap/rollover在ACK丢失、late settle和局部mode block下守恒，deep_think局部阻断不误停normal；
27. Region terminal-attempt与applied-projection两条高水位分离，无效高代不压有效低代；reservation只引用immutable projection event、begin使用`current applied >= frozen`，success refresh不误杀，terminal high-water既不参与授权也不使last projection event漂移；
28. Start在never-issued、outcome-unknown、bootstrap-pending三边界可恢复且只复用同一Temporal request ID/envelope；assignment父级pointer线性化normal terminal与首次late request，统一ingress跨root/post-terminal只有一个结果，三条terminal path和跨assignment lineage复合FK全部通过；
29. Browser current context、health/readiness、fence/boot与release在同一logical-free CAS一致；owned-dirty仅三条白名单边，captcha permit三态/短heartbeat/continuation closure在每个killpoint可恢复，quarantined不被冒充terminal或free；
30. SECURITY DEFINER/trigger catalog manifest、ACL/RLS/owner/search-path攻击测试，以及supervisor/gateway release-reader/journal corpus的reader-expand、滚动、rollback、backup restore门禁全部通过；任何未知record/terminal、旧OS动作或manifest漂移都保持paused/raw端口关闭。

## 18. 明确禁止的“看似修好了”方案

- 只给 `record_task_outcome()` 加 account `FOR UPDATE`；
- 让relay HTTP probe按完成/commit顺序直接覆盖region state，或只加region行锁却没有probe generation、stale-noop receipt和manual override epoch；
- 用“已关单的最大probe generation”判业务stale，让较高代invalid/diagnostic/barrier no-op压掉较低代valid failure；把terminal high-water写入reservation/last projection event，或begin要求applied generation严格相等而误杀benign success refresh；
- 让历史reservation复合FK直接引用持续更新的region current行，导致新probe被FK阻断或CASCADE改写历史；
- 只增加 `reserved_today`，忽略周/年、reset 和 unknown；
- quota policy换版时新建另一套scope/bucket或把baseline/exposure归零；用subject全局config gate粗暴清除/恢复某一个mode scope block；
- 把 `used_*` 从 success 静默改义为 platform debit；
- 只依靠 browser fence 串行，不做 quota reservation；
- 只依靠 quota token，不把 browser token 传给 adapter；
- heartbeat lost 只打日志；
- lease 过期后直接 token+1 接管原 browser；
- supervisor只在命令入口比较generation，却让已接收的旧 `systemctl` job在后台继续；
- 只做per-instance supervisor队列，却让两个instance并发操作同一共享worker unit；或先扫描blast radius、后关scope gate，留下并发grant漏项；
- supervisor重启后把新进程内存中的空队列当作无旧job，不恢复fsync command journal、不对账D-Bus/systemd实际状态就adopt/seal；
- 把线程池中的单个 `to_thread` 当作可精确kill的holder，或未隔离同worker boot全部lease就停共享worker；
- 让browser `Restart=always` 在boot commit/gateway sync前重新暴露raw CDP；
- 管理端继续直接写 `released_at`；
- click 报错后无条件 Enter fallback；
- 用 Temporal retry 重发 `dispatching/submitted`；
- 因grant/release ACK丢失直接创建新generation，不先read-back同一operation；
- Start ACK/visibility不明时换Temporal request ID、workflow ID或envelope重试；把NOT_FOUND直接当confirmed-not-forwarded，或仅凭assignment item header hash伪造no-run terminal而不物化逐item neutral/materialization证据；
- 把gateway丢弃的无关frame当作整题confirmed-not-sent证据；
- 用一条`submission_operation_id PRIMARY KEY` staging行覆盖unknown manifest与后续captcha答案；
- 从v1 result缺字段推断成legacy，或让pre-marker非终态workflow执行会返回新typed deferred的Activity；
- 用最近 50 条 event 扫描充当幂等；
- task commit 后 best-effort governor；
- task commit后best-effort sampling回调，或用run-item resolution/Task/completed samples直接当observed cells；
- 以第一个创建/完成、锁竞争winner、样本最多或slot当前为空来猜primary；允许普通run-now/top-up抢占主批次或自动扶正；
- 对尚未绑定的multi-leg canonical-main intent只替换一个slot、却把old intent cancelled/superseded而不给其余member原子生成continuation intent；或让current slot继续指向失效intent；
- 在Task事务建立到sampling cell/campaign的跨域FK，或在sampling事务用FK/trigger反向锁Task/Run；
- token+1后直接把assist/capture/collection标为held，跳过credential pending/install receipt/activation；
- 把quarantined captcha continuation直接标terminal/free，遗漏historical action/permit/owner/item/materialization closure，或把permit expiry当confirmed-not-forwarded；
- context writer/recovery/rebind只锁browser/fence却跳过browser health/current context，或先把fence free再补context/health/readiness；
- 用当前open/active状态让pause/closing前旧Activity重新获得normal权限，不比较单调effect epoch；
- 只凭Temporal terminated事件把hard-terminate chain标completed，不等待gateway/scope物理隔离和dispatching/forwarded item保守settlement；
- 把worker-scope recovery operation ID、`unit_stopped`或child `sealed`投影直接当hard physical证据，不生成并核验append-only scope physical-isolation receipt；
- 把physical seal与DB free/completed合成一步，导致hard closure前提前regrant；或反过来要求先free/scope open才生成physical receipt，制造hard complete↔free闭包环；
- watcher给既有CAN/normal/cancel operation原地补hard-closure header，或仅因暂未查询到successor就改kind/终结chain；
- Assignment只查termination child表absence却不在父行维护root/terminal pointer/version；normal terminal后第一次late cancel/admin/watch再创建effectful root/RPC/resource set；或让root alias与post-terminal各自拥有不相交的幂等key表；
- 用各列单独FK/UNIQUE代替同assignment/root/start lineage复合FK，让intent、alias、escalation、no-run或terminal receipt借用另一assignment的证明；
- durable operation apply先锁operation/claim再反向等待control/assignment/account/browser，或失败后把phase/epoch/token回退到旧值；
- hard terminate因当前history尚无CAN command就reject intent/transfer并supersede cooperative owner；Workflow Task可能仍在提交CAN，只有actual-workflow pre-command abort receipt或immutable terminal排序可决定；
- parked workflow靠无限watchdog Activity等待，不做history budget和waiter transfer；
- 在浏览器 I/O 时持 DB 行锁；
- 在生产用 local fencing 或 env fallback；
- 只写 mock 单测，不写真实 PostgreSQL 并发测试；
- 以“当前没发现重复数据”为恢复依据。

## 19. 新会话最终交付格式

实施过程中持续给用户简短进度，但最终交付必须包括：

1. 实际实现的协议和与本文设计的差异；
2. schema/migration revision、约束、权限和 backfill 结果；
3. 精确修改文件列表；
4. 锁顺序和状态机说明；
5. Temporal replay 兼容证明；
6. 测试命令、通过数和关键并发/fault 结果；
7. invariant SQL 结果；
8. 当前部署/worker/scheduler/cron/全局 pause 状态；
9. 未完成风险和是否允许恢复采集的明确结论；
10. 如未满足任何硬门槛，必须说“仍不可恢复”，不能用单元测试通过替代。

## 20. 开工时建议的第一组只读命令

```bash
cd /home/xln/geo-system/platform-v2
git status --short
git rev-parse --short HEAD
git diff -- api/geo_platform/collection workflows/activities workflows/definitions/collection.py
rg -n "resolve_collectable|resolve_batch_instance|record_task_outcome|persist_collection_result" api workflows
rg -n "collection_region|relay|probe.*region|region.*health|fresh_until" api workflows tools scripts
rg -n "acquire_browser_fence|heartbeat_browser_fence|release_browser_fence|connect_over_cdp|platform_browser" api workflows tools scripts
rg -n "workflow.patched|maximum_attempts|CollectionBatchInput|CollectionBatchItemResult" workflows tests/workflows
rg -n "revision.*=|down_revision.*=" migrations/versions
```

随后先输出一份“当前树相对本文假设的差异清单”，再开始改动。不要假定 2026-08-21 审计后的代码没有变化。
