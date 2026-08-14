#!/usr/bin/env bash
# 以生产环境变量运行指定命令（env 文件仅 root 可读，经 sudo 读出后经 bash source 注入；
# 已核验两个 env 文件无含空格/反引号/$ 的裸值，source 安全； secrets 不落盘、不打印）。
set -euo pipefail
set -a
source <(sudo -n cat /etc/geo-platform-v2/platform.env)
source <(sudo -n cat /etc/geo-platform-v2/worker-adapters.env)
# wukong.env 仅 worker 部分模块需要，缺它不影响扇出/侧车；存在则注入。
if sudo -n test -f /etc/geo-platform-v2/wukong.env; then
  source <(sudo -n cat /etc/geo-platform-v2/wukong.env)
fi
set +a
cd /home/xln/geo-system/platform-v2
exec "$@"
