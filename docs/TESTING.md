# 测试维护契约

测试按反馈速度和依赖边界分车道。默认命令必须快速、确定、无外部依赖；需要数据库、服务、文档工具链或仓库外评审材料的用例必须使用注册 marker，并由对应车道显式执行。

## 日常反馈

| 命令                     | 覆盖范围                                                                       |
| ------------------------ | ------------------------------------------------------------------------------ |
| `pnpm test`              | 各 workspace 单元/组件测试、根目录浏览器运行时守卫、Python 快速车道            |
| `pnpm test:python`       | 排除所有显式依赖车道的 Python 测试                                             |
| `pnpm test:e2e:contract` | 浏览器合同测试；所有业务流跑桌面端，仅响应式/可访问性/视觉套件重复平板和手机端 |

默认 Python 车道启用 `--fail-on-skip`。选中的测试一旦 skip，整条车道失败。新增测试不得通过“依赖不存在就 skip”进入默认车道；应把依赖写成 marker 和可复现的准备步骤。

## 显式依赖车道

| Marker / 命令                                       | 前置条件                                               |
| --------------------------------------------------- | ------------------------------------------------------ |
| `slow` / `pnpm test:python:slow`                    | 高数据量或其他长耗时但无需外部服务的测试               |
| `service_integration` / `pnpm test:python:services` | 仓库管理的 Temporal 等服务已启动且健康                 |
| `isolated_postgres` / `pnpm test:python:isolated`   | 独占、可破坏、位于当前 migration head 的测试库         |
| `knowledge_postgres` / `pnpm test:python:knowledge` | 独占、可破坏、位于当前 migration head 的知识治理测试库 |
| `compat_postgres` / `pnpm test:python:compat`       | 固定历史 migration 的兼容性测试库                      |
| `document_toolchain` / `pnpm test:python:documents` | LibreOffice 和规定字体已安装                           |
| `external_fixture` / `pnpm test:python:external`    | 已评审的仓库外 fixture 位于约定位置                    |

CI 使用 `scripts/prepare_ci_test_databases.sh` 建立两个用途隔离的 PostgreSQL 库。开发机不得把默认业务库冒充隔离测试库。缺库、版本错误、缺工具或缺 fixture 都应 fail-loud，不能显示为通过。

真实 Vault Transit 验证不在 pytest 的 `testpaths` 中，使用 `pnpm test:vault-integration` 单独运行。浏览器真实 API 边界使用 `pnpm test:e2e:live`；它要求 45200 端口上的可写 API、当前 head 数据库，以及由 `scripts/prepare_live_api_e2e_dataset.py` 生成的 20k+ 媒体数据集。`pnpm test:e2e:all` 仅用于上述依赖全部就绪时合并执行。

## 浏览器产物与视觉基线

普通 Playwright 运行只允许写入被忽略的 `test-results/`。测试代码不得直接改写 `tests/s04-evidence/`、`tests/visual-evidence/`、`tests/e2e-results/` 或其他已跟踪证据目录。

视觉失败必须先检查 actual、expected 和 diff，确认是合法界面演进后，才可通过 `e2e-baseline-regen` workflow 或聚焦的 `--update-snapshots=changed` 更新具体快照。不得用批量重录掩盖功能断言失败。历史交付证据不属于测试缓存，未经确认归档位置不得删除。

## 新增或修改测试

- 优先验证公开行为和稳定合同，不锁定内部实现细节。
- 同一个业务流只在桌面端完整执行；只有确实验证响应式行为的 spec 才加入平板/手机矩阵。
- 大型语义流程与视觉快照分开，避免旧图片阻断后续功能断言。
- 测试必须拥有自己的数据、端口和输出路径，不依赖开发机残留状态。
- 新车道、marker 或根命令必须同步更新 `scripts/check_ci_workflow.py`，使 CI 漂移守卫能够发现漏跑。
