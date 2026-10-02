"""SourceParser 实现集合：各输入格式 → 统一 turn 序列。

注册顺序即探测顺序（先命中先用）：URL → Markdown → HTML → Claude JSONL。
所有实现只产 turn 序列，格式拼接归 anchor_format（唯一写方）。
"""
from ...domain.anchor_format import ROLE_USER
from .html_parser import HtmlParser
from .jsonl_parser import JsonlParser
from .markdown_parser import MarkdownParser
from .url_parser import UrlParser


def default_parsers() -> list:
    """生产注册表：有序，anchor 命令按 supports() 依次探测。"""
    return [UrlParser(), MarkdownParser(), HtmlParser(), JsonlParser()]


__all__ = ["HtmlParser", "JsonlParser", "MarkdownParser", "UrlParser",
           "ROLE_USER", "default_parsers"]
