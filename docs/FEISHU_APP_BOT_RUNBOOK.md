# GEO 飞书自建应用机器人激活、验收与回滚手册

本手册从“主树实现完成但生产未部署”开始。任何真实飞书后台变更、消息发送、生产迁移或服务重启
都需要用户明确授权。命令示例不包含秘密值，也不要把秘密粘贴到终端历史、聊天或工单。

## 1. 飞书后台最小配置

1. 创建企业自建应用，开启机器人能力；可用范围只包含实际值班人员。
2. 申请并经管理员批准最小权限：`im:message:send_as_bot` 和 `im:message:update`。第一版不申请读取
   群消息、全量通讯录、云文档或管理员权限。
3. 订阅新版 `card.action.trigger`。
4. 配置 HTTPS 回调：
   `https://<public-host>:8443/api/v2/integrations/feishu/card-action`。
5. 配置独立 Verification Token 与 Encrypt Key；公网证书必须由受信任 CA 签发。
6. 发布应用版本并完成管理员审核。
7. 把机器人加入专用测试群，记录 app ID、tenant key、测试群 `chat_id` 和获准操作人的 `open_id`。
8. 先不要把生产通知渠道切到 `feishu_app`。

飞书发送/更新和回调契约以[官方发送消息接口](https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN)、
[官方消息卡片更新概览](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/introduction)、
[官方回调指南](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/receive-and-handle-callbacks?lang=zh-CN)
为准。

## 2. 凭据文件

在 root-only 目录准备以下文件，owner `root:root`、目录 0700、文件 0600、每个文件末尾允许一个换行：

| 文件 | 内容 |
| --- | --- |
| `/etc/geo-platform-v2/credentials/feishu-app-secret` | App Secret |
| `.../feishu-verification-token` | Verification Token |
| `.../feishu-encrypt-key` | Encrypt Key |
| `.../feishu-allowed-open-ids` | 每行一个获准 open ID，可含 `#` 注释 |
| `.../feishu-link-signing-key` | 至少 32 字节的独立随机 HMAC key |

link key 必须独立生成，不能复用 App Secret 或 Encrypt Key。可以在 root shell 以 `umask 077` 将
`openssl rand -base64 48` 直接重定向到目标文件；不要先输出到屏幕。

非秘密标识从 `deploy/production/feishu-bot.env.example` 生成
`/etc/geo-platform-v2/feishu-bot.env`。不要把任何上述秘密放进 Environment 或 env 文件。
其中 `GEO_ASSIST_REGISTRY_DIR` 必须是绝对路径，并由 API、main worker、OTP CLI 和 bot 读取同一值；
不要让它随 release snapshot 改变。

API 只需要 link key。安装
`geo-platform-v2-api-feishu-credential.conf.example` 为 API systemd drop-in。bot unit 加载全部五个
credential。worker/OTP/alert receiver 只读取非秘密 app channel 配置，不得到 App Secret。

如果切换期仍保留 Server酱 fallback，将现有 SendKey 迁入独立 credential file，并安装
`geo-platform-v2-alert-receiver-credentials.conf.example`。验收完成后删除该 drop-in/credential 并轮换
或撤销旧 SendKey。不要使用 `systemctl show ... Environment` 检查秘密。

## 3. 部署前门禁

- 记录当前 git revision、dirty 状态、API/worker/s02/alert receiver 的 snapshot 与 PYTHONPATH。
- 核对在途 collection、formal report、sidecar、browser fence、relay 与 firing alerts。
- 核对没有仍处于 active/claimed 的旧 assist 注册表；跨 snapshot/数据库角色切换不承接半途会话。
- 正式报告关键阶段存在时不部署；bot 部署不要求重启 collection worker 或常驻浏览器。
- 从生产 PostgreSQL 做可恢复备份，记录校验值与恢复演练位置。
- 在隔离库从空库执行 `alembic upgrade head`，并通过本手册列出的 unit/integration gate。
- 用既有 release 流程从主树生成新的不可变 snapshot；禁止修改历史 snapshot。API、main worker 与 bot
  必须解析同一份新代码和同一个稳定 `GEO_ASSIST_REGISTRY_DIR`，旧 s02 可保持独立但不得承载本功能。
- 检查 Nginx 使用公共可信证书。`nginx -t` 必须通过后才 reload。
- 确认 bot unit 和所有 GEO 服务均清空大小写 proxy env，`NO_PROXY=*`。

## 4. 安全部署顺序

1. 备份数据库；在维护记录中写明 migration 前 revision。
2. 对新 release 执行 migration `s06_0025`。它只新增 `notification` schema，不修改接管注册表或现有
   workflow outbox。
3. 安装新 snapshot、bot unit、API link-key drop-in、非秘密 env 和 Nginx 配置。只安装 bot 可以不重启
   worker，但在切换任何 producer 到 `feishu_app` 前，必须另择无在途关键任务窗口，把 API 与 main
   worker 都切到这份新 snapshot；未切换时禁止启用新渠道。
4. `systemd-analyze verify` 检查 unit；`nginx -t` 检查 callback 精确路径、body/rate/timeout 门。
5. reload systemd/Nginx，并且只启动独立 bot。先保持
   `GEO_ASSIST_NOTIFY_FLAVOR` 和 `GEO_ALERT_NOTIFY_CHANNEL` 为旧渠道/disabled。
6. 检查 loopback `/health` 与 `/readiness`；确认 collection worker、浏览器 fence、relay 和报告任务
   状态没有变化。
7. 在飞书后台完成 callback challenge；不得为此制造真实验证码或停 relay。
8. 在专用测试群执行 synthetic smoke。
9. smoke 全部通过后，先切人工接管为 `feishu_app`；观察一个完整接管闭环。
10. 再切 Alertmanager/relay primary 为 `feishu_app`；观察一个 synthetic firing→resolved 闭环。
11. 观察期内不无脑双发。Server酱只作为关闭态 fallback；用户确认后再移除并撤销其凭据。

主树测试通过不等于生产生效。只有新 snapshot、migration、unit、Nginx 和运行态证据全部对应时才能
记录“已部署”。

## 5. Synthetic smoke

使用专用测试群和短 TTL 测试记录，不触发真实平台验证码、不停 relay、不暂停 run：

1. bot health/ready，callback challenge 成功；
2. 写入一张 synthetic assist 通知，sender 保存 `message_id`；
3. A 认领成功，A 重复点击幂等；B 并发认领收到“已有其他值班人员认领”；
4. 非 allowlist 用户被拒绝，audit 只保存 actor hash/mask；
5. “打开接管页”进入 capability URL，URL 到期或篡改均为统一 403；
6. release 后 B 可认领；只有当前认领人可 complete；
7. 分别验证 `workflow_captcha` 只生成一条 signal command、`otp_cli` 生成零 signal command；
8. solved/expired 后原卡更新、按钮消失；
9. 同 fingerprint firing 重放不新发卡，resolved 更新原 `message_id`；
10. 暂时让 fake sender 返回 503：生产者仍快速成功，command 退避，恢复后 drain；
11. 在 fake/loopback 环境污染 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`，客户端仍直连；
12. 全程复核 G07–G34、formal report、browser fence、relay 和账号额度未受影响。

飞书要求卡片回调快速完成。回调 handler 只做本地鉴权/CAS/outbox；smoke 中应从 Nginx 与 bot 日志
确认没有外部 OpenAPI 等待。日志中只能出现稳定 marker/code/request log ID。

## 6. 安全观测

只查询计数、状态和安全 marker；不要导出 callback/card JSON：

```sql
SELECT state, count(*)
FROM notification.delivery_command
GROUP BY state ORDER BY state;

SELECT kind, state, count(*)
FROM notification.notice
GROUP BY kind, state ORDER BY kind, state;

SELECT operation, attempts, last_error, request_log_id, updated_at
FROM notification.delivery_command
WHERE state='dead'
ORDER BY updated_at DESC
LIMIT 20;
```

告警项：pending 最老年龄、dispatching 超过 5 分钟、dead 增长、bot readiness、callback 403/429/5xx。
bot 自身告警不得再次送入 bot，避免递归。

## 7. 回滚

常规回滚不降数据库：

1. 将 `GEO_ASSIST_NOTIFY_FLAVOR` 切回明确的 `feishu_webhook`/旧渠道，或暂时 disabled；
2. 将 `GEO_ALERT_NOTIFY_CHANNEL` 切回 `serverchan` 或 disabled；
3. 停止 bot unit，移除/禁用飞书 callback Nginx location；
4. 保留 `notification` schema、审计、message ID 和接管注册表；
5. 核对 workflow signal outbox、在途 run、browser fence、relay 和报告状态。

不要因 bot 回滚而 downgrade `s06_0025`：downgrade 会删除审计和 delivery 状态。只有确认没有任何真实
通知数据且获得单独授权时才可考虑 schema downgrade。回滚不需要重启 Temporal、collection worker 或
常驻浏览器。

## 8. 轮换与撤销

- App Secret：写新 credential file，原子替换后仅重启 bot；确认 token 获取成功再撤销旧 secret。
- Verification Token / Encrypt Key：飞书后台与 credential file 必须在同一维护窗口切换；challenge 和
  签名 smoke 通过后结束窗口。
- allowlist：原子替换文件并重启 bot；移除人员后检查其后续 action 全部 forbidden。
- link key：轮换会立即使旧卡片接管链接失效。只在没有活跃 assist 时轮换，或保留短期双 key 支持后另行
  实现；当前版本是单 key fail-closed。
- App 撤销：先切渠道/停 bot，再从测试群移除和撤销应用；保留数据库审计。
- Server酱：飞书闭环验收后删除 credential/drop-in，轮换或撤销旧 SendKey。

## 9. 离线质量门

所有命令必须显式使用隔离测试 DSN，且 HTTP 测试不得访问真实飞书：

- `ruff check`：通知模块、接管/告警/relay 接入、迁移和相关测试；
- `mypy`：同一组生产 Python 文件；
- unit：Feishu client、callback security、card、bot adapter、alert policy、captcha/OTP/legacy webhook；
- integration：空 PostgreSQL migration、并发 claim、event replay、OTP/workflow complete、alert
  firing/resolved、sender retry/dead letter、capability routes；
- `systemd-analyze verify` 与 `nginx -t`：使用临时 root/渲染配置，不直接改生产；
- secret scan：App Secret/token/ticket/手机号原文不得进入 tracked diff、测试输出或 Markdown。
