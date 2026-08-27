# 周度知识治理 Runbook

## 调度

`geo-platform-v2-knowledge-governance.timer` 每周一 03:37 运行，并加入最多 30 分钟稳定随机延迟。`geo-platform-v2-siliconindex-sync.timer` 每六小时刷新公共 last-known-good snapshot。两个任务失败都不会让项目请求访问远端。

安装后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now geo-platform-v2-siliconindex-sync.timer
sudo systemctl enable --now geo-platform-v2-knowledge-governance.timer
sudo systemctl enable --now geo-platform-v2-knowledge-backup.timer
```

`/etc/geo-platform-v2/platform.env` 必须设置 `GEO_KNOWLEDGE_GOVERNANCE_TENANT_PUB_ID`。模型 key 可选；缺失时 deterministic 能力和本地治理仍正常。

## 周度流程

1. 读取当前 local knowledge release 和 hash。
2. 尝试下载并完整验证 SiliconIndex manifest 与所有数据文件。
3. 远端失败时记录 degraded connector run，继续验证本地 snapshot 和 release。
4. 生成内容寻址 governance report。
5. 查看 candidate backlog、review ready、oldest age 和 conflicts。
6. 审核新增观察、证据缺口和三方合并冲突。
7. 用四眼流程形成 change set。
8. 发布新的本机 release，并运行金标和只读影子回放。
9. 只导出 approved、public、有证据且已脱敏的增量。
10. SiliconIndex 发布后回读线上 manifest、数据和 hash；connector 标为 reconciled。

人工离线验证命令：

```bash
.venv/bin/python tools/run_knowledge_governance_batch.py --offline
.venv/bin/python tools/evaluate_brand_knowledge.py --policies deterministic_only
.venv/bin/python tools/sync_siliconindex_snapshot.py --status
```

## 紧急发布

紧急纠错不需要等周一，但仍需要证据、独立裁决、独立 change-set approval、领域质量门和独立 publisher。发布前备份数据库和 artifacts。先发布但不激活，验证 hash 后再 activate。

## 远端连续失败七天

不要修改 `CURRENT`，不要删除已验证 snapshot，也不要让 API 临时联网。每天检查 sync status 的 current release 是否未变。继续接收观察、审核候选和发布本地 release。远端恢复后从上次共同 base 做三方合并，再导出 local-ahead 变更；不得用恢复后的 upstream 覆盖本地知识。

## 核查命令

```bash
systemctl status geo-platform-v2-knowledge-governance.service
systemctl list-timers 'geo-platform-v2-*knowledge*'
journalctl -u geo-platform-v2-knowledge-governance.service --since '8 days ago'
curl -fsS http://127.0.0.1:8020/api/v2/knowledge/v1/health
curl -fsS http://127.0.0.1:8020/api/v2/knowledge/v1/readiness
```

metrics 需要关注 active release、release age、candidate backlog、oldest candidate age、conflicts、model call/error/latency/cost、connector last attempt/success 和 export lag。
