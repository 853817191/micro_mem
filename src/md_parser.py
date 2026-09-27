"""md 文件 frontmatter 解析工具：知识/锚点 md 的序列化与反序列化。

用 pyyaml 解析 frontmatter（可靠处理 sources/external_refs 等嵌套结构）。
"""
import yaml


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 md 文本：返回 (meta: dict, body: str)。

    - 无 frontmatter 时返回 ({}, 全文)
    - frontmatter 用 yaml 解析（支持嵌套列表）
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body


def parse_file(path: str) -> tuple[dict, str]:
    """读取并解析 md 文件：返回 (meta, body)。"""
    with open(path, encoding="utf-8") as f:
        return parse_frontmatter(f.read())
