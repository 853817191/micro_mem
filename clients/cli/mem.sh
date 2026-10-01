#!/usr/bin/env sh
# micro_mem 命令包装器（macOS / Linux）：一行转发到统一入口 python -m micro_mem
# 仓库根由脚本自身位置推导（clients/cli/../..），可用环境变量 MEMORY_HOME 覆盖。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MEM_ROOT=${MEMORY_HOME:-$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)}
# 解释器探测：实际执行 --version 判活（Windows 的 python3 微软商店 stub「存在但跑不起来」，会被自动跳过）
if [ -z "$PYTHON" ]; then
  if python3 --version >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi

export PYTHONPATH="$MEM_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m micro_mem "$@"
