# 本机知识服务生产发布

## 发布边界

生产 API、SiliconIndex 同步、connector、周度治理和知识备份从 `/opt/geo-platform-v2/current` 读取同一份不可变代码和同一份锁定 Python 环境。数据集、知识 artifact、SiliconIndex last-known-good、数据库、凭据和备份仍在 release 目录之外。原始 Git 工作树可以有用户改动，但不参与生产导入。

## 发布前

1. 确认目标提交已推送，工作树中的任务文件已经通过全量测试、OpenAPI、migration、Compose 和 systemd 校验。
2. 运行 `scripts/production_backup.py`，另行备份 `/var/lib/geo-platform-v2/knowledge` 和 `/etc/geo-platform-v2`；记录 manifest、文件大小和 SHA-256。
3. 把 PostgreSQL dump 恢复到隔离数据库，先执行 `alembic upgrade head` 和 release membership 回填检查。不得拿生产库第一次试 migration。
4. 记录旧 `/opt/geo-platform-v2/current` 目标、active knowledge release、数据库 migration head、API 进程启动时间和四个 timer 的下次运行时间。

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
2. 让新 release 在未占用端口启动一个单 worker 影子 API，验证 health/readiness、published-only、LLM、replica 下载和数据库/artifact materialization 一致性。
3. 安装仓库中的五个 systemd service 文件，`systemd-analyze verify` 后 `daemon-reload`。
4. 创建只指向完整 release 的临时 symlink，再用同一文件系统的 rename 原子替换 `current`；不要先删除现有 `current`。
5. 重启 API，手工触发 sync、connector、governance 和 backup 各一次，再启用/核对 timer。
6. 从真实入口执行 deterministic-only 和允许模型的请求，确认知识版本、模型/提示词、状态、置信度和降级披露。
7. 临时把同步目标指向拒绝连接的本机端口，确认业务请求不等待 Render、观察可写、本机可发布；恢复后执行三方对账。

SiliconIndex 只有存在真实、已审、允许公开的本地增量时才发布。没有增量时以完整本地 Git/HTTP publication drill 验证外发链路，不制造假品牌或空公共版本。

## 回滚

代码回退只把 `/opt/geo-platform-v2/current` 原子指回上一不可变 release，并重启/验证服务；不要 downgrade 数据库或删除新表。知识回滚另走 Knowledge API，必须同时切换 artifact 和数据库 release membership，并用代表对象证明读取版本变化。若新 release 已产生无法被旧代码读取的数据，先保持新代码、只回滚知识版本，再按兼容性决定代码回退。

发布完成后保存：目标/旧 commit、release 目录 hash、备份点、migration 前后版本、systemd InvocationID、API 启动时间、active/previous knowledge release、影子与正式请求收据、断网/恢复收据和所有定时任务状态。
