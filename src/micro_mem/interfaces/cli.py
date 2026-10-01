"""统一命令入口（D4：单 mem 命令树，12 个子命令）。

    mem search <query> [--multi] [--limit N]   检索（字面 + 语义兜底，RRF 融合）
    mem get <id>                               取完整知识（全文从真值组装）
    mem create --type --scope --title ...      收录一条知识（来源=user_declared）
    mem deprecate <id>                         废弃（留痕不删）
    mem delete <id>                            移出索引工作集（真值保留，D7）
    mem traverse <id> [--depth N]              图遍历
    mem rebuild                                从真值全量重建索引
    mem import --dir <目录> [--mode]           存量导入历史会话 → 锚点
    mem anchor [jsonl] [标题]                  会话 jsonl → 保真锚点（缺省取最新会话）
    mem distill [anchor_id]                    增量蒸馏准备（缺省取最新锚点）
    mem confirm <candidates.json>              候选落库（MAIN 两段式 + 游标回写）
    mem serve [--port 8000]                    可视化 HTTP 服务

本层只做参数解析与打印；全部业务编排走 application 服务。
"""
import argparse
import glob
import json
import os
import sys

from ..composition import Components, assemble
from ..domain.models import DistillCandidate, Knowledge, KnowledgeType, Scope, Source, SourceType

# Windows 下强制 UTF-8 输出，避免管道/控制台中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ================= 检索 / 读取 =================

def cmd_search(args, ctx: Components) -> None:
    """search：双路召回（字面 + 语义 + RRF）；--multi 按空格拆词跨词融合。"""
    if args.multi:
        hits = ctx.search.search_multi(args.query.split(), limit=args.limit)
    else:
        hits = ctx.search.search(args.query, limit=args.limit)
    if not hits:
        print("无命中")
        return
    for h in hits:
        print(f"[{h.id}] {h.title}  ({h.type.value}/{h.scope.value}) score={h.score:.4f}")
        if h.summary:
            print(f"    {h.summary}")


def cmd_get(args, ctx: Components) -> None:
    """get：按 id 取完整知识（body/sources 从真值组装，D3）。"""
    k = ctx.search.get(args.id)
    if k is None:
        print(f"未找到: {args.id}")
        return
    print(f"id: {k.id}   type: {k.type.value}   scope: {k.scope.value}   status: {k.status.value}")
    print(f"title: {k.title}")
    print(f"summary: {k.summary}")
    print(f"parents: {k.parents}   links: {k.links}")
    print(f"external_refs: {[(r.type.value, r.value) for r in k.external_refs]}")
    print(f"sources: {[(s.type.value, s.ref) for s in k.sources]}")
    print(f"body: {(k.body or '')[:300]}")


def cmd_traverse(args, ctx: Components) -> None:
    """traverse：图遍历，从 start_id 沿边双向扩展 N 跳。"""
    results = ctx.search.traverse(args.id, depth=args.depth)
    if not results:
        print(f"{args.id} 无关联节点")
        return
    for to_id, etype, depth in results:
        print(f"  {args.id} -[{etype}]→ {to_id}  (跳数 {depth})")


# ================= 写 =================

def cmd_create(args, ctx: Components) -> None:
    """create：收录一条知识（来源=user_declared）。"""
    kid = ctx.knowledge.create(Knowledge(
        type=KnowledgeType(args.type), scope=Scope(args.scope),
        title=args.title, summary=args.summary or "", body=args.body or "",
        sources=[Source(SourceType.USER_DECLARED)]))
    print(f"已收录: {kid} → {ctx.truth.knowledge_ref(kid)}")


def cmd_deprecate(args, ctx: Components) -> None:
    """deprecate：废弃（留痕不删，检索降权由索引层自行处理）。"""
    try:
        ctx.knowledge.deprecate(args.id)
    except KeyError:
        print(f"未找到: {args.id}")
        return
    print(f"已废弃: {args.id}")


def cmd_delete(args, ctx: Components) -> None:
    """delete：移出索引工作集（真值档案保留，D7；rebuild 会恢复）。"""
    if ctx.knowledge.delete(args.id):
        print(f"已移出索引工作集: {args.id}（真值保留，rebuild 可恢复）")
    else:
        print(f"未找到: {args.id}")


def cmd_rebuild(args, ctx: Components) -> None:
    """rebuild：清空索引，从真值（knowledge/*.md）全量重建。"""
    count = ctx.rebuild.rebuild()
    print(f"rebuild 完成：{count} 条知识重建")


def cmd_import(args, ctx: Components) -> None:
    """import：存量导入历史会话 JSONL → 生成锚点。"""
    result = ctx.importer.import_history(args.dir, mode=args.mode)
    print(f"存量导入完成：锚点 {result.anchors_created} 个，知识 {result.knowledge_created} 个")
    if result.errors:
        print("错误：")
        for e in result.errors[:10]:
            print(f"  {e}")


# ================= 蒸馏三段（编排归 DistillService，此处只打印） =================

def _find_newest_session() -> str:
    """取 ~/.claude/projects 下最新修改的 jsonl 会话文件。"""
    root = os.path.expanduser("~/.claude/projects")
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    return max(files, key=os.path.getmtime) if files else ""


def _latest_anchor(ctx: Components) -> str:
    """取最新锚点 id。优先 s-<yyyymmdd>-<seq>（单调递增 = 最近创建）；
    无日期型 id 时退回字典序最大（演示/手编 id 场景的兜底）。"""
    anchors = [a.id for a in ctx.truth.list_anchors()]
    dated = [aid for aid in anchors if len(aid) >= 15 and aid[2:10].isdigit()]
    return max(dated or anchors, default="")


def cmd_anchor(args, ctx: Components) -> None:
    """anchor：会话 jsonl（含工具过程，全量保真）→ 锚点。"""
    path = args.jsonl or _find_newest_session()
    if not path or not os.path.exists(path):
        print("未找到 jsonl 会话文件，请传入路径: mem anchor <jsonl_path>")
        raise SystemExit(1)
    anchor_id = ctx.distill.anchor(path, title=args.title or os.path.basename(path))
    if not anchor_id:
        print("该会话无可提取的对话文本")
        raise SystemExit(1)
    a = ctx.truth.get_anchor(anchor_id)
    size = len(a.content) if a else 0
    print(f"锚点已保存: {anchor_id} → data/anchors/{anchor_id}.md")
    print(f"会话长度: {size} 字符")


def cmd_distill(args, ctx: Components) -> None:
    """distill：增量蒸馏准备——重同步锚点 → 输出增量轮次 + 整体上下文（供 AI 提炼）。"""
    anchor_id = args.anchor_id or _latest_anchor(ctx)
    if not anchor_id:
        print("无锚点，请先运行 anchor")
        raise SystemExit(1)
    try:
        dctx = ctx.distill.prepare(anchor_id)
    except (KeyError, ValueError) as e:
        print(e)
        raise SystemExit(1) from e
    first_delta = dctx.delta_turns[0][0] if dctx.delta_turns else dctx.distilled_until
    print(f"锚点: {anchor_id} | 已蒸馏到 turn {dctx.distilled_until} | "
          f"本轮增量 turn {first_delta}..{dctx.processed_until}")
    if ctx.distill.state_path:
        print(f"状态文件: {ctx.distill.state_path}")
    print("=" * 70)
    print(f"【增量轮次】（据此提炼候选；sources.ref 填 {anchor_id}）:")
    for i, t in dctx.delta_turns:
        print(f"── turn {i} ──\n{t}")
    print("=" * 70)
    print("【整体锚点上下文】（供整体视角，勿重复提炼已蒸馏部分，前 8000 字符）：")
    print(dctx.full_text[:8000])


def cmd_confirm(args, ctx: Components) -> None:
    """confirm：候选落库（幂等 + 溯源校验 + MAIN 两段式 + 游标回写 + 自动挂靠）。"""
    with open(args.candidates, encoding="utf-8") as f:
        raw = json.load(f)
    if not raw:
        print("candidates 为空")
        return
    candidates = [DistillCandidate.from_dict(r) for r in raw]
    try:
        result = ctx.distill.confirm(candidates)
    except (ValueError, KeyError) as e:
        print(f"落库失败（未回写蒸馏游标）: {e}")
        raise SystemExit(1) from e
    print(f"落库 {len(result.created_ids)} 条: {result.created_ids}")
    if result.cursor_updated:
        cursor = ctx.truth.get_distill_cursor(result.cursor_updated)
        print(f"已更新锚点蒸馏游标: {result.cursor_updated}.distilled_until = {cursor}")
    if result.attached:
        print(f"自动挂靠: {result.attached}")
    else:
        print("自动挂靠: 无匹配主题")


# ================= 可视化服务 =================

def cmd_serve(args, ctx: Components) -> None:
    """serve：可视化 HTTP 服务（单线程：本地用，避免 SQLite 跨线程问题）。"""
    from http.server import HTTPServer

    from .http import make_handler
    server = HTTPServer(("127.0.0.1", args.port), make_handler(ctx))
    print(f"记忆系统可视化: http://localhost:{args.port}/")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


# ================= 参数解析 =================

def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器（单 mem 命令树）。"""
    parser = argparse.ArgumentParser(prog="mem", description="记忆/知识管理系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="检索（字面 + 语义兜底）")
    p.add_argument("query", help="查询词")
    p.add_argument("--limit", type=int, default=10, help="返回条数")
    p.add_argument("--multi", action="store_true",
                   help="多关键词：按空格拆词，跨词 RRF 融合（多词同命中前置）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="取完整知识")
    p.add_argument("id", help="节点 id")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("create", help="收录一条知识")
    p.add_argument("--type", required=True, help="event|model|fact|method")
    p.add_argument("--scope", required=True, help="universal|domain|personal")
    p.add_argument("--title", required=True, help="标题")
    p.add_argument("--summary", default="", help="判断用摘要")
    p.add_argument("--body", default="", help="正文")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("deprecate", help="废弃知识（留痕不删）")
    p.add_argument("id", help="节点 id")
    p.set_defaults(func=cmd_deprecate)

    p = sub.add_parser("delete", help="移出索引工作集（真值保留）")
    p.add_argument("id", help="节点 id")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("traverse", help="图遍历")
    p.add_argument("id", help="起始节点 id")
    p.add_argument("--depth", type=int, default=2, help="扩展跳数")
    p.set_defaults(func=cmd_traverse)

    p = sub.add_parser("rebuild", help="从真值全量重建索引")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("import", help="存量导入历史会话")
    p.add_argument("--dir", required=True, help="历史会话目录（含 .jsonl）")
    p.add_argument("--mode", default="backfill", help="backfill|incremental")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("anchor", help="会话 jsonl → 保真锚点")
    p.add_argument("jsonl", nargs="?", default="", help="会话 jsonl 路径（缺省取最新会话）")
    p.add_argument("title", nargs="?", default="", help="锚点标题（缺省用文件名）")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("distill", help="增量蒸馏准备")
    p.add_argument("anchor_id", nargs="?", default="", help="锚点 id（缺省取最新锚点）")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("confirm", help="蒸馏候选落库")
    p.add_argument("candidates", help="候选 JSON 文件路径")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("serve", help="可视化 HTTP 服务")
    p.add_argument("--port", type=int, default=8000, help="端口")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None, components: Components | None = None) -> None:
    """入口：解析参数并分发。components 可注入（测试用），缺省走生产装配。"""
    args = build_parser().parse_args(argv)
    ctx = components or assemble()
    args.func(args, ctx)


if __name__ == "__main__":
    main()
