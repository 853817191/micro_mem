#!/usr/bin/env sh
# micro_mem 命令包装器（macOS / Linux）：一行转发到统一入口 python -m micro_mem
# 仓库根由脚本自身位置推导（clients/cli/../..），可用环境变量 MEMORY_HOME 覆盖。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MEM_ROOT=${MEMORY_HOME:-$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)}
PYTHON=${PYTHON:-python3}

export PYTHONPATH="$MEM_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m micro_mem "$@"
