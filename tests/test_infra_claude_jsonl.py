"""claude_jsonl 反腐层测试：Claude 会话 JSONL → 领域对话文本。"""
import json

from micro_mem.infrastructure.claude_jsonl import parse_session, parse_turns, scan_jsonl


def write_jsonl(path, items) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def user_text(text):
    return {"message": {"role": "user", "content": text}}


def assistant_blocks(*blocks):
    return {"message": {"role": "assistant", "content": list(blocks)}}


def tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, content):
    return {"type": "tool_result", "tool_use_id": tid, "content": content}


def user_tool_result(tid, content):
    """tool_result 以 user 角色回传（不算新轮次）。"""
    return {"message": {"role": "user", "content": [tool_result(tid, content)]}}


def test_turns_split_on_real_user_text(tmp_path):
    """每个真实 user 发言起新轮次；tool_result 并入所属轮次。"""
    path = tmp_path / "s.jsonl"
    write_jsonl(path, [
        user_text("第一个问题"),
        assistant_blocks({"type": "text", "text": "第一个回答"}),
        user_text("第二个问题"),
        assistant_blocks(tool_use("t1", "Bash", {"command": "ls"})),
        user_tool_result("t1", "file1.txt"),
        assistant_blocks({"type": "text", "text": "第二个回答"}),
    ])
    turns = parse_turns(str(path))
    assert [t for t, _ in turns] == [1, 2]
    assert "第一个问题" in turns[0][1] and "第一个回答" in turns[0][1]
    turn2 = turns[1][1]
    assert "第二个问题" in turn2
    assert "## tool_use: Bash" in turn2
    assert "## tool_result (Bash)" in turn2 and "file1.txt" in turn2


def test_session_format_wraps_turn_markers(tmp_path):
    path = tmp_path / "s.jsonl"
    write_jsonl(path, [user_text("你好"), assistant_blocks({"type": "text", "text": "你好！！"})])
    text = parse_session(str(path))
    assert text.startswith("── turn 1 ──")
    assert "## user" in text and "## assistant" in text


def test_malformed_lines_skipped(tmp_path):
    """坏行静默跳过（真实 jsonl 常有元数据行/截断行）。"""
    path = tmp_path / "s.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"message": {"role": "user", "content": "问题"}}\n')
        f.write("not json at all\n")
        f.write('{"type": "meta"}\n')
        f.write('{"message": {"role": "assistant", "content": "回答"}}\n')
    turns = parse_turns(str(path))
    assert len(turns) == 1
    assert "问题" in turns[0][1] and "回答" in turns[0][1]


def test_scan_jsonl_recursive_sorted(tmp_path):
    sub = tmp_path / "proj" / "nested"
    sub.mkdir(parents=True)
    write_jsonl(sub / "b.jsonl", [user_text("B")])
    write_jsonl(tmp_path / "proj" / "a.jsonl", [user_text("A")])
    (tmp_path / "proj" / "ignore.txt").write_text("x", encoding="utf-8")
    found = scan_jsonl(str(tmp_path))
    assert len(found) == 2
    assert found == sorted(found)
    assert all(p.endswith(".jsonl") for p in found)
