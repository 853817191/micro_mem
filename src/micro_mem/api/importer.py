"""业务存量导入接口 Importer：扫描历史 JSONL 会话 → 生成锚点。

存量与增量共用同一管道：存量 = 批量扫描生成锚点（后续蒸馏由 AI 流程处理）。
"""
import glob
import json
import os
from dataclasses import dataclass, field

from ..common.config import Config
from ..store.base import NetworkStore


@dataclass
class ImportResult:
    """存量导入结果统计。"""
    anchors_created: int = 0          # 生成的锚点数
    knowledge_created: int = 0        # 生成的知识数（当前锚点导入阶段为 0）
    errors: list[str] = field(default_factory=list)


class Importer:
    """存量导入：扫描 Claude JSONL 会话 → 提取对话 → save_anchor 生成锚点。"""

    def __init__(self, config: Config, store: NetworkStore, writer):
        """依赖注入：配置 + 引擎 + 写接口。"""
        self.config = config
        self.store = store
        self.writer = writer

    def import_history(self, source_dir: str,
                       time_range: tuple | None = None,
                       mode: str = "backfill") -> ImportResult:
        """扫描 source_dir 下的 .jsonl 会话，每个生成一个锚点。

        - source_dir: 历史会话目录（如 ~/.claude/projects/）
        - time_range: (start, end) 可选时间过滤（当前按文件路径处理，暂简单）
        - mode: backfill（全量）/ incremental（增量）——当前同处理
        """
        result = ImportResult()
        for path in self._scan_jsonl(source_dir):
            try:
                text = self._parse_jsonl(path)
                if text:
                    self.writer.save_anchor(
                        text, title=os.path.basename(path), source=os.path.abspath(path))
                    result.anchors_created += 1
            except Exception as e:  # 单文件失败不影响整体导入
                result.errors.append(f"{os.path.basename(path)}: {e}")
        return result

    # ================= 私有工具 =================

    def _scan_jsonl(self, source_dir: str):
        """递归扫描目录下的 .jsonl 文件。"""
        pattern = os.path.join(source_dir, "**", "*.jsonl")
        return sorted(glob.glob(pattern, recursive=True))

    def _parse_jsonl(self, path: str) -> str:
        """解析 Claude JSONL：提取可读对话（含工具过程），按轮次拼接。"""
        turns = self._parse_turns(path)
        return "\n".join(f"── turn {i} ──\n{t}" for i, t in turns)

    def _parse_turns(self, path: str) -> list[tuple[int, str]]:
        """切分轮次：每个真实 user 发言起一个新轮次（含其后的 assistant/tool 过程）。

        返回 [(轮次号从 1 开始, 该轮次文本), ...]。
        tool_result（工具结果）消息不算新轮次，并入其所属轮次。
        """
        tool_names: dict[str, str] = {}   # tool_use_id -> 工具名（给 tool_result 标注来源）
        turns: list[tuple[int, str]] = []
        current: list[str] = []
        turn_no = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = item.get("message") or {}
                role = msg.get("role")
                blocks = msg.get("content")
                # 预扫描 tool_use，登记 id → 工具名
                if isinstance(blocks, list):
                    for b in blocks:
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tid = b.get("id")
                            if isinstance(tid, str):
                                tool_names[tid] = b.get("name") or "tool"
                # 新的 user 发言轮次（有文本的用户消息，而非 tool_result）
                if role == "user" and self._has_text_block(blocks):
                    if current:
                        turns.append((turn_no, "\n".join(current)))
                    turn_no += 1
                    current = []
                rendered = self._render_message(role, blocks, tool_names)
                if rendered:
                    current.append(rendered)
        if current:
            turns.append((turn_no, "\n".join(current)))
        return turns

    @staticmethod
    def _has_text_block(blocks) -> bool:
        """content 是否含用户文本（字符串或 text 块），用于区分真实发言与 tool_result。"""
        if isinstance(blocks, str):
            return bool(blocks.strip())
        if isinstance(blocks, list):
            return any(isinstance(b, dict) and b.get("type") == "text"
                       and str(b.get("text") or "").strip() for b in blocks)
        return False

    def _render_message(self, role, blocks, tool_names: dict) -> str:
        """渲染单条消息为可读文本（user/assistant 文本 + 工具调用与结果）。"""
        if isinstance(blocks, str):
            if role in ("user", "assistant") and blocks.strip():
                return f"## {role}\n{blocks}"
            return ""
        if not isinstance(blocks, list):
            return ""
        lines = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                txt = str(b.get("text") or "").strip()
                if txt:
                    lines.append(f"## {role}\n{txt}")
            elif btype == "tool_use":
                name = b.get("name") or "tool"
                inp = b.get("input")
                try:
                    inp_s = json.dumps(inp, ensure_ascii=False) if inp is not None else ""
                except (TypeError, ValueError):
                    inp_s = str(inp)
                lines.append(f"## tool_use: {name}\n{inp_s}")
            elif btype == "tool_result":
                content = self._tool_result_text(b.get("content"))
                tool_label = tool_names.get(b.get("tool_use_id"), "tool")
                if content:
                    lines.append(f"## tool_result ({tool_label})\n{content}")
        return "\n".join(lines)

    @staticmethod
    def _tool_result_text(content) -> str:
        """提取 tool_result 输出文本（可能是字符串或 text 块列表）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, dict) and c.get("type") == "text":
                    parts.append(str(c.get("text") or ""))
            return "\n".join(p for p in parts if p)
        return ""
