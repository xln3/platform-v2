# 周度知识治理 Runbook

## 调度

`geo-platform-v2-knowledge-governance.timer` 每周一 03:37 运行，并加入最多 30 分钟稳定随机延迟。`geo-platform-v2-siliconindex-sync.timer` 每六小时刷新公共 last-known-good snapshot。`geo-platform-v2-knowledge-connector.timer` 每五分钟处理 API 写入的耐久 connector 队列。三个任务失败都不会让项目请求访问远端。

安装后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now geo-platform-v2-siliconindex-sync.timer
sudo systemctl enable --now geo-platform-v2-knowledge-connector.timer
sudo systemctl enable --now geo-platform-v2-knowledge-governance.timer
sudo systemctl enable --now geo-platform-v2-knowledge-backup.timer
```

`/etc/geo-platform-v2/platform.env` 必须设置 `GEO_KNOWLEDGE_GOVERNANCE_TENANT_PUB_ID`。模型 key 可选；缺失时 deterministic 能力和本地治理仍正常。

## 周度流程

1. 读取当前 local knowledge release 和 hash。
2. 尝试下载并完整验证 SiliconIndex manifest 与所有数据文件。
3. 从本机知识 artifact 读取最后共同的 SiliconIndex release，而不是把“当前远端”误当 base。
4. 将共同 base、已验证 upstream 和当前 `local_ahead` 对象编译成相同品牌投影后做三方对账。
5. 远端变化幂等写入 `shared` observation；同一远端 release 不重复计数。终态候选只有新 release 形成新 evidence version 时才重开。
6. 同字段双写形成 state=`conflict` 的 change set；冲突变化或消失时旧冲突转 `superseded`，metrics 只统计仍未解决的冲突。
7. 生成内容寻址 governance report 和公开增量候选；查看 backlog、review ready、oldest age、evidence gap 和 conflicts。
8. 审核新增观察、证据缺口和冲突，用四眼流程形成 change set。
9. 运行有时间截点的历史回放；品牌 impact gate 检查评测集 hash 和新错误预算后，发布新的本机 release。
10. 只导出 reviewed、public、`local_ahead`、有公开证据且已脱敏的增量。
11. SiliconIndex 发布后从公网回读 manifest、数据和 hash；publish connector 只有版本/hash 与预期一致才记录 success。

人工离线验证命令：

```bash
.venv/bin/python tools/run_knowledge_governance_batch.py --offline
.venv/bin/python tools/run_knowledge_connector_queue.py --limit 20
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
systemctl status geo-platform-v2-knowledge-connector.service
systemctl list-timers 'geo-platform-v2-*knowledge*'
journalctl -u geo-platform-v2-knowledge-governance.service --since '8 days ago'
curl -fsS http://127.0.0.1:8020/api/v2/knowledge/v1/health
curl -fsS http://127.0.0.1:8020/api/v2/knowledge/v1/readiness
```

metrics 需要关注 active release、release age、candidate backlog、oldest candidate age、conflicts、model call/error/latency/cost、connector last attempt/success 和 export lag。
