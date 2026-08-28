#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec .venv/bin/pytest -q \
  -m "not isolated_postgres and not knowledge_postgres and not compat_postgres and not external_fixture and not document_toolchain and not slow and not service_integration" \
  --fail-on-skip "$@"
