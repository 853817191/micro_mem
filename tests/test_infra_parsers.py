"""SourceParser 各实现：五种输入通道 → 统一 turn 序列。"""
import json

from micro_mem.infrastructure.parsers import (
    HtmlParser,
    JsonlParser,
    MarkdownParser,
    default_parsers,
)
from micro_mem.infrastructure.parsers.html_parser import parse_html_text
from micro_mem.infrastructure.parsers.url_parser import UrlParser


# ================= Markdown =================

def test_markdown_splits_on_headings(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# 需求单概述\n甲内容\n\n## 报价流程\n乙内容\n\n## 选择方案\n丙内容",
                 encoding="utf-8")
    turns = MarkdownParser().parse_turns(str(f))
    assert [n for n, _ in turns] == [1, 2, 3]
    assert "# 需求单概述" in turns[0][1] and "甲内容" in turns[0][1]
    assert turns[0][1].startswith("## user")          # 文档类整轮标 user


def test_markdown_without_heading_is_single_turn(tmp_path):
    f = tmp_path / "plain.md"
    f.write_text("没有标题的整篇文档", encoding="utf-8")
    turns = MarkdownParser().parse_turns(str(f))
    assert turns == [(1, "## user\n没有标题的整篇文档")]


# ================= HTML =================

def test_html_strips_tags_and_scripts():
    turns = parse_html_text(
        "<html><head><style>body{}</style></head><body>"
        "<h1>标题甲</h1><p>段落<b>加粗</b>内容</p>"
        "<script>var x=1;</script>"
        "<h2>标题乙</h2><div>第二段</div></body></html>")
    assert len(turns) == 2
    assert "标题甲" in turns[0][1] and "段落加粗内容" in turns[0][1]
    assert "var x" not in turns[0][1]                 # script 被跳过
    assert "body{}" not in turns[0][1]                # style 被跳过
    assert "标题乙" in turns[1][1]


def test_html_without_headings_is_single_turn():
    turns = parse_html_text("<p>只有一个段落</p>")
    assert len(turns) == 1


def test_html_parser_supports_file_suffix():
    assert HtmlParser().supports("a.htm")
    assert HtmlParser().supports("A.HTML")
    assert not HtmlParser().supports("a.md")


# ================= JSONL（存量会话，回归） =================

def test_jsonl_parser_matches_legacy(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(
        json.dumps({"message": {"role": "user", "content": f"第{i}问"}},
                   ensure_ascii=False) for i in range(1, 3)), encoding="utf-8")
    turns = JsonlParser().parse_turns(str(f))
    assert [n for n, _ in turns] == [1, 2]
    assert "第1问" in turns[0][1]


# ================= 注册表探测顺序 =================

def test_registry_dispatch(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# t\nx", encoding="utf-8")
    jsonl = tmp_path / "a.jsonl"
    jsonl.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                                ensure_ascii=False), encoding="utf-8")
    parsers = default_parsers()
    assert type(_pick(parsers, str(md))).__name__ == "MarkdownParser"
    assert type(_pick(parsers, str(jsonl))).__name__ == "JsonlParser"
    assert type(_pick(parsers, "https://example.com/x")).__name__ == "UrlParser"
    assert _pick(parsers, str(tmp_path / "a.xyz")) is None


def _pick(parsers, source):
    for p in parsers:
        if p.supports(source):
            return p
    return None
