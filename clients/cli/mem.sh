#!/usr/bin/env sh
# micro_mem 命令包装器（macOS / Linux）
# 仓库根由脚本自身位置推导（clients/cli/../..），可用环境变量 MEMORY_HOME 覆盖。

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MEM_ROOT=${MEMORY_HOME:-$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)}
PYTHON=${PYTHON:-python3}

CMD=${1:-}
if [ -z "$CMD" ]; then
  cat <<'EOF'
Usage:
  mem search <keywords>      - search memory
  mem get <id>               - get knowledge detail
  mem rebuild                - rebuild index from md
  mem anchor/distill/confirm - distill session
EOF
  exit 1
fi

case "$CMD" in
  anchor|distill|confirm)
    exec "$PYTHON" "$MEM_ROOT/src/micro_mem/cli/distill.py" "$@"
    ;;
  *)
    exec "$PYTHON" "$MEM_ROOT/src/micro_mem/cli/main.py" "$@"
    ;;
esac
