"""HTTP(S) URL → turn 序列（网页通道）：抓取 → 按 HTML 解析。

标准库 urllib（UA 伪装 + 10s 超时），抓取失败抛 ValueError 带 URL 上下文。
"""
import urllib.request

from ...application.ports import SourceParser
from .html_parser import parse_html_text

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class UrlParser(SourceParser):
    """http(s) 地址：抓取网页内容走 HTML 切轮。"""

    def supports(self, source: str) -> bool:
        return source.startswith(("http://", "https://"))

    def parse_turns(self, source: str) -> list[tuple[int, str]]:
        req = urllib.request.Request(source, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
        except Exception as e:
            raise ValueError(f"URL 抓取失败: {source}（{e}）") from e
        return parse_html_text(raw.decode(charset, errors="replace"))
