"""蒸馏当前 session：真实锚点 + 增量蒸馏（统一链路 = jsonl + 完整过程）。

用法：
    python src/micro_mem/cli/distill.py anchor [jsonl_path] [标题]
        创建锚点：解析 jsonl（含工具过程，全量保真）→ 锚点 md。
        缺 jsonl 时取 ~/.claude/projects 最新会话。
    python src/micro_mem/cli/distill.py distill [anchor_id]
        增量蒸馏准备：重同步锚点（含新增轮次）→ 输出自上次蒸馏以来的增量轮次 + 整体锚点上下文，
        并把 {anchor_id, processed_until} 写入 data/temp/distill_state.json（供 confirm 回写游标）。
    python src/micro_mem/cli/distill.py confirm [candidates.json]
        落库：幂等去重 + 溯源校验（sources.ref 必须指向真实锚点）→ 成功后回写锚点 distilled_until。
"""
import glob
import json
import os
import sys

# Windows 下强制 UTF-8 输出，避免管道/控制台中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from micro_mem.api.attach import auto_attach_all
from micro_mem.common.config import Config
from micro_mem.container import build_components
from micro_mem.domain.types import (
    Decision,
    DistillCandidate,
    KnowledgeType,
    Scope,
    Source,
    SourceType,
)


def _find_newest_session() -> str:
    """取 ~/.claude/projects 下最新修改的 jsonl 会话文件。"""
    root = os.path.expanduser("~/.claude/projects")
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    return max(files, key=os.path.getmtime) if files else ""


def _latest_anchor() -> str:
    """取最近修改的锚点 id。"""
    anchors = glob.glob(os.path.join(Config().anchors_dir(), "s-*.md"))
    return os.path.basename(max(anchors, key=os.path.getmtime))[:-3] if anchors else ""


def cmd_anchor(argv) -> str:
    """创建锚点：jsonl（含工具过程）→ 保真锚点。"""
    ctx = build_components()
    writer = ctx.writer
    importer = ctx.importer
    path = argv[2] if len(argv) > 2 else _find_newest_session()
    if not path or not os.path.exists(path):
        print("未找到 jsonl 会话文件，请传入路径: "
              "python src/micro_mem/cli/distill.py anchor <jsonl_path>")
        return ""
    title = argv[3] if len(argv) > 3 else os.path.basename(path)
    text = importer._parse_jsonl(path)
    if not text:
        print("该会话无可提取的对话文本")
        return ""
    anchor_id = writer.save_anchor(text, title=title, source=os.path.abspath(path))
    print(f"锚点已保存: {anchor_id} → data/anchors/{anchor_id}.md")
    print(f"会话长度: {len(text)} 字符 | 轮次数: {len(importer._parse_turns(path))}")
    return anchor_id


def cmd_distill(argv) -> None:
    """增量蒸馏准备：重同步锚点 + 输出增量轮次与整体上下文。"""
    ctx = build_components()
    config = ctx.config
    writer = ctx.writer
    importer = ctx.importer
    anchor_id = argv[2] if len(argv) > 2 else _latest_anchor()
    if not anchor_id:
        print("无锚点，请先运行 anchor")
        return
    source = writer.get_anchor_source(anchor_id)
    if not source or not os.path.exists(source):
        print(f"锚点 {anchor_id} 无有效 jsonl 源（{source or '空'}），无法做增量蒸馏")
        return
    # 1. 重同步锚点：整段会话（含新增轮次）保真全文
    text = importer._parse_jsonl(source)
    turns = importer._parse_turns(source)
    writer.resync_anchor(anchor_id, text)
    # 2. 增量范围
    distilled_until = writer.get_anchor_distilled_until(anchor_id)
    delta = [(i, t) for i, t in turns if i > distilled_until]
    processed_until = max((i for i, _ in delta), default=distilled_until)
    # 3. 写蒸馏状态（confirm 据此回写游标）
    state = {"anchor_id": anchor_id, "processed_until": processed_until}
    os.makedirs(config.temp_dir(), exist_ok=True)
    state_path = os.path.join(config.temp_dir(), "distill_state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    # 4. 输出供 AI 提炼
    first_delta = delta[0][0] if delta else distilled_until
    print(f"锚点: {anchor_id} | 已蒸馏到 turn {distilled_until} | "
          f"本轮增量 turn {first_delta}..{processed_until}")
    print(f"状态文件: {state_path}")
    print("=" * 70)
    print(f"【增量轮次】（据此提炼候选；sources.ref 填 {anchor_id}）:")
    for i, t in delta:
        print(f"── turn {i} ──\n{t}")
    print("=" * 70)
    print("【整体锚点上下文】（供整体视角，勿重复提炼已蒸馏部分，前 8000 字符）：")
    print(text[:8000])


def cmd_confirm(argv) -> None:
    """落库：幂等去重 + 溯源校验 + 回写蒸馏游标。"""
    ctx = build_components()
    config = ctx.config
    writer = ctx.writer
    reader = ctx.reader
    distiller = ctx.distiller
    cand_path = argv[2] if len(argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "candidates.json")
    with open(cand_path, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        print("candidates 为空")
        return

    def build(r, main_id=None):
        """构造候选；suggested 里的 MAIN 占位替换为主事件 id。"""
        parents = [main_id if p == "MAIN" else p for p in r.get("suggested_parents", [])]
        links = [main_id if link_id == "MAIN" else link_id
                 for link_id in r.get("suggested_links", [])]
        decision = {"keep": Decision.KEEP, "edit": Decision.EDIT,
                    "reject": Decision.REJECT}.get(r.get("decision", "keep"), Decision.KEEP)
        return DistillCandidate(
            type=KnowledgeType(r["type"]), scope=Scope(r["scope"]),
            title=r["title"], summary=r.get("summary", ""), body=r.get("body", ""),
            suggested_parents=parents, suggested_links=links,
            sources=[Source(SourceType(s["type"]), s.get("ref", "")) for s in r.get("sources", [])],
            decision=decision, edit_id=r.get("edit_id", ""))

    # 第一遍：主事件（第一条）先落库；其余候选的 MAIN 占位 → main_id
    try:
        main_ids = distiller.confirm_distill([build(raw[0])])
        main_id = main_ids[0] if main_ids else None
        rest_ids = distiller.confirm_distill([build(r, main_id) for r in raw[1:]])
    except Exception as e:
        print(f"落库失败（未回写蒸馏游标）: {e}")
        return
    all_ids = main_ids + rest_ids
    print(f"落库 {len(all_ids)} 条: {all_ids}")

    # 回写蒸馏游标（存在 distill_state.json 时）
    state_path = os.path.join(config.temp_dir(), "distill_state.json")
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        anchor_id = state.get("anchor_id", "")
        until = int(state.get("processed_until", -1))
        if anchor_id and writer.mark_distilled(anchor_id, until):
            print(f"已更新锚点蒸馏游标: {anchor_id}.distilled_until = {until}")
        os.remove(state_path)

    # 自动挂靠：落库后对新知识尝试挂靠到相关主题节点（同类知识聚合）
    attached = auto_attach_all(writer, reader, all_ids)
    if attached:
        print("自动挂靠:", {k: v for k, v in attached.items()})
    else:
        print("自动挂靠: 无匹配主题")

    # 验证
    print("\n[验证] search('记忆系统'):")
    for h in reader.search("记忆系统"):
        print(f"  [{h.id}] {h.title}")
    if main_id:
        print(f"[验证] traverse('{main_id}', 2)（主事件 → 方法论/架构知识）:")
        for to_id, etype, d in reader.traverse(main_id, depth=2):
            print(f"  {main_id} -[{etype}]→ {to_id}（跳数 {d}）")


def main() -> None:
    """入口：anchor / distill / confirm 三段分发。"""
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "anchor":
        cmd_anchor(sys.argv)
    elif cmd == "distill":
        cmd_distill(sys.argv)
    elif cmd == "confirm":
        cmd_confirm(sys.argv)
    else:
        print("用法: mem-distill anchor|distill|confirm")


if __name__ == "__main__":
    main()
