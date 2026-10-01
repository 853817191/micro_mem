"""pytest 公共引导：src-layout 导入路径 + Windows GBK 控制台 UTF-8 重配置。

旧栈的 fixtures（config/store/writer/reader/distiller/importer）随 api/common/store
目录一并拆除——新测试用 micro_mem.composition.assemble_inmemory / assemble 自行装配。
"""
import sys
from pathlib import Path

# 确保 src-layout 下 micro_mem 可导入（不依赖 pip install -e）
SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Windows GBK 控制台下中文/✓ 会崩，统一 UTF-8（与 CLI 入口一致）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
