# GEO 飞书自建应用机器人架构决策

状态：2026-08-14 主工作树已实现，尚未部署、尚未配置真实飞书凭据、尚未发送真实消息。

## 1. 决策

生产只保留一个卡片回调入口：HTTPS `card.action.trigger` 回调。Nginx 精确路径为
`/api/v2/integrations/feishu/card-action`，只转发到 loopback `127.0.0.1:18092` 的独立 bot
服务。

飞书官方推荐服务端 SDK 长连接处理卡片回调；其优势是不增加公网回调入口，并要求回调在
3 秒内完成。但本次核查的 Python SDK 1.7.2 上游实现仍会直接丢弃 WebSocket
`MessageType.CARD` 帧，上游 issue #126 也记录了相同缺口。因此这一版不把未工作的 SDK 路径
包装成生产能力，而是选择具备签名、加密、token、重放和限速校验的 HTTPS 入口。

官方契约与实现证据：

- [官方 Python SDK 回调/长连接说明](https://open.feishu.cn/document/server-side-sdk/python--sdk/handle-callbacks?lang=zh-CN)
- [官方 Python SDK 当前 WebSocket client](https://raw.githubusercontent.com/larksuite/oapi-sdk-python/v2_main/lark_oapi/ws/client.py)
- [官方 SDK card callback issue #126](https://github.com/larksuite/oapi-sdk-python/issues/126)
- [回调签名与 Encrypt Key 说明](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case?lang=en-US)
- [发送消息接口](https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN)
- [消息卡片更新接口与权限概览](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/introduction)

当官方 Python SDK 的 card frame 分派、重连、心跳和优雅退出均可离线验证后，可以另做 ADR
切换到长连接。切换时必须替换 HTTPS 入口，不能同时运行两套生产消费入口。

## 2. 组件边界

```text
captcha activity / OTP CLI / alert receiver / relay probe
                         │ 仅本地数据库事务，不访问飞书
                         ▼
 notification.notice + notification.delivery_command
                         │ SKIP LOCKED / 有限重试 / dead letter
                         ▼
          独立 geo-platform-v2-feishu-bot 服务
                         │ trust_env=False，官方 HTTPS 域名 allowlist
                         ▼
                飞书 OpenAPI 消息发送/更新

飞书 card.action.trigger
        │ Nginx body/rate/timeout 门
        ▼
 bot 回调鉴权 → event_id/nonce 持久幂等 → 行锁 CAS → update outbox
        │ complete 只调用统一 AssistCompletionService
        ├─ workflow_captcha → workflow signal outbox
        └─ otp_cli          → 注册表完成回执，不查询 CollectionRun
```

`FeishuAppClient` 只负责 tenant access token 内存缓存/single-flight 刷新、发送、更新、有限重试
和安全诊断 ID。`cards.py` 是纯卡片投影。`NotificationService` 管状态、去重、审计与 delivery
outbox。`InteractionService` 管身份、CAS、事件幂等和完成语义。HTTP handler 不发飞书请求。

## 3. 持久状态

迁移 `s06_0025` 新增 channel-neutral 的 `notification` schema：

| 表 | 用途 | 敏感数据边界 |
| --- | --- | --- |
| `notice` | 通知公开 ID、状态、fingerprint、message_id、脱敏摘要、认领人哈希 | 只存 assist ticket SHA-256，不存 ticket 原文 |
| `delivery_command` | send/update outbox、确定性 UUID、尝试次数、退避、dead letter | 不存渲染卡片、token 或响应正文 |
| `interaction` | `event_id` 唯一幂等、动作、脱敏 actor、稳定响应 | 不存完整回调 payload |
| `callback_replay` | timestamp/nonce 哈希与过期时间 | 不存 nonce 原文 |
| `audit_event` | actor hash、动作、结果、时间、关联通知 | 不存手机号、账号原文或秘密 |

人工接管状态为：

```text
pending_delivery → active → claimed → solved | expired | closed
        └──────────────────────────────→ delivery_failed
```

认领只是协作锁。只有数据库行锁内的 compare-and-set 能改变认领人；认领本身不会恢复 workflow。
释放只允许当前认领人执行。`complete` 只在认领后对当前认领人展示和受理。

Alertmanager 以 `fingerprint` 复用一条 notice。repeat 窗口内只增加计数；状态转换或配置的卡片
更新时间到达后才入 update command。`resolved` 不受 firing 限频影响，并更新原 `message_id`。
没有历史 firing 的孤立 resolved 不发送单独绿卡。

## 4. 接管链接与完成语义

旧 webhook 路径仍使用 `/api/v2/assist/<raw-ticket>`。`feishu_app` 路径不会把 raw ticket 写入
Temporal activity result。卡片 sender 使用通知 ID、ticket SHA-256、到期时间和独立 HMAC key
生成短期 capability：

`/api/v2/assist/notification/<notification-id>/<capability>`

capability 不可反推出 raw ticket，绑定 notification ID 和 TTL。API 验证 capability 后按 digest
读取既有 0600 注册表；frame/status/input/done 仍只代理硬编码 loopback bridge。数据库、outbox、
日志和卡片 callback `value` 中都没有 raw ticket。

`GEO_ASSIST_REGISTRY_DIR` 必须配置为 API、main worker、OTP CLI 与 bot 所共享的绝对运行时目录，
不能依赖各 release snapshot 的源码相对路径。生产样例固定到主运行时目录；切 release 前要求没有活跃
assist，并核对四个进程解析到同一目录（只输出路径，不列出注册表内容）。

新记录显式写 `session_kind`：

- `workflow_captcha`：完成时复用既有 `captcha_solved` workflow signal outbox 和
  `captcha-solved:<session_id>` 幂等键。
- `otp_cli`：完成只推进 CLI 注册表/通知状态，不伪造 CollectionRun，不发 Temporal signal。
- 旧记录兼容：`run_pub_id` 以 `otp-assist-` 开头时视作 OTP，否则视作 workflow captcha。

手机 `/done` 与飞书 `complete` 共同调用 `assist_completion.py`，顺序始终是数据库先提交、注册表
后原子替换。数据库提交后注册表写失败时，重复事件会复用持久 interaction 并再次尝试收口文件。

## 5. 回调安全边界

回调依次执行：

1. Nginx 精确路径、256 KiB body 上限、每 IP 速率限制、3 秒 upstream timeout、关闭 access log；
2. SHA-256 请求签名、timestamp 最大偏差、nonce 约束；
3. Encrypt Key AES-CBC 解密和 Verification Token 校验；
4. `schema`、`event_type`、app ID、tenant key、event ID 校验；
5. open ID allowlist、目标 notification、message ID、chat ID 校验；
6. `callback_replay` 与 `interaction.event_id` 持久防重放；
7. notice 行锁内执行 claim/release/recheck/complete；
8. 网络更新只写 delivery outbox，HTTP 立即返回 toast。

callback `value` 固定只有 `v`、公开 notification ID 和枚举 action。它不能携带 ticket、数据库主键、
秘密、URL 参数或可执行命令。未知 action fail-closed。

## 6. 通知策略与降级

- 首批 Alertmanager：全部 critical、配置白名单中的 warning（默认只有
  `GeoCollectionRunStalled`）、relay down/recovered。
- `GEO_FEISHU_ALERT_QUIET_HOURS=23:00-07:00` 可静默 warning firing；resolved 仍收口旧卡。
- `GEO_FEISHU_MENTION_ONCALL` 和 `GEO_FEISHU_ONCALL_OPEN_ID` 控制 assist/critical 卡片是否 @值班人。
- bot 自身告警名 `GeoFeishu*` 或 service `feishu-bot` 不再投递给自身，避免递归风暴。
- 发送失败按指数退避，达到上限后 command=`dead`；初次 send 失败时 notice=`delivery_failed`。
- captcha activity、OTP CLI、alert receiver 和 relay probe 都只写本地 outbox。飞书网络不可用不阻塞
  workflow，也不会占用其事件循环等待外部超时。
- 旧 `feishu` 名称保留兼容但会记录弃用提示；明确名称为 `feishu_webhook`。新渠道唯一名称是
  `feishu_app`。

## 7. 网络与秘密

生产 OpenAPI base 只允许 `https://open.feishu.cn`；开发注入只允许官方 HTTPS 或 loopback HTTP。
httpx 固定 `trust_env=False`，systemd 同时清空大小写 proxy env 并设置 `NO_PROXY=*`。飞书不进入地域
relay；本功能不引入 LLM，也不改变 `https://api.inferera.com` 模型路由。

App Secret、Verification Token、Encrypt Key、allowlist 和 link HMAC key 只通过 systemd
`LoadCredential=` 文件读取。tenant access token 只在进程内缓存并提前刷新。安全错误只记录稳定
marker、业务 code 和飞书请求日志 ID，不记录响应正文、请求卡片或 token。

## 8. 已知限制

- 第一版权限模型是 open ID allowlist，不是飞书组织角色同步；直接转派通过“释放→他人认领”完成。
- OTP `complete` 是受权人工确认，不等同于自动登录态探测；CLI 的 `--expect-*` 仍是 best-effort。
- 卡片 update 受飞书消息可编辑窗口和服务端配额约束；过旧消息的 update 最终会进入 dead letter。
- 第一版不在 update 永久失败后自动补发新卡，以免故障期间反向刷屏；dead command 先由安全观测和
  人工处置，受限补充消息可在拿到真实错误码分布后作为下一阶段能力。
- HTTPS 回调要求公网可达且证书受公共 CA 信任。现有 self-signed 证书不能用于正式飞书回调验收。
- 当前源码和部署模板均未激活生产；真实 app/chat/open ID/凭据与管理员审批仍待执行。
