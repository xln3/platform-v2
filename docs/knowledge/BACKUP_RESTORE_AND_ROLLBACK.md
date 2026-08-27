# 备份、恢复与 Release 回滚

## 覆盖范围

主备份中的 PostgreSQL custom dump覆盖 `knowledge` schema、RLS、append-only triggers 和治理历史。`geo-platform-v2-knowledge-backup.timer` 单独备份 `/var/lib/geo-platform-v2/knowledge`，包括不可变 release、manifest、CURRENT/PREVIOUS 和治理报告。

两类备份缺一不可。只有数据库没有 artifact 时无法验证激活 hash；只有 artifact 没有数据库时会丢失 observation、evidence、adjudication 和审计链。

## Artifact 备份与验证

```bash
.venv/bin/python tools/knowledge_release_backup.py backup \
  --source /var/lib/geo-platform-v2/knowledge \
  --backup-root .production-backups/knowledge

.venv/bin/python tools/knowledge_release_backup.py verify \
  --manifest .production-backups/knowledge/<timestamp>/manifest.json
```

备份工具拒绝 symlink，逐文件记录大小和 SHA-256，并再次读取 tar 验证。权限为 0600。当前工具不自动删除旧备份；保留期由运维变更单单独配置，避免误删唯一恢复点。

## 恢复演练

先恢复到一个不存在或为空的目录。工具拒绝覆盖非空目录。

```bash
.venv/bin/python tools/knowledge_release_backup.py restore \
  --manifest <backup>/manifest.json \
  --target <empty-restore-dir>
```

恢复后用 `KnowledgeReleaseStore.verify(active_release)` 检查 release hash。数据库应恢复到新的隔离数据库，运行 `alembic current`、RLS 跨租户查询和 observation append-only 更新拒绝测试。不要直接覆盖生产目录做演练。

2026-08-27 的本地演练恢复了 4 个 artifact 文件。archive hash 为 `sha256:775c25507e257c547e67195d4b355032e4ba513c241567e102aa6922f6f4a79a`。恢复后的 active release 是 `knowledge-2026-08-27.1`，内容 hash 重新验证为 `sha256:0f0ad8c316336d0822a904bd0bc0e80bc31a08f731a94cc827b8bd37b0178d3f`。

最终 release 链生成后再次演练。`/tmp/geo-knowledge-recovery-20260827-final-Y37Tlv` 中的备份恢复了 7 个文件，archive hash 为 `sha256:9f5cbfff2068e1981dc6b1136a923a8004c4d9f03f09ac300d889a5db6088836`。空目录恢复后 `CURRENT=knowledge-2026-08-27.2`、`PREVIOUS=knowledge-2026-08-27.1`，active content hash 重新验证为 `sha256:05eee1d75251efdc151e65afe9856d62f0c5ba21486a1ee23d3f09f1dac4c9d0`。

生产切换到最终血缘版本后第三次演练。备份 manifest 为 `.production-backups/knowledge/20260827T055637Z/manifest.json`，共 9 个文件，archive hash 为 `sha256:2bb3dca98844cf61ffbc87f7331e246b0df5a49bd2183c3ffb0e12480bdbd653`。该备份恢复到空目录 `/tmp/geo-knowledge-production-restore-20260827.l36TVs` 后逐文件验证通过；`CURRENT=knowledge-2026-08-27.3`、`PREVIOUS=knowledge-2026-08-27.2`，对应内容 hash 分别为 `sha256:93a04f23f5585efa6e569a973953f65acd8ee4897108982cb73f412b3ec21261` 和 `sha256:05eee1d75251efdc151e65afe9856d62f0c5ba21486a1ee23d3f09f1dac4c9d0`。

本次生产发布前还完成了全量服务备份 `.production-backups/20260827T051856Z`，以及 PostgreSQL custom dump `.deploy-backups/knowledge-evolution-predeploy-20260827T1320CST/geo_platform.pre-s17.dump`。数据库 dump 为 53,946,120 bytes，SHA-256 为 `d6935b140cdecd9a2e2aea55df6ab27fe1fb119857e79605ea637bc143f1e4d3`。

## Release 回滚

回滚不修改旧 release。调用 `/releases/{release_id}/rollback` 只把 active pointer 原子切换到已验证 release，并追加 activation/audit 历史。

步骤：备份；验证目标 manifest；执行 rollback；检查 CURRENT/PREVIOUS；运行 deterministic smoke；检查正式报告；观察 metrics。若数据库 audit commit 失败，服务会把 artifact pointer 恢复到先前 release。

## 灾难恢复顺序

1. 恢复 PostgreSQL 到隔离实例并检查 dump。
2. 恢复 knowledge artifacts 到空目录并逐文件验 hash。
3. 设置 API 指向恢复目录和数据库。
4. 运行 migrations 到预期 head，不跨版本猜测。
5. 检查 active release 的数据库 hash 与 artifact hash 一致。
6. 运行 RLS、append-only、gold set、readiness 和 deterministic smoke。
7. 最后切换正式流量；模型策略先保持 deterministic-only。
