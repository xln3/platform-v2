# 常驻浏览器 / 代理中继运维手册（2026-08-09 起，浏览器矩阵化）

五平台采集的浏览器拓扑、实例/端口/账号登记、代理轮换与 fencing 运维口径。
单元文件均在本目录，生产实体在 `/etc/systemd/system/` 与 `/etc/geo-platform-v2/`。

## 实例模型

**常驻浏览器实例 = 平台 × 地域 × 账号**，实例键 = `{platform}_{regiontag}`
（下划线小写，第一段恒为平台 slug，如 `doubao_sh`）。实例键作为 opaque
"platform" 进入全部既有机制：systemd 单元 `browser@<实例键>`、worker 侧
`browser_router`（batch 按 (adapter, region) 路由）、互斥锁 / DB fence
（`platform.browser_fence` 键列）、CDP env 解析（`GEO_BROWSER_<KEY>_CDP_URL`
优先，旧 `GEO_<PLATFORM>_CDP_URL` 兜底）、captcha-assist 接管（锁/CDP 按实例键，
撞码特征表按平台 slug）。

路由诚实失败（全部 non_retryable）：清单未配置 `browser_instances_not_configured` /
清单或实例配置畸形 `browser_instances_invalid` / 平台无实例
`browser_instance_unavailable` / 地域与实例出口省码不符 `region_exit_mismatch`。
**绝不静默拿别地域出口顶替。**

## 拓扑与登记表

- **常驻浏览器**：`geo-platform-v2-browser@<实例键>.service`
  （`tools/resident_browser.py`，patchright headed Chromium，DISPLAY=:1，CDP 只绑
  127.0.0.1）。env：`/etc/geo-platform-v2/browser-<实例键>.env`
  （`RESIDENT_PLATFORM` 填实例键 / `PROFILE_DIR` / `PROXY_URL` / `CDP_PORT`）。
- **代理中继**：`geo-platform-v2-proxy-relay@<地域tag>.service`
  （`tools/wukong_auth_relay.py`，悟空预认证 CONNECT 中继——悟空网关只接受首个
  CONNECT 即带凭据，Chromium 的 407 质询模式被拒，故一律经中继）。
  **中继实例标签 = 出口地域**（sh/tj/bj），同地域多平台共用。凭据只在中继
  env（`UPSTREAM_PROXY_URL`），消费侧零秘密。
- **消费侧**：worker 的 `GEO_<P>_PROXY_URL` 与浏览器的 `RESIDENT_PROXY_URL`
  都指中继本地端口；采集 attach 走 `GEO_BROWSER_<KEY>_CDP_URL`。

| 实例键      | profile 目录                 | 中继（出口）            | CDP 端口 | 状态                                            |
| ----------- | ---------------------------- | ----------------------- | -------- | ----------------------------------------------- |
| doubao_sh   | runtime/profiles/doubao-sh   | relay@sh :19323（上海） | 19222    | active                                          |
| deepseek_tj | runtime/profiles/deepseek-tj | relay@tj :19324（天津） | 19224    | active                                          |
| tongyi_bj   | runtime/profiles/tongyi-bj   | relay@bj :19325（北京） | 19225    | active                                          |
| yiyan_sh    | runtime/profiles/yiyan-sh    | relay@sh :19323（上海） | 19226    | active                                          |
| yuanbao_tj  | runtime/profiles/yuanbao-tj  | relay@tj :19324（天津） | 19227    | active                                          |
| doubao_bj   | runtime/profiles/doubao-bj   | relay@bj :19325（北京） | 19230    | active（20260810 开户+live 验收）               |
| deepseek_sh | runtime/profiles/deepseek-sh | relay@sh :19323（上海） | 19231    | active（20260810 人工手动登录+batch live 验收） |
| yiyan_bj    | runtime/profiles/yiyan-bj    | relay@bj :19325（北京） | 19232    | active（20260810 开户+live 验收）               |
| deepseek_bj | runtime/profiles/deepseek-bj | relay@bj :19325（北京） | 19233    | active（20260810 开户+live 验收）               |
| yuanbao_bj  | runtime/profiles/yuanbao-bj  | relay@bj :19325（北京） | 19234    | active                                          |
| yuanbao_sh  | runtime/profiles/yuanbao-sh  | relay@sh :19323（上海） | 19235    | 待开户；未登录前不得加入 worker 清单            |

CDP 端口分配：19222-19227、19230-19235 已分配（见表；新实例接着 19236
起分配并登记本表）。

worker 侧清单（`/etc/geo-platform-v2/worker-adapters.env`）：
`GEO_BROWSER_INSTANCES` 只列**已开户已 enable** 的实例（当前九台全量；
进了清单而浏览器没起 = 该地域采集 browser-launch-failed）。每实例配
`GEO_BROWSER_<KEY>_CDP_URL` 与 `GEO_BROWSER_<KEY>_EXIT_GB`（6 位省码，
与中继出口一致）。

## 新增实例 SOP（Phase 3 开户启用）

1. `mkdir` 新 profile 目录（`runtime/profiles/<platform>-<tag>`；fail-closed
   不自动建——必须先人工 mkdir）。
2. 写 `/etc/geo-platform-v2/browser-<实例键>.env`（参考
   `browser-doubao_sh.env.example`；CDP 端口取本表下一个空号并登记）。
3. `systemctl enable --now geo-platform-v2-browser@<实例键>`，确认
   `resident_browser_up` + CDP 端口在听。
4. 开户/登录：`tools/otp_assist_login.py --platform <实例键> --goto <平台首页>`
   （手机人工接管，profile 留在该实例的常驻浏览器）。
5. worker-adapters.env：`GEO_BROWSER_INSTANCES` 追加实例键 + 配
   `GEO_BROWSER_<KEY>_CDP_URL` / `_EXIT_GB`；`GEO_MEASUREMENT_EXIT_GB_MAP`
   加 `<实例键>:<出口省码>`（INV-1 provenance 真源）。
6. `systemctl restart geo-platform-v2-worker`（s02-worker 不消费实例清单，
   无需重启）。

## 存量迁移记录（2026-08-09，slug → 实例键）

五台 slug 实例（browser@doubao 等）迁入实例键模型：env 改名
`browser-<slug>.env` → `browser-<slug>_<tag>.env`（`RESIDENT_PLATFORM` 改实例键；
profile/中继/CDP 端口全部不变），逐台「先停旧 slug 实例、再起新实例键实例」
（同 profile 锁 + 同 CDP 端口，两实例绝不能同时 active）。旧 env 已删，备份在
`/etc/geo-platform-v2/*.bak-20260810T010235-w1-matrix`（含旧 worker-adapters.env
与 proxy-relay-doubao.env）。`proxy-relay@doubao` 同日改名 `proxy-relay@sh`
（19323 实为上海出口的历史误命名；env 内容原样复制，消费侧端口不变零联动）。
`platform.browser_fence` 五行旧 slug 租约均为已释放+已过期（无 stale 锁，
无需清理）；新实例键租约行在首次持锁时自建。

## 代理轮换（换上游节点 / 轮换凭据）

1. 改 `/etc/geo-platform-v2/proxy-relay-<tag>.env` 的 `UPSTREAM_PROXY_URL`。
2. `systemctl restart geo-platform-v2-proxy-relay@<tag>`。
3. 消费侧端口不变，**无需联动重启浏览器/worker**；浏览器进程的既有连接随页面
   导航自然走新上游（要立即生效可再 restart 对应 `browser@<实例键>`，注意共用
   同一中继的实例会一起换）。
4. **若上游出口城市变了**（不只是换同城市节点）：同步改
   `/etc/geo-platform-v2/worker-adapters.env` 里受影响实例的
   `GEO_BROWSER_<KEY>_EXIT_GB` 与 `GEO_MEASUREMENT_EXIT_GB_MAP` 对应条目
   （两值同真源，6 位出口省码）并 restart `geo-platform-v2-worker`——该映射是
   INV-1 合格性判定的 geo provenance 来源，漂移会让新答案的
   `observed_gb_code` 失真。

换 profile / 端口 / 本地代理指向：改 `browser-<实例键>.env` + `restart
geo-platform-v2-browser@<实例键>`。重启期间采集 attach 断连按
`browser-launch-failed` 诚实重试自愈（unit `Restart=always`）。

## 多 worker fencing（2026-08-07 起，s06_0012）

- `platform.browser_fence` 表 + `resident_browser.py` 复合锁（进程内锁 + DB lease
  - fencing token + 30s 心跳续期）。`browser_lock()`/`platform_browser()` 签名不变，
    captcha-assist 持锁语义不变。2026-08-09 起 fence 键 = 实例键（opaque 串，
    String(80) 直装无需迁移）；旧 slug 租约行已全释放过期（见迁移一节）。
- env（worker）：`GEO_BROWSER_FENCING=db|local`（**缺省 db**，fail-closed——DB 不可达
  抛 `BrowserBusyError`，绝不降级）、`GEO_BROWSER_FENCE_HOLDER`（缺省 hostname:pid）、
  `GEO_BROWSER_FENCE_TTL_S`（缺省 7200）、`GEO_BROWSER_FENCE_HEARTBEAT_S`（缺省 30）。
- holder 崩溃回收窗 = TTL（最坏 2h 该实例不可调度）；盯三个事件告警：
  `browser_fence_lost` / `browser_fence_preempted` / `browser_fence_release_stale`。
  stale 租约人工回收 = SQL 把对应行置 `released_at`（有实证先例）。

## 撞码/登录人工接管

- captcha-assist：batch 撞 `wall_captcha` → workflow 挂起 → 手机页
  `/api/v2/assist/<ticket>` 人工过码 → 断点续跑（细节见 `workflows/activities/captcha_assist.py`
  与 `definitions/collection.py` patch 门 `captcha-assist-v1`）。2026-08-09 起
  assist attach 撞码 batch 的**同一台常驻实例**（实例键经 CaptchaPause 转发；
  锁/CDP 按实例键，撞码特征表按平台 slug）。
- 登录/OTP 接管（开户用）：`tools/otp_assist_login.py --platform <实例键>`
  （兼容旧 slug）——同一套 bridge/ticket/通知骨架，人工在手机页完成登录
  （含输 OTP），profile 留在常驻浏览器。
- 真机演练：`scripts/drill_captcha_assist.py`（缺省实例 `doubao_sh`，
  `DRILL_INSTANCE` 可换）。
