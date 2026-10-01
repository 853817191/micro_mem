"""distill_view（R1 视图层）单测：预算自适应压缩 + 预检索查询提取。

拍板口径：预算内原文全量（正确性优先）；超预算才按"噪声优先"分档压缩。
"""
from micro_mem.application.distill_view import (
    DEFAULTS,
    QUERY_CHARS,
    extract_user_text,
    render_view,
)


def test_under_budget_returns_full_text():
    """预算内：原文全量返回，不触发压缩。"""
    turns = [(1, "## user\n短问题"), (2, "## assistant\n" + "长" * 500)]
    view, compressed = render_view(turns, budget=100000,
                                   assistant_chars=300, tool_input_chars=100)
    assert compressed is False
    assert view == turns, "预算内不得改动任何字符"


def test_over_budget_compresses_by_segment_kind():
    """超预算：tool_result 剥离 / assistant 截断 / tool_use 截断 / user 保留。"""
    big_result = "文件内容" * 500
    turn_text = (
        "## user\n这个业务流程怎么走\n"
        f"## assistant\n{'分析' * 500}\n"
        f"## tool_use: Bash\n{'x' * 500}\n"
        f"## tool_result (Bash)\n{big_result}")
    view, compressed = render_view([(1, turn_text)], budget=10,
                                   assistant_chars=300, tool_input_chars=100)
    assert compressed is True
    out = view[0][1]
    assert "这个业务流程怎么走" in out, "user 段永不压缩"
    assert big_result not in out and "[已剥离" in out, "tool_result 剥离为占位"
    assert "分析" * 151 not in out and "…[截断]" in out, "assistant 截到 300"
    assert "x" * 101 not in out, "tool_use 入参截到 100"


def test_budget_boundary_is_inclusive():
    """恰好等于预算：不压缩（边界含等于）。"""
    turns = [(1, "## user\nabc")]
    total = sum(len(t) for _, t in turns)
    _view, compressed = render_view(turns, budget=total,
                                    assistant_chars=1, tool_input_chars=1)
    assert compressed is False


def test_extract_user_text_only_user_segments():
    """预检索查询素材：只提 user 段，跨轮拼接，尊重字符上限。"""
    turns = [(1, "## user\n用户的问题\n## assistant\n回答内容"),
             (2, "## user\n第二个问题")]
    assert extract_user_text(turns) == "用户的问题\n第二个问题"
    assert extract_user_text(turns, max_chars=4) == "用户的问"
    assert extract_user_text([(1, "## assistant\n没有用户发言")]) == ""


def test_defaults_keys_cover_view_config():
    """DEFAULTS 与 config.distill_view 的键集合一致（防配置漂移）。"""
    assert set(DEFAULTS) == {"context_budget_chars", "assistant_chars",
                             "tool_input_chars", "preretrieve_hits", "subtree_max"}
    assert QUERY_CHARS > 0
