# GAP-20260725-001: media prices dataset artifact endpoint

- Reporter/session: 媒体比价台运营页面任务（2026-07-27）
- Status: accepted
- Contract/ADR affected: OpenAPI `/api/v2/datasets/media-prices`（新增只读数据集制品端点）；`@geo/api-client` 新增 `getMediaPricesDataset` 边界；`scripts/check_frontend_contracts.py` operations `external_sections` 增加 `"media-prices"`
- Blocking work: 无（纯增量）
- Current behavior: 平台没有任何"数据集制品"类端点；三平台媒体比价数据（~20,600 行，~12MB JSON）只存在于 developlog 研究目录的离线页面（`window.PRICE_DATA` 包装），运营端不可见。
- Needed behavior: 运营/审核/管理员在 operations-web 查看/筛选/导出比价数据；数据由离线脚本（`developlog/research/prfabu/compare/build_data.py`）刷新到仓库根 `.datasets/`（已 gitignore），API 从服务器文件读取返回，附带 `X-Dataset-Sha256` 完整性头与 `Cache-Control: private, no-store`。
- Backward compatibility: 纯增量。无既有端点变更；鉴权复用 `account:read`（operator/reviewer/admin），customer/analyst 403。`external_sections` 与 `operationsNav` 同步新增一项，既有 nav id 集合不变。
- Proposed OpenAPI/schema diff: `GET /api/v2/datasets/media-prices` → 200 `application/json`（schema `unknown`，与 reports 制品端点一致，由客户端边界校验信封：content-type、25MB 上限、JSON 可解析、`rows` 为数组）；404 `dataset_not_found`；401/403 走统一 `ApiError`。api-client 边界结果在 `{ready, forbidden, unavailable}` 之外增加 `{kind:'missing'}`（映射 404），用于页面区分"数据集未生成"与瞬时不可用——这是对三态约定的一个端点级扩展。
- Owning session: 本任务会话
- Resolution and validation: 已实现。`api/geo_platform/datasets/router.py`（含 mtime+size 小缓存与 sha256 sidecar/实时回退）、`config.py` 追加 `datasets_dir`（env `GEO_DATASETS_DIR`，缺省回退仓库根 `.datasets`）；`tests/integration/test_datasets_media_prices.py` 覆盖 operator 200+sha256 头 / customer 403 / 缺失 404；`packages/api-client/src/index.test.ts` 覆盖 ready/forbidden/missing/畸形与超限；`apps/operations-web/app/features/media-prices/` 页面与 vitest 用例齐全。

## 2026-07-27 追加：在线刷新流水线端点

- 新增 `POST /api/v2/datasets/media-prices/refresh`（202 `{state:"running"}`；锁文件存在 → 409 `refresh_already_running`）与 `GET /api/v2/datasets/media-prices/refresh-status`（无记录 → `{state:"never"}`）。
- 权限选择：refresh 为写操作，采用 `account:operate`（与 governance/capability/collection 的账号写操作一致，覆盖 operator+admin，排除 reviewer/customer/analyst/worker）；status 与数据集读取仍为 `account:read`。无权限妥协。
- 作业模式：uvicorn 多 worker 下禁止内存态作业——API 只负责锁判定与 `asyncio.create_subprocess_exec(sys.executable, tools/media_prices_refresh.py)`（不接受任何用户输入进命令行），进度/结果全部落 `.datasets/media-prices.refresh.json`（原子写），status 端点纯读文件。并发闸=`.datasets/media-prices.refresh.lock`（O_EXCL，>45min 僵死自动清理；品达全量分页纳入后相应延长）。
- api-client 新增 `requestMediaPricesRefresh`（`{started|already_running|forbidden|unavailable}`）与 `getMediaPricesRefreshStatus`（`ProjectResourceResult`）；品达全量分页纳入后，前端轮询调整为间隔 10s、上限 45 分钟，done 后自动重载数据集。
- prfabu 会话：`.datasets/prfabu_session.txt`（Netscape 格式 PHPSESSID，gitignore 纳管）人工维护；会话失效时流水线标记 `stale: session_expired` 并沿用既有原始分页，不做自动打码登录。

## 2026-07-28 自媒体与品达扩展

- 自媒体目录拆为 `.datasets/media-wemedia.json` + `.sha256`，由独立
  `GET /api/v2/datasets/media-wemedia` 提供；运营页通过“新闻媒体 / 自媒体”
  Tab 首次激活懒加载，切回后复用内存数据。
- 共享 JSON 响应仍保持 25 MiB 上限；仅懒加载的 `/media-wemedia` 制品采用
  64 MiB 路径级上限，并继续在解析前同时校验声明长度、实际解码字节数与 SHA-256。
- 新闻与自媒体均纳入品达发稿作为第六价格源。品达使用登录后
  `/home_web/mediadata` 的 `pageSize=1000` 可重试单路分页，避免其大 CSV 导出长流
  中断造成静默缺行；单页原始响应独立缓存。
- 品达会话由
  `SUPPLIER_ACCOUNT=... SUPPLIER_PASSWORD=... DISPLAY=:1 node tools/supplier_session_login.mjs pinda`
  自动更新 `.datasets/pinda_session.txt`（0600）；逆传播使用同一工具的
  `nichuanbo` headed/noVNC 模式，成功后写 `.datasets/nichuanbo_storage.json`（0600）。
- 逆传播当前线上极验返回 `error_113`（服务端 forbidden），强制离线模式又被其登录
  后端拒绝，因此在供应商修复验证码配置前不伪造登录态、不向价格数据集写入空壳来源。
