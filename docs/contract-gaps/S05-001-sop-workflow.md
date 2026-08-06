# S05-001 — GEO 信源文章 SOP 工作流

- Reporter/session: S05（2026-07-28）
- Status: resolved on 2026-07-28
- Owners touched: S00（OpenAPI/API client/guard）、S01（RBAC）、S03（Operations Web）
- Blocking work: 无；所有改动均为新增能力或对共享注册表的纯追加
- Detailed contract: `docs/sop/SOP_MODULE_DESIGN.md`

## Gap

platform-v2 目前没有把《GEO信源型文章通用写作与验证流程 SOP》落成可执行、可监测的
业务工作区。已有采集、报告和人工效果复测登记能力不能表达冻结查询集、文章版本、发布前
停止条件、公开状态、索引观察、同题复测、文章级引用归因和追加式工作日志之间的完整关系。

## Accepted additive change

- 新增 PostgreSQL schema `sop` 与 16 张 tenant-scoped、FORCE RLS 表，迁移号
  `s05_0001`，基于 `s04_0029`。
- 新增 `/api/v2/sop` REST 面及 `sop:read` / `sop:write` 权限：
  operator、analyst 读写，reviewer 只读，admin 继承 `*`，customer 不授权。
- 新增 Operations Web 的“信源 SOP”导航、项目列表和项目工作区；14 个步骤分别提供
  “监测”和“操作台”视图，覆盖 SOP 阶段 0–15。
- 重新生成 OpenAPI 与 schema client；在 `@geo/api-client` 追加 SOP 安全投影边界，
  应用代码不直接使用 `fetch`、原始 generated client 或 `/api/v2/` 字面量。

## Safety and compatibility

- 纯增量：不修改任何既有 API 的请求、响应或权限语义。
- 不复活旧 `post_*` 表，也不接自动发布执行；发布记录由运营手工登记。
- 不接自动采集调度或 LLM 自动写稿/审查；本期工作区忠实记录人工操作和真实结果。
- `publication_ready=false` 时服务端拒绝创建发布记录；工作日志没有更新或删除端点。
- 所有查询同时依赖 RLS 与显式 `tenant_pub_id` 条件，跨租户按 404 处理。

## Validation required for resolution

- Alembic downgrade/upgrade 与唯一 head 检查。
- SOP 后端 ruff、mypy、集成测试和 OpenAPI drift 检查。
- Operations Web typecheck、组件测试与 frontend architecture guard。
- 真实 API 冒烟覆盖项目创建、查询集冻结、发布硬门、公开状态、复测、归因和 dashboard。

## Resolution evidence

- `s05_0001` 是开发库唯一 Alembic head/current，迁移已验证
  upgrade/downgrade/upgrade。
- SOP 闭环集成测试通过；全量 integration `74 passed`，覆盖跨租户 404、
  customer 403、冻结后写入 409、发布硬门和公开终态。
- API client typecheck 与 `74` 项测试通过；Operations Web typecheck、`39` 项测试
  与生产构建通过。
- `pnpm check:api` 通过，OpenAPI、manifest、generated schema 和 frontend
  architecture guard 无漂移。
- 桌面/移动端浏览器冒烟均渲染 14 个步骤，控制台和页面错误为 0。
