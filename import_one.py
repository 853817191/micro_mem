"""导入单个历史会话：解析 JSONL → 生成锚点 → 输出对话内容供 AI 提炼候选。

用法：python import_one.py <jsonl路径>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.importer import Importer
from src.api.writer import MemoryWriter
from src.config import Config
from src.embedder import HashEmbedder
from src.store.sqlite_store import SqliteNetworkStore


def main():
    if len(sys.argv) < 2:
        print("用法: python import_one.py <jsonl路径>")
        return
    path = sys.argv[1]

    config = Config()
    store = SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)
    embedder = HashEmbedder(dim=config.embedding_dim)
    writer = MemoryWriter(config, store, embedder=embedder)
    importer = Importer(config, store, writer)

    text = importer._parse_jsonl(path)          # 解析对话文本
    if not text:
        print("该会话无可提取的对话文本")
        return
    anchor_id = writer.save_anchor(text, title=os.path.basename(path), source=os.path.abspath(path))
    print(f"锚点已生成: {anchor_id}")
    print(f"对话长度: {len(text)} 字符")
    print("=" * 40)
    print("对话内容（前 4000 字符，供提炼候选）：")
    print(text[:4000])


if __name__ == "__main__":
    main()
