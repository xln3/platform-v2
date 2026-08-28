# intake-form 独立部署指南

`apps/intake-form` 是免登录客户填表页（邀请 token 制，ADR-0006）。产物为纯静态 SPA（`build/client/`），有两种部署方式。

## 方式 A：主站 nginx 伺服（默认，已上线）

主站 8443 直接伺服：`https://<host>:8443/platform/intake-form/#t=<token>`。
邀请链接由运营侧签发：`POST /api/v2/projects/{pub}/intake/invites`（operator 权限），token 原文只在响应出现一次。

## 方式 B：独立服务器静态托管

1. 构建：`cd apps/intake-form && pnpm build`，把 `build/client/` 拷到目标服务器任意静态服务（nginx/Caddy/对象存储）。
2. API 指向主站，二选一：
   - **反代（推荐）**：目标服务器 nginx 加 `location /api/v2/ { proxy_pass https://<主站>:8443; proxy_read_timeout 300s; }`，前端同源调用，零 CORS 改动。注意 `proxy_pass` 到 8443 需 `proxy_ssl_verify off` 或信任主站证书。
   - **直连**：构建前设 `VITE_GEO_API_BASE=https://<主站>:8443`，并在主站 `/etc/geo-platform-v2/platform.env` 配 `GEO_CORS_ORIGINS=https://<填表页域名>`，重启 `geo-platform-v2-api`。token 走 `X-Intake-Token` 头，无 cookie 依赖，跨域无 SameSite 问题。
3. SPA fallback：所有路径回退到 `index.html`（照主站 `try_files $uri $uri/ /platform/intake-form/index.html;` 的语义；若部署在非 `/platform/intake-form/` 路径，需用对应 basename 重新构建）。

## 运营要点

- token：TTL 缺省 168h（`GEO_INTAKE_INVITE_TTL_HOURS`）；AI 调研+AI 扩写共用配额缺省 3 次/邀请（`GEO_INTAKE_INVITE_AI_QUOTA`，LLM 成本闸门）；可随时 `DELETE .../invites/{pub_id}` 撤销。
- 提交（submit）后该邀请全部写端点返回 409 `invite_submitted`，表单只读。
- SiliconIndex 是跨项目唯一品牌事实源。独立同步任务从 `GEO_SILICONINDEX_BASE_URL`（缺省 `https://siliconindex-consumer.onrender.com/data/v1`）拉取完整发布版，校验发布哈希和引用后原子更新 `CURRENT`；API、填表和品牌榜单只读同一个本地版本，绝不在项目请求中临时联网解析品牌。
- 快照目录由 `GEO_SILICONINDEX_SNAPSHOT_DIR` 指定（开发缺省 `data/siliconindex-snapshots`；生产建议 `/var/lib/geo-platform-v2/siliconindex`）。手工同步可执行 `.venv/bin/python tools/sync_siliconindex_snapshot.py`，只看状态可加 `--status`。生产单元见 `deploy/production/geo-platform-v2-siliconindex-sync.{service,timer}`；安装 timer 前必须让 API 使用同一路径。
- 同步按不可变 `release_id` 落目录，拒绝哈希不符、跨源下载、坏引用和版本倒退；刷新失败时 `CURRENT` 保持上一有效版本。目录尚未安装时，填表候选功能返回 `available:false`，网安排名使用仓库内由 SiliconIndex release 生成且带 hash 的 `siliconindex_projection_*` 固定投影，不回退到项目自建别名表；该投影是可再生读模型，不允许人工维护。
- 合同文档：`GET /api/v2/projects/{pub}/intake/contract.docx`（intake:read）以清洁交付1版为模板填槽，缺数据的槽位留空并在 `X-Contract-Unfilled-Count` 头披露数量（INV-32 零合成）。
