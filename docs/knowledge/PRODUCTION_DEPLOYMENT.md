# 本机知识服务生产发布

## 发布边界

生产 API、SiliconIndex 同步、connector、周度治理和知识备份从 `/opt/geo-platform-v2/current` 读取同一份不可变代码和同一份锁定 Python 环境。数据集、知识 artifact、SiliconIndex last-known-good、数据库、凭据和备份仍在 release 目录之外。原始 Git 工作树可以有用户改动，但不参与生产导入。

## 发布前

1. 确认目标提交已推送，工作树中的任务文件已经通过全量测试、OpenAPI、migration、Compose 和 systemd 校验。
2. 运行 `scripts/production_backup.py`，另行备份 `/var/lib/geo-platform-v2/knowledge` 和 `/etc/geo-platform-v2`；记录 manifest、文件大小和 SHA-256。
3. 把 PostgreSQL dump 恢复到隔离数据库，先执行 `alembic upgrade head` 和 release membership 回填检查。不得拿生产库第一次试 migration。
4. 记录旧 `/opt/geo-platform-v2/current` 目标、active knowledge release、数据库 migration head、API 进程启动时间和四个 timer 的下次运行时间。

### 请求级模型目录配置

生产 `/etc/geo-platform-v2/platform.env` 必须显式设置知识任务自己的默认模型和允许清单；不要直接复制调研或 Service 2 清单：

```dotenv
GEO_KNOWLEDGE_LLM_MODEL=gpt-5.6-luna
GEO_KNOWLEDGE_LLM_MODELS=gpt-5.6-luna,qwen3.7-plus
```

每个 ID 必须先有当前知识提示词与严格 Schema 的准入证据。2026-08-28 的任务证据位于 `docs/knowledge/evidence/knowledge-model-admission-20260828.json`：`gpt-5.6-luna` 与 `qwen3.7-plus` 已准入，`claude-opus-5` 因领域输出校验失败未准入。Qwen 的费用仍为未知，不得按免费处理。知识专用 key/base URL 可以通过 `GEO_KNOWLEDGE_LLM_*` 单独注入；空值才复用 `GEO_RESEARCH_LLM_*`。环境文件权限保持 0600，禁止通过 `VITE_*`、模型目录响应、日志或请求体公开。

修改配置前先把原文件连同权限、owner 和 SHA-256 备份到发布备份点。用解析后的模型目录做只读预检；若目录返回 `status=unavailable`，不得宣称模型选择可用，但 `deterministic_only` 仍可发布和烟测。

## 构建不可变 release

下面的 `<commit>` 必须是已推送的完整 Git SHA，不能使用脏工作树内容：

```bash
release_root=/opt/geo-platform-v2/releases/<commit>
sudo install -d -m 0755 /opt/geo-platform-v2/releases
sudo install -d -m 0755 "$release_root"
git archive <commit> | sudo tar -x -C "$release_root"
sudo chown -R xln:xln "$release_root"
sudo -u xln uv sync --project "$release_root" --frozen --no-dev
sudo chown -R root:root "$release_root"
sudo chmod -R a-w "$release_root"
```

release 内的 `.venv` 必须存在 `uvicorn`、`alembic`、`jsonschema` 和应用包。用 `uv sync --check --frozen --no-dev --project "$release_root"` 验证锁文件，不要从旧工作树复制 venv。

## 迁移、影子验证和切换

1. 用 `/etc/geo-platform-v2/platform.env` 的管理 DSN 从新 release 执行 `alembic upgrade head`。命令和日志不得打印 DSN。
2. 让新 release 在未占用端口启动一个单 worker 影子 API，验证 health/readiness、published-only、模型目录、默认/非默认模型、deterministic-only、模型隔离缓存、未允许模型拒绝、replica 下载和数据库/artifact materialization 一致性。
3. 安装仓库中的五个 systemd service 文件，`systemd-analyze verify` 后 `daemon-reload`。
4. 创建只指向完整 release 的临时 symlink，再用同一文件系统的 rename 原子替换 `current`；不要先删除现有 `current`。
5. 重启 API，手工触发 sync、connector、governance 和 backup 各一次，再启用/核对 timer。
6. 从真实 Operations Web `/platform/operations/knowledge-runtime` 依次执行默认模型、非默认模型和 deterministic-only 请求；重复同模型/同输入证明缓存命中，再切换模型证明不复用前一个模型缓存。回读请求模型、供应商实际模型、标识来源、目录/提示词/知识版本、采用状态、provider call、token、费用未知、时延、缓存和降级。
7. 对同一 API 直接提交未允许模型，确认 422 `knowledge_model_not_allowed` 且没有供应商调用；提交 deterministic-only 加显式模型，确认 422 `knowledge_model_not_applicable`。
8. 临时把同步目标指向拒绝连接的本机端口，确认业务请求不等待 Render、观察可写、本机可发布；恢复后执行三方对账。

SiliconIndex 只有存在真实、已审、允许公开的本地增量时才发布。没有增量时以完整本地 Git/HTTP publication drill 验证外发链路，不制造假品牌或空公共版本。

## 回滚

代码回退只把 `/opt/geo-platform-v2/current` 原子指回上一不可变 release，并重启/验证服务；不要 downgrade 数据库或删除新表。知识回滚另走 Knowledge API，必须同时切换 artifact 和数据库 release membership，并用代表对象证明读取版本变化。若新 release 已产生无法被旧代码读取的数据，先保持新代码、只回滚知识版本，再按兼容性决定代码回退。

发布完成后保存：目标/旧 commit、release 目录 hash、备份点、migration 前后版本、systemd InvocationID、API 启动时间、active/previous knowledge release、影子与正式请求收据、断网/恢复收据和所有定时任务状态。
