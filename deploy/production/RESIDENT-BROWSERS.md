# 常驻浏览器 / 代理中继运维手册（2026-08-07 起）

五平台采集的浏览器拓扑与代理轮换、fencing 运维口径。单元文件均在本目录，
生产实体在 `/etc/systemd/system/` 与 `/etc/geo-platform-v2/`。

## 拓扑

- **常驻浏览器**：`geo-platform-v2-browser@<platform>.service`（`tools/resident_browser.py`，
  patchright headed Chromium，DISPLAY=:1，CDP 只绑 127.0.0.1）。
  实例与端口：doubao 19222 / deepseek 19224 / tongyi 19225 / yiyan 19226 / yuanbao 19227。
  env：`/etc/geo-platform-v2/browser-<slug>.env`（`RESIDENT_PLATFORM/PROFILE_DIR/PROXY_URL/CDP_PORT`）。
- **代理中继**：`geo-platform-v2-proxy-relay@<tag>.service`（`tools/wukong_auth_relay.py`，
  悟空预认证 CONNECT 中继——悟空网关只接受首个 CONNECT 即带凭据，Chromium 的
  407 质询模式被拒，故一律经中继）。实例：doubao(:19323，上海) / tj(:19324) / bj(:19325)。
  **中继实例是地域标签不是平台 slug**：doubao+yiyan 共用 19323，deepseek+yuanbao
  共用 19324，tongyi 用 19325。凭据只在中继 env（`UPSTREAM_PROXY_URL`），消费侧零秘密。
- **消费侧**：worker 的 `GEO_<P>_PROXY_URL` 与浏览器的 `RESIDENT_PROXY_URL` 都指中继
  本地端口；采集 attach 走 `GEO_<P>_CDP_URL`。

## 代理轮换（换上游节点 / 轮换凭据）

1. 改 `/etc/geo-platform-v2/proxy-relay-<tag>.env` 的 `UPSTREAM_PROXY_URL`。
2. `systemctl restart geo-platform-v2-proxy-relay@<tag>`。
3. 消费侧端口不变，**无需联动重启浏览器/worker**；浏览器进程的既有连接随页面
   导航自然走新上游（要立即生效可再 restart 对应 `browser@<platform>`，注意共用
   同一中继的平台会一起换）。
4. **若上游出口城市变了**（不只是换同城市节点）：同步改
   `/etc/geo-platform-v2/worker-adapters.env` 的 `GEO_MEASUREMENT_EXIT_GB_MAP`
   里该平台 slug 的 6 位出口省码并 restart `geo-platform-v2-worker`——该映射是
   INV-1 合格性判定的 geo provenance 来源（2026-08-08 起），漂移会让新答案
   的 `observed_gb_code` 失真。

换 profile / 端口 / 本地代理指向：改 `browser-<slug>.env` + `restart
geo-platform-v2-browser@<slug>`。重启期间采集 attach 断连按
`browser-launch-failed` 诚实重试自愈（unit `Restart=always`）。

## 多 worker fencing（2026-08-07 起，s06_0012）

- `platform.browser_fence` 表 + `resident_browser.py` 复合锁（进程内锁 + DB lease
  + fencing token + 30s 心跳续期）。`browser_lock()`/`platform_browser()` 签名不变，
  captcha-assist 持锁语义不变。
- env（worker）：`GEO_BROWSER_FENCING=db|local`（**缺省 db**，fail-closed——DB 不可达
  抛 `BrowserBusyError`，绝不降级）、`GEO_BROWSER_FENCE_HOLDER`（缺省 hostname:pid）、
  `GEO_BROWSER_FENCE_TTL_S`（缺省 7200）、`GEO_BROWSER_FENCE_HEARTBEAT_S`（缺省 30）。
- **上线顺序硬约束：先应用迁移 s06_0012，再让 worker 跑 db 模式**；单 worker
  过渡期可显式 `GEO_BROWSER_FENCING=local`。
- holder 崩溃回收窗 = TTL（最坏 2h 该平台不可调度）；盯三个事件告警：
  `browser_fence_lost` / `browser_fence_preempted` / `browser_fence_release_stale`。

## 撞码/登录人工接管

- captcha-assist：batch 撞 `wall_captcha` → workflow 挂起 → 手机页
  `/api/v2/assist/<ticket>` 人工过码 → 断点续跑（细节见 `workflows/activities/captcha_assist.py`
  与 `definitions/collection.py` patch 门 `captcha-assist-v1`）。
- 登录/OTP 接管（开户用）：`tools/otp_assist_login.py --platform <slug>`——同一套
  bridge/ticket/通知骨架，人工在手机页完成登录（含输 OTP），profile 留在常驻浏览器。
