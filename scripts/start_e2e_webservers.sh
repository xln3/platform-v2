#!/usr/bin/env bash
set -euo pipefail

pnpm build
exec node scripts/e2e_static_servers.mjs
