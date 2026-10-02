"""Markdown 文件 → turn 序列（文档通道）。

切轮规则：一级/二级标题（# / ##）为轮边界，每个标题段 = 1 turn（标题行保留，
是检索线索）；无标题的 md 整篇 = 1 turn。
"""
import re

from ...application.ports import SourceParser
from ...domain.anchor_format import wrap_turn

_HEADING_RE = re.compile(r"(?m)(?=^#{1,2} )")


class MarkdownParser(SourceParser):
    """md 文档：按 #、## 标题切段。"""

    def supports(self, source: str) -> bool:
        return source.lower().endswith((".md", ".markdown"))

    def parse_turns(self, source: str) -> list[tuple[int, str]]:
        try:
            with open(source, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise ValueError(f"md 文件不可读: {source}（{e}）") from e
        sections = [s.strip() for s in _HEADING_RE.split(text) if s.strip()]
        return [(i + 1, wrap_turn(s)) for i, s in enumerate(sections)]
