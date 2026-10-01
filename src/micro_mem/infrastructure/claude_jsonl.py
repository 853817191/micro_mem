"""Claude 会话 JSONL → 领域对话文本（反腐层）。

只负责格式解析，不碰存储/配置/业务编排（编排归 ImportService，阶段3）。
输出与旧 Importer 完全一致的文本格式：轮次以 "── turn N ──" 分隔，
消息渲染为 "## <role>" / "## tool_use: <name>" / "## tool_result (<name>)" 段落。
"""
import glob
import json
import os


def scan_jsonl(source_dir: str) -> list[str]:
    """递归扫描目录下的 .jsonl 文件（按路径排序，保证确定性）。"""
    pattern = os.path.join(source_dir, "**", "*.jsonl")
    return sorted(glob.glob(pattern, recursive=True))


def parse_session(path: str) -> str:
    """解析 Claude JSONL 会话为可读对话文本（含工具过程），按轮次拼接。"""
    turns = parse_turns(path)
    return "\n".join(f"── turn {i} ──\n{t}" for i, t in turns)


def parse_turns(path: str) -> list[tuple[int, str]]:
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
            if role == "user" and _has_text_block(blocks):
                if current:
                    turns.append((turn_no, "\n".join(current)))
                turn_no += 1
                current = []
            rendered = _render_message(role, blocks, tool_names)
            if rendered:
                current.append(rendered)
    if current:
        turns.append((turn_no, "\n".join(current)))
    return turns


def _has_text_block(blocks) -> bool:
    """content 是否含用户文本（字符串或 text 块），用于区分真实发言与 tool_result。"""
    if isinstance(blocks, str):
        return bool(blocks.strip())
    if isinstance(blocks, list):
        return any(isinstance(b, dict) and b.get("type") == "text"
                   and str(b.get("text") or "").strip() for b in blocks)
    return False


def _render_message(role, blocks, tool_names: dict) -> str:
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
            content = _tool_result_text(b.get("content"))
            tool_label = tool_names.get(b.get("tool_use_id"), "tool")
            if content:
                lines.append(f"## tool_result ({tool_label})\n{content}")
    return "\n".join(lines)


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
