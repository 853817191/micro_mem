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
    mem distill [anchor_id] [--domain X]       增量蒸馏准备 / 主题驱动领域盘点
    mem plan <plan.json>                       提交蒸馏计划（校验 + 归档 data/plans/）
    mem confirm <plan_id> [--force]            计划落库（硬校验 + $ROOT 两段式 + 游标回写）
    mem serve [--port 8000]                    可视化 HTTP 服务

本层只做参数解析与打印；全部业务编排走 application 服务。
"""
import argparse
import glob
import json
import os
import sys

from ..application.distill_service import DistillService
from ..application.plan_checker import PlanRejectedError
from ..composition import Components, assemble
from ..domain.models import DistillPlan, Knowledge, KnowledgeType, Scope, Source, SourceType

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
    print(f"id: {k.id}   type: {k.type.value}   scope: {k.scope.value}   "
          f"status: {k.status.value}   aspect: {k.aspect or '-'}")
    print(f"title: {k.title}")
    print(f"summary: {k.summary}")
    print(f"parents: {k.parents}   links: {k.links}")
    print(f"external_refs: {[(r.type.value, r.value) for r in k.external_refs]}")
    print(f"sources: {[(s.type.value, s.ref, s.turns or None) for s in k.sources]}")
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
        aspect=args.aspect or "",
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


# ================= 蒸馏（编排归 DistillService，此处只打印） =================

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
    """anchor：S0 素材解析 →（--preview 确认闸门）→ S1 落库；--deprecate 作废锚点。"""
    if args.deprecate:
        if args.source or args.text:
            print("--deprecate 与素材输入互斥：作废已有锚点就不要给新素材")
            raise SystemExit(1)
        if ctx.truth.deprecate_anchor(args.deprecate):
            print(f"锚点已作废: {args.deprecate}（真值保留；新蒸馏计划不得再引用）")
        else:
            print(f"未找到锚点: {args.deprecate}")
            raise SystemExit(1)
        return
    try:
        if args.text:
            if args.source:
                print("--text 与 source 互斥：直接输入文本就不要给路径参数")
                raise SystemExit(1)
            title = args.title or args.text.strip()[:20]
            if args.preview:
                _print_preview(ctx.distill.preview_turns(text=args.text),
                               "inline-text", title)
                return
            anchor_id = ctx.distill.anchor_text(args.text, title=args.title)
            if not anchor_id:
                print("空文本，无可入库内容")
                raise SystemExit(1)
        else:
            source = args.source or _find_newest_session()
            if not source or (not source.startswith(("http://", "https://"))
                              and not os.path.exists(source)):
                print("未找到素材：mem anchor <路径|URL>，或 mem anchor --text \"描述\"")
                raise SystemExit(1)
            title = args.title or DistillService.default_title(source)
            if args.preview:
                _print_preview(ctx.distill.preview_turns(source=source),
                               source, title)
                return
            _warn_duplicate_anchor(ctx, source)
            anchor_id = ctx.distill.anchor(source, title=args.title)
            if not anchor_id:
                print("该素材无有效内容（零轮）")
                raise SystemExit(1)
    except ValueError as e:
        print(e)
        raise SystemExit(1)
    a = ctx.truth.get_anchor(anchor_id)
    size = len(a.content) if a else 0
    print(f"锚点已保存: {anchor_id} → data/anchors/{anchor_id}.md")
    print(f"会话长度: {size} 字符")


def _warn_duplicate_anchor(ctx: Components, source: str) -> None:
    """重复素材提示（不拒收）：同 source 已有锚点 = 将作为新快照入库。"""
    dups = [a for a in ctx.truth.list_anchors()
            if a.source == source and a.status != "deprecated"]
    if dups:
        ids = "、".join(a.id for a in dups)
        print(f"提示: 同素材已有锚点 {ids}，本次将作为新快照再入库一份"
              f"（旧档保留；如旧档有误请先 mem anchor --deprecate <id>）")


def _print_preview(turns: list[tuple[int, str]], source: str,
                   title: str = "") -> None:
    """素材确认视图（确认闸门）：标题 + 轮次摘要清单，人核对遗漏/偏差后再落库。"""
    print(f"【素材确认】共 {len(turns)} 轮 | 源: {source}")
    if title:
        print(f"将生成标题: {title}（可用 --title 覆盖）")
    print("=" * 70)
    for n, t in turns:
        print(f"── turn {n} ──")
        print(t if len(t) <= 200 else t[:200] + "……")
        print()
    print("核对轮次切分与内容完整性。确认无误后去掉 --preview 执行正式入库；")
    print("有遗漏/偏差：修正素材后重新 --preview。")


def cmd_distill(args, ctx: Components) -> None:
    """distill：会话驱动 = 增量蒸馏准备；--domain = 主题驱动领域盘点。"""
    if args.domain:
        if args.anchor_id:
            print("--domain 与 anchor_id 互斥：领域盘点用 --domain，会话增量用 anchor_id")
            raise SystemExit(1)
        _cmd_distill_domain(args, ctx)
        return
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
    print("=" * 70)
    if dctx.view_compressed:
        print("【增量轮次·压缩视图】（超预算触发压缩：tool_result 剥离、长文截断；"
              "定位据此，原文下钻见文末锚点文件）:")
    else:
        print("【增量轮次】（全量原文；source.anchor 填 "
              f"{anchor_id}）:")
    for i, t in dctx.view_turns:
        print(f"── turn {i} ──\n{t}")
    print("=" * 70)
    print("【相关已有子树（预检索）】（计划项 parent/edit_id 坐标的参照系）:")
    if dctx.related:
        for k in dctx.related:
            aspect = f" ({k.aspect})" if k.aspect else ""
            parent = k.parents[0] if k.parents else "-"
            print(f"[{k.id}] {k.title}{aspect} — {k.summary}   parent: {parent}")
    else:
        print("  （无相关已有节点——大概率是新领域/create）")
    print("=" * 70)
    print(f"完整原文见锚点文件 data/anchors/{anchor_id}.md（按 ── turn N ── 定位）；"
          f"source.anchor 填 {anchor_id}")


def _cmd_distill_domain(args, ctx: Components) -> None:
    """distill --domain：主题驱动盘点报告（树全景 + 相关锚点 + 机械空缺统计）。"""
    dctx = ctx.distill.prepare_domain(args.domain)
    if not dctx.root_id:
        print(f"未定位到领域根: \"{args.domain}\"")
        for n in dctx.candidates:
            print(f"  [{n.id}] {n.title}")
        print("请用 id 或更完整的 title 重试" if dctx.candidates
              else "（库中无任何领域根）")
        raise SystemExit(1)
    counts: dict[str, int] = {}
    for k in dctx.tree:
        if k.aspect:
            counts[k.aspect] = counts.get(k.aspect, 0) + 1
    axis_info = " / ".join(f"{a} {n}" for a, n in counts.items()) or "无切面节点"
    print(f"领域: {dctx.root_title} ({dctx.root_id}) | "
          f"树规模 {len(dctx.tree)} 节点（{axis_info}）")
    print("=" * 70)
    print("【领域树全景】（按轴分组）:")
    if dctx.tree:
        root = dctx.tree[0]
        print(f"[{root.id}] {root.title} — {root.summary}   （领域根）")
    grouped: dict[str, list[Knowledge]] = {}
    for k in dctx.tree[1:]:
        grouped.setdefault(k.aspect or "(无切面)", []).append(k)
    for aspect, nodes in grouped.items():
        print(f"── {aspect} ──")
        for k in nodes:
            parent = k.parents[0] if k.parents else "-"
            print(f"  [{k.id}] {k.title} — {k.summary}   parent: {parent}")
    print("=" * 70)
    print("【相关锚点】（盘点素材：粗读锚点对照已有树，缺口判断归 AI）:")
    if dctx.anchors:
        for a in dctx.anchors:
            cursor = "未蒸馏" if a.distilled_until < 0 else f"游标 {a.distilled_until}"
            print(f"  [{a.id}] {a.title}（{a.date}，{cursor}）")
    else:
        print("  （无相关锚点——该领域可能尚无历史会话锚定）")
    print("=" * 70)
    print("【空缺提示】（机械统计）:")
    if dctx.gaps:
        for g in dctx.gaps:
            print(f"  - {g}")
    else:
        print("  （各轴均有节点，相关锚点均已蒸馏）")
    print("=" * 70)
    print(f"下一步：产出 driver=topic 的蒸馏计划（domain_root 填 existing + "
          f"{dctx.root_id}），mem plan 提交")


def _print_rejected(e: PlanRejectedError) -> None:
    """校验拒收的统一打印：逐条列出 + force 提示。"""
    print("校验未通过（未落库、未动游标）：")
    for issue in e.issues:
        print(f"  {issue.format()}")
    if e.need_force:
        print("以上为警告级问题，人工确认无误后加 --force 重试")


def cmd_plan(args, ctx: Components) -> None:
    """plan：提交蒸馏计划（R2 批准动作）——校验 + 回填 expected_turns + 归档。"""
    try:
        with open(args.file, encoding="utf-8") as f:
            plan = DistillPlan.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"计划文件解析失败: {e}")
        raise SystemExit(1) from e
    try:
        plan, warnings = ctx.distill.submit_plan(plan)
    except PlanRejectedError as e:
        _print_rejected(e)
        raise SystemExit(1) from e
    except ValueError as e:
        print(e)
        raise SystemExit(1) from e
    print(f"计划已归档: {plan.plan_id} → data/plans/{plan.plan_id}.json")
    print(f"状态: approved | 增量轮次全集: {plan.expected_turns}")
    for w in warnings:
        print(f"  {w.format()}")
    print(f"下一步：逐项填充 title/summary/body（只回读各项 source.turns 标注的轮次），"
          f"然后运行 mem confirm {plan.plan_id}")


def cmd_confirm(args, ctx: Components) -> None:
    """confirm：计划落库（硬校验 + $ROOT 两段式 + 轮次级溯源 + 游标回写 + 计划回写）。"""
    try:
        result = ctx.distill.confirm_plan(args.plan_id, force=args.force)
    except PlanRejectedError as e:
        _print_rejected(e)
        raise SystemExit(1) from e
    except (KeyError, ValueError) as e:
        print(e)
        raise SystemExit(1) from e
    print(f"计划 {result.plan_id} 落库完成："
          f"新建 {len(result.created_ids)} 条 {result.created_ids}，"
          f"更新 {len(result.edited_ids)} 条 {result.edited_ids}，"
          f"跳过 {result.skipped} 条")
    if result.cursor_updated:
        cursor = ctx.truth.get_distill_cursor(result.cursor_updated)
        print(f"已更新锚点蒸馏游标: {result.cursor_updated}.distilled_until = {cursor}")
    for w in result.warnings:
        print(f"  已放行警告: {w}")
    print(f"计划已回写落库结果并归档（status=confirmed）: data/plans/{result.plan_id}.json")


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
    p.add_argument("--aspect", default="",
                   help="领域切面：flow|structure|boundary|constraint|…（可扩展，可空）")
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

    p = sub.add_parser("anchor", help="素材（jsonl/md/html/URL/--text）→ 保真锚点")
    p.add_argument("source", nargs="?", default="",
                   help="素材路径或 http(s) URL（jsonl 缺省取最新会话）")
    p.add_argument("title", nargs="?", default="", help="锚点标题（缺省按来源生成）")
    p.add_argument("--text", default="",
                   help="直接输入一段描述文本（整段=1 turn；与 source 互斥）")
    p.add_argument("--deprecate", default="",
                   help="作废已有锚点（逻辑标，真值保留；新计划不得再引用它）")
    p.add_argument("--preview", action="store_true",
                   help="确认闸门：只渲染素材确认视图不落库，核对后再去掉本参数执行")
    p.set_defaults(func=cmd_anchor)

    p = sub.add_parser("distill", help="增量蒸馏准备 / --domain 领域盘点")
    p.add_argument("anchor_id", nargs="?", default="", help="锚点 id（缺省取最新锚点）")
    p.add_argument("--domain", default="",
                   help="主题驱动：领域根 title 或 id，输出盘点报告（与 anchor_id 互斥）")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("plan", help="提交蒸馏计划（校验 + 归档）")
    p.add_argument("file", help="计划 JSON 文件路径（AI 手写的 draft）")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("confirm", help="蒸馏计划落库")
    p.add_argument("plan_id", help="已归档的计划 id（plan-<yyyymmdd>-<seq>）")
    p.add_argument("--force", action="store_true", help="警告级问题人工确认后放行")
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
