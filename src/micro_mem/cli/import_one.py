"""导入单个历史会话：解析 JSONL → 生成锚点 → 输出对话内容供 AI 提炼候选。

用法：python src/micro_mem/cli/import_one.py <jsonl路径>
"""
import os
import sys

# Windows 下强制 UTF-8 输出，避免管道/控制台中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, SRC)

from micro_mem.container import build_components  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("用法: python src/micro_mem/cli/import_one.py <jsonl路径>")
        return
    path = sys.argv[1]

    ctx = build_components()
    writer = ctx.writer
    importer = ctx.importer

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
