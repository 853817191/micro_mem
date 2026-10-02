"""Claude 会话 JSONL → turn 序列（存量会话通道）。

渲染逻辑（含工具过程保真）在 claude_jsonl 反腐层；本实现只做端口适配：
supports .jsonl，parse_turns 委托 claude_jsonl.parse_turns。
"""
from .. import claude_jsonl
from ...application.ports import SourceParser
from ...domain.anchor_format import wrap_turn


class JsonlParser(SourceParser):
    """存量 Claude 会话 jsonl：按 user 发言切轮（含其后 assistant/tool 过程）。"""

    def supports(self, source: str) -> bool:
        return source.lower().endswith(".jsonl")

    def parse_turns(self, source: str) -> list[tuple[int, str]]:
        try:
            turns = claude_jsonl.parse_turns(source)
        except OSError as e:
            raise ValueError(f"jsonl 会话文件不可读: {source}（{e}）") from e
        return [(n, wrap_turn(t)) for n, t in turns]
