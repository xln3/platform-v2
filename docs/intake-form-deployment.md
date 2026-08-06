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
3.  SPA fallback：所有路径回退到 `index.html`（照主站 `try_files $uri $uri/ /platform/intake-form/index.html;` 的语义；若部署在非 `/platform/intake-form/` 路径，需用对应 basename 重新构建）。

## 运营要点

- token：TTL 缺省 168h（`GEO_INTAKE_INVITE_TTL_HOURS`）；AI 调研+AI 扩写共用配额缺省 3 次/邀请（`GEO_INTAKE_INVITE_AI_QUOTA`，LLM 成本闸门）；可随时 `DELETE .../invites/{pub_id}` 撤销。
- 提交（submit）后该邀请全部写端点返回 409 `invite_submitted`，表单只读。
- SiliconIndex：快照目录 `GEO_SILICONINDEX_SNAPSHOT_DIR`（缺省 `data/siliconindex-snapshots`，相对 API 工作目录，生产建议绝对路径）；目录缺失时分类/模板问法功能自动隐藏（`available:false`），不影响其他功能。
- 合同文档：`GET /api/v2/projects/{pub}/intake/contract.docx`（intake:read）以清洁交付1版为模板填槽，缺数据的槽位留空并在 `X-Contract-Unfilled-Count` 头披露数量（INV-32 零合成）。
