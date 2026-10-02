"""锚点格式：turn 序列 ↔ 锚点正文的唯一权威转换（S0 的标准输出格式）。

协议全景：蒸馏全链路（S2 供给切轮 / S3 轮次标注 / S6 R3 回读定位）消费的唯一
素材格式是锚点正文——`── turn N ──` 分隔、轮内 role 段落（## user / ## assistant
/ ## tool_use / ## tool_result）。本模块是格式的唯一写方（serialize）与唯一读方
（parse）：所有 SourceParser 只产 turn 序列，不再各自拼格式；供给/回读只调 parse，
不再各自切轮。格式条款：

1. 轮次分隔符：`── turn N ──`（行首整行；N 从 1 连续，作为展示标签被解析还原）
2. 轮内文本：role 段落头（`## <role>`）+ 内容；文档类输入整轮标 `## user`
3. 段落/轮次间空行由序列化统一产生；解析对空白容错（strip）
4. 无分隔符的散文本 = 单轮素材（parse 返回 [(1, 全文)]）
5. 空内容 = 零轮（无效素材，调用方拒收）
"""
import re

_TURN_RE = re.compile(r"(?m)^── turn (\d+) ──[ \t]*$")

ROLE_USER = "user"  # 文档类输入（md/html/url/text）无角色概念，统一标 user 保持下游渲染兼容


def wrap_turn(text: str) -> str:
    """轮内文本统一加 role 段落头（已有段落头则不重复加）。"""
    stripped = text.strip()
    if stripped.startswith("## "):
        return stripped
    return f"## {ROLE_USER}\n{stripped}"


def serialize_turns(turns: list[tuple[int, str]]) -> str:
    """turn 序列 → 锚点正文（唯一写方）。turn 号只作展示标签，原文照排。"""
    return "\n".join(f"── turn {n} ──\n{t}" for n, t in turns)


def parse_turns(text: str) -> list[tuple[int, str]]:
    """锚点正文 → turn 序列（唯一读方）。

    分隔符外的散文本（首个分隔符之前）并入第 1 轮，不丢内容；
    无分隔符 → 整文单轮；空文 → 零轮。
    """
    matches = list(_TURN_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [(1, stripped)] if stripped else []
    turns: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip("\n").strip()
        if i == 0 and text[:m.start()].strip():
            lead = text[:m.start()].strip()
            body = (lead + "\n" + body).strip() if body else lead
        turns.append((int(m.group(1)), body))
    return turns
