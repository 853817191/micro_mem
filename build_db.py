"""建库脚本：创建 data 目录 + 执行 schema.sql 生成 memory.db。

用法：python build_db.py
幂等：schema.sql 全部 IF NOT EXISTS，可重复执行。
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))

# 需要创建的 data 子目录
DATA_DIRS = [
    "data",
    "data/anchors",      # 锚点：对话记录（保真）
    "data/knowledge",    # 知识：md 真值
    "data/db",           # SQLite 索引
    "data/temp",         # 临时过程产物
]


def ensure_dirs() -> None:
    """创建 data 目录结构（已存在则跳过）。"""
    for d in DATA_DIRS:
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)


def build() -> None:
    """执行 schema.sql 生成 memory.db 并列出建成的表。"""
    ensure_dirs()
    db_path = os.path.join(ROOT, "data", "db", "memory.db")
    with open(os.path.join(ROOT, "schema.sql"), encoding="utf-8") as f:
        schema = f.read()

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema)
        conn.commit()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table','virtual table') ORDER BY name")]
        print("建库成功:", db_path)
        print("表:", tables)
    finally:
        conn.close()


if __name__ == "__main__":
    build()
