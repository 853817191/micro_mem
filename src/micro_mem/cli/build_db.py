"""建库脚本：创建 data 目录 + 复用引擎建表生成 memory.db。

用法：python src/micro_mem/cli/build_db.py
幂等：schema.sql 全部 IF NOT EXISTS，可重复执行。
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from micro_mem.common.config import Config  # noqa: E402
from micro_mem.store.sqlite_store import SqliteNetworkStore  # noqa: E402


def ensure_dirs(config: Config) -> None:
    """创建 data 目录结构（已存在则跳过）。"""
    for d in (config.data_dir, config.anchors_dir(), config.knowledge_dir(),
              os.path.join(config.data_dir, "db"), config.temp_dir()):
        os.makedirs(d, exist_ok=True)


def build() -> None:
    """复用引擎建表：SqliteNetworkStore 首次连接自动执行 schema.sql（幂等）。"""
    config = Config()
    ensure_dirs(config)
    SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)

    # 列出建成的表（独立只读连接，仅用于展示，不重复建表逻辑）
    conn = sqlite3.connect(config.db_path())
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table','virtual table') ORDER BY name")]
        print("建库成功:", config.db_path())
        print("表:", tables)
    finally:
        conn.close()


if __name__ == "__main__":
    build()
