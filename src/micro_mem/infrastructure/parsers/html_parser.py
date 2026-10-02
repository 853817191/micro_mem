"""HTML → turn 序列（本地网页另存通道；URL 抓取复用 parse_html_text）。

解析策略：标准库 HTMLParser，无第三方依赖。script/style 整块跳过；
h1/h2/h3 为轮边界（有结构信号就切，轮次粒度对齐蒸馏的 turns 标注）；
块级标签（p/div/li/br 等）视作行边界。纯文本段落折叠空白。
"""
from html.parser import HTMLParser

from ...application.ports import SourceParser
from ...domain.anchor_format import wrap_turn

_SKIP_TAGS = {"script", "style"}
_HEADING_TAGS = {"h1", "h2", "h3"}
_BREAK_TAGS = {"p", "div", "li", "br", "tr", "section", "article", "ul", "ol", "table"}


class _TurnCollector(HTMLParser):
    """收集文本并按标题切轮：self.sections 为 [ [行...], ... ]。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[list[str]] = [[]]
        self._cur: list[str] = []          # 当前段落的行缓冲（块级边界 flush）
        self._skip_depth = 0
        self._heading_open = False         # 标题标签内：块级边界不切新轮

    # ---- 段落缓冲 ----
    def _emit_line(self) -> None:
        line = " ".join("".join(self._cur).split())
        self._cur = []
        if line:
            self.sections[-1].append(line)

    def _new_section(self) -> None:
        self._emit_line()
        if self.sections[-1]:              # 空段不产生新轮（连续标题）
            self.sections.append([])

    # ---- 标签事件 ----
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _HEADING_TAGS:
            self._new_section()
            self._heading_open = True
        elif tag in _BREAK_TAGS:
            self._emit_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _HEADING_TAGS:
            self._heading_open = False
            self._emit_line()
        elif tag in _BREAK_TAGS:
            self._emit_line()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._cur.append(data)

    def close(self) -> None:               # type: ignore[override]
        super().close()
        self._emit_line()


def parse_html_text(text: str) -> list[tuple[int, str]]:
    """HTML 文本 → turn 序列（纯函数，URL 抓取与本地文件共用）。"""
    collector = _TurnCollector()
    collector.feed(text)
    collector.close()
    sections = ["\n".join(lines).strip() for lines in collector.sections]
    return [(i + 1, wrap_turn(s)) for i, s in enumerate(sections) if s]


class HtmlParser(SourceParser):
    """本地 .html/.htm 文件。"""

    def supports(self, source: str) -> bool:
        return source.lower().endswith((".html", ".htm"))

    def parse_turns(self, source: str) -> list[tuple[int, str]]:
        try:
            with open(source, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            raise ValueError(f"html 文件不可读: {source}（{e}）") from e
        return parse_html_text(text)
