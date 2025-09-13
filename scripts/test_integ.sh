#!/usr/bin/env bash
set -euo pipefail
export INTEG_STACK_UP=${INTEG_STACK_UP:-1}
export API=${API:-http://localhost:8000}
export KEY=${KEY:-dev-apikey-123}
curl -sf "$API/api/v1/health" >/dev/null
pytest -m integration -vv -ra "$@"
