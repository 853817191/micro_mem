"""锚点格式（domain/anchor_format.py）：turn 序列 ↔ 正文的唯一权威转换。"""
from micro_mem.domain.anchor_format import (
    parse_turns,
    serialize_turns,
    wrap_turn,
)


def test_roundtrip_multi_turns():
    turns = [(1, "## user\n甲"), (2, "## user\n乙"), (3, "## assistant\n丙")]
    assert parse_turns(serialize_turns(turns)) == turns


def test_parse_strips_blank_edges_between_turns():
    text = "── turn 1 ──\n\n## user\n甲\n\n\n── turn 2 ──\n\n## user\n乙\n\n"
    assert parse_turns(text) == [(1, "## user\n甲"), (2, "## user\n乙")]


def test_lead_text_merged_into_first_turn():
    """分隔符前的散文本（无 frontmatter 场景）并入第 1 轮，不丢内容。"""
    text = "散文本\n── turn 1 ──\n## user\n甲"
    assert parse_turns(text) == [(1, "散文本\n## user\n甲")]


def test_no_separator_is_single_turn():
    assert parse_turns("整段描述无结构") == [(1, "整段描述无结构")]


def test_empty_is_zero_turns():
    assert parse_turns("") == []
    assert parse_turns("   \n  ") == []


def test_wrap_turn_adds_role_header():
    assert wrap_turn("正文") == "## user\n正文"


def test_wrap_turn_keeps_existing_header():
    assert wrap_turn("## assistant\n正文") == "## assistant\n正文"
