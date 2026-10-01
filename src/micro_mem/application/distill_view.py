"""R1 定位素材的视图层：预算自适应压缩 + 预检索查询提取。

原则（拍板，设计文档 4.1）：正确性优先——增量内容不超预算就**原文全量**输出；
只有超预算（首次建树的大会话）才逐段压缩，且按"噪声优先"分档：
tool_result 剥离（领域知识的噪声主力）→ assistant 长文截断 → tool_use 入参截断；
user 发言永不压缩（定位的语义核心）。

轮次文本结构由 claude_jsonl 反腐层渲染："## user" / "## assistant" /
"## tool_use: <name>" / "## tool_result (<name>)" 段落。本模块只做视图变换，
不碰解析与保真（解析归反腐层，保真归档归锚点）。
"""

# R1 视图预算参数的兜底默认（生产路径由 config.distill 节覆盖注入，
# 两处默认值保持一致——改这里要同步 infrastructure/config.py 的 DEFAULT_CONFIG）
DEFAULTS: dict[str, int] = {
    "context_budget_chars": 100000,   # 增量总字符预算：超了才触发压缩
    "assistant_chars": 300,           # 压缩时 assistant 段保留字符数
    "tool_input_chars": 100,          # 压缩时 tool_use 入参保留字符数
    "preretrieve_hits": 10,           # 预检索关键词召回的命中上限
    "subtree_max": 30,                # 预检索子树（含祖先链/直接子节点）总量上限
}

# 预检索查询素材的字符上限（取增量里 user 发言的拼接，关键词密度最高）
QUERY_CHARS = 500


def render_view(turns: list[tuple[int, str]], budget: int,
                assistant_chars: int,
                tool_input_chars: int) -> tuple[list[tuple[int, str]], bool]:
    """增量轮次 → R1 视图。返回 (视图轮次, 是否触发了压缩)。

    总字符 ≤ budget：原样返回（全量优先，定位正确性不受损）；
    > budget：逐轮按段落类型压缩（见模块 docstring 的分档）。
    """
    if sum(len(t) for _, t in turns) <= budget:
        return list(turns), False
    return [(i, _compress_turn(t, assistant_chars, tool_input_chars))
            for i, t in turns], True


def extract_user_text(turns: list[tuple[int, str]], max_chars: int = QUERY_CHARS) -> str:
    """提取增量轮次里的 user 段落拼接（预检索的查询素材）。"""
    parts = [body for _i, text in turns
             for header, body in _split_segments(text) if header == "## user"]
    return "\n".join(parts)[:max_chars]


# ---------------- 私有 ----------------


def _compress_turn(text: str, assistant_chars: int, tool_input_chars: int) -> str:
    """单轮压缩：按段落类型分档（user 与无法识别的内容不压，防丢信息）。"""
    out = []
    for header, body in _split_segments(text):
        if header.startswith("## tool_result"):
            out.append(f"{header}\n[已剥离 {len(body)} 字符]")
        elif header.startswith("## assistant"):
            out.append(f"{header}\n{_clip(body, assistant_chars)}")
        elif header.startswith("## tool_use"):
            out.append(f"{header}\n{_clip(body, tool_input_chars)}")
        else:
            out.append(f"{header}\n{body}" if header else body)
    return "\n".join(out)


def _split_segments(text: str) -> list[tuple[str, str]]:
    """按 '## ' 段头切分轮次文本为 [(段头, 段体)]；无段头的内容兜底保留。"""
    segments: list[tuple[str, str]] = []
    header = ""
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            if header or body:
                segments.append((header, "\n".join(body)))
            header, body = line, []
        else:
            body.append(line)
    if header or body:
        segments.append((header, "\n".join(body)))
    return segments


def _clip(text: str, limit: int) -> str:
    """截断到 limit 字符（超出补省略标记）。"""
    return text if len(text) <= limit else text[:limit] + " …[截断]"
