# V2 任务地域代理路由

## 结论

`CollectionTaskInput.region` 已从冻结配置进入 Temporal payload；代理秘密不进入该
payload。`platform_registry` 在浏览器启动前调用 worker-local resolver：

1. 把 `CN-BJ` / GB 六位码 / 中文代表城市归一成 GB 地域；
2. 在悟空 server truth 与本地 cache 中复用同地域、剩余时间充足的租约；
3. 没有租约时以 `proxy_lease_unavailable` 停止；采集任务永不申请、永不购买；
4. 对真实出口 IP 做地域硬校验，地域不符即拒绝，绝不回落到固定错误地域；
5. operator 通过一次性 systemd oneshot 明确确认付费，随后重跑任务，resolver 复用新订单。

没有活动租约数或每日申请数上限。防重复扣费依靠结构正确性：所有入口共用 cache
旁的跨进程排他锁；购买前强制刷新悟空订单列表并按城市精确查重；付费 POST 前持久化
per-city purchase intent。若请求可能已到达供应商但订单尚未出现，intent 会进入
`uncertain`，任何重试购买都会被阻断，直到 operator 在悟空后台核实。

代理 URL 只在 worker 内存、浏览器启动参数和权限为 `0600` 的 cache 中出现。日志、
heartbeat、Temporal task/result 只记录地域、来源和 provider action。

## 配置

`/etc/geo-platform-v2/worker-adapters.env`：

```dotenv
GEO_REGION_PROXY_MODE=wukong
GEO_WUKONG_MODULE_ROOT=/home/xln/geo-system/server
GEO_WUKONG_CACHE=/home/xln/geo-system/platform-v2/runtime/wukong_leases.json
GEO_WUKONG_MIN_REMAINING_MIN=20
```

悟空账号单独放 `/etc/geo-platform-v2/wukong.env`，`root:root 0600`；模板见
`deploy/production/wukong.env.example`。五个平台原 `GEO_*_PROXY_URL` 保留作
`GEO_REGION_PROXY_MODE=static` 回滚路径，在动态模式下只会被本次任务解析出的代理覆盖。
`WUKONG_CACHE` 必须与 `GEO_WUKONG_CACHE` 指向同一个文件，旧 geosys 服务也加载同一
`WUKONG_CACHE`；不一致时 provider 初始化会直接失败，避免跨入口出现两个购买状态副本。

内置覆盖中国大陆 31 个 ISO subdivision code（如 `CN-BJ`、`CN-SC`）以及旧链的
GB/中文城市映射。自定义别名使用：

```dotenv
GEO_REGION_GB_MAP=custom-beijing:110000,custom-chengdu:510100
```

## 人工确认付费

先阅读悟空后台余额、地域和当前订单；确认真实费用后，operator 才执行：

```bash
sudo systemctl start geo-platform-v2-proxy-purchase@CN-SC.service
sudo journalctl -u geo-platform-v2-proxy-purchase@CN-SC.service -n 30 --no-pager
```

`systemctl start` 就是本次付费确认动作。该模板没有 `[Install]`，不能开机自启；CLI
还要求 `--confirm-spend`，缺任一条件都不会购买。成功只打印地域、action 与观测 GB，
不打印代理凭据。购买完成后重新发起失败的 collection run。

只读/复用探针使用 `manage_region_proxy.py resolve`。该命令与采集任务都不会创建订单。

如果付费命令返回 `proxy_purchase_reconciliation_required`，先在悟空后台确认该地域没有
新增订单，再显式清除不确定 intent：

```bash
sudo systemd-run --wait --pipe --collect \
  -p User=xln -p Group=xln \
  -p WorkingDirectory=/home/xln/geo-system/platform-v2 \
  -p EnvironmentFile=/etc/geo-platform-v2/worker-adapters.env \
  -p EnvironmentFile=/etc/geo-platform-v2/wukong.env \
  /home/xln/geo-system/platform-v2/.venv/bin/python \
  tools/manage_region_proxy.py clear-purchase-intent \
  --region CN-SC --confirm-no-order
```

若订单已经出现，不要清除；重新运行 `resolve` 会通过 server truth 收敛为复用。

## 回滚

```bash
sudoedit /etc/geo-platform-v2/worker-adapters.env
# GEO_REGION_PROXY_MODE=static
sudo systemctl restart geo-platform-v2-worker
```

回滚只恢复平台固定代理，不删除或购买任何悟空订单。
