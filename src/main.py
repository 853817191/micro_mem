"""统一命令入口：rebuild / create / search / traverse / get / import。

用法：
    python src/main.py rebuild
    python src/main.py create --type fact --scope domain --title "需求单业务流程" --summary "..." --body "..."
    python src/main.py search "审批"
    python src/main.py traverse k-0010 --depth 2
    python src/main.py get k-0010
    python src/main.py import --dir <历史会话目录>
"""
import argparse
import glob
import os
import sys

# Windows 下强制 UTF-8 输出，避免管道/控制台中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from dataclasses import dataclass

# 项目根加入 path，统一以 src 包方式导入（包内相对导入正常工作）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.distiller import Distiller                 # noqa: E402
from src.api.importer import Importer                   # noqa: E402
from src.api.reader import MemoryReader                 # noqa: E402
from src.api.writer import MemoryWriter                 # noqa: E402
from src.config import Config                           # noqa: E402
from src.embedder import HashEmbedder                   # noqa: E402
from src.md_parser import parse_file                    # noqa: E402
from src.store.base import NetworkStore                 # noqa: E402
from src.store.sqlite_store import SqliteNetworkStore   # noqa: E402
from src.types import (ExternalRef, Knowledge, KnowledgeType,  # noqa: E402
                       RefType, Scope, Source, SourceType, Status)


@dataclass
class Components:
    """装配好的组件集合（命令函数统一接收）。"""
    config: Config
    store: NetworkStore
    writer: MemoryWriter
    reader: MemoryReader
    distiller: Distiller
    importer: Importer


# ================= 装配 =================

def build_components() -> Components:
    """装配：加载配置 → 选引擎 → 向量化器 → 组装四接口。"""
    config = Config()
    if config.network_store == "sqlite":
        store = SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)
    else:
        raise NotImplementedError(f"引擎 {config.network_store} 未实现")
    embedder = HashEmbedder(dim=config.embedding_dim)
    writer = MemoryWriter(config, store, embedder=embedder)
    reader = MemoryReader(config, store, embedder=embedder)
    distiller = Distiller(config, store, writer, reader)
    importer = Importer(config, store, writer)
    return Components(config, store, writer, reader, distiller, importer)


# ================= 命令实现 =================

def cmd_rebuild(args, ctx: Components) -> None:
    """rebuild：清空索引，从 knowledge/*.md 全量重建（幂等，保留原 id）。"""
    ctx.store.clear_all()
    paths = sorted(glob.glob(os.path.join(ctx.config.knowledge_dir(), "k-*.md")))
    for path in paths:
        meta, body = parse_file(path)
        ctx.writer.create_knowledge(_knowledge_from_meta(meta, body), skip_md=True)
    print(f"rebuild 完成：{len(paths)} 条知识重建")


def cmd_create(args, ctx: Components) -> None:
    """create：收录一条知识（来源=user_declared）。"""
    k = Knowledge(
        type=KnowledgeType(args.type), scope=Scope(args.scope),
        title=args.title, summary=args.summary or "", body=args.body or "",
        sources=[Source(SourceType.USER_DECLARED)])
    kid = ctx.writer.create_knowledge(k)
    print(f"已收录: {kid} → data/knowledge/")


def cmd_search(args, ctx: Components) -> None:
    """search：双路召回（字面 + 语义 + RRF）→ 候选；--multi 按空格拆词做多关键词融合。"""
    if args.multi:
        terms = args.query.split()
        hits = ctx.reader.search_multi(terms, limit=args.limit)
    else:
        hits = ctx.reader.search(args.query, limit=args.limit)
    if not hits:
        print("无命中")
        return
    for h in hits:
        print(f"[{h.id}] {h.title}  ({h.type.value}/{h.scope.value}) score={h.score}")
        if h.summary:
            print(f"    {h.summary}")


def cmd_traverse(args, ctx: Components) -> None:
    """traverse：图遍历，从 start_id 沿边扩展 N 跳。"""
    results = ctx.reader.traverse(args.id, depth=args.depth)
    if not results:
        print(f"{args.id} 无关联节点")
        return
    for to_id, etype, depth in results:
        print(f"  {args.id} -[{etype}]→ {to_id}  (跳数 {depth})")


def cmd_get(args, ctx: Components) -> None:
    """get：按 id 取完整知识。"""
    k = ctx.reader.get(args.id)
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


def cmd_import(args, ctx: Components) -> None:
    """import：存量导入历史会话 JSONL → 生成锚点。"""
    result = ctx.importer.import_history(args.dir, mode=args.mode)
    print(f"存量导入完成：锚点 {result.anchors_created} 个，知识 {result.knowledge_created} 个")
    if result.errors:
        print("错误：")
        for e in result.errors[:10]:
            print(f"  {e}")


# ================= 工具 =================

def _knowledge_from_meta(meta: dict, body: str) -> Knowledge:
    """把解析的 frontmatter + body 转成 Knowledge（rebuild 用，容错空列表=None）。"""
    sources = [Source(SourceType(s.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                      s.get("ref", "")) for s in (meta.get("sources") or [])]
    refs = [ExternalRef(RefType(r["type"]), r["value"])
            for r in (meta.get("external_refs") or [])]
    return Knowledge(
        id=meta.get("id", ""),
        type=KnowledgeType(meta.get("type", "fact")),
        scope=Scope(meta.get("scope", "domain")),
        title=meta.get("title", ""),
        summary=meta.get("summary", ""),
        body=body,
        sources=sources,
        parents=list(meta.get("parents") or []),
        links=list(meta.get("links") or []),
        external_refs=refs,
        status=Status(meta.get("status", "draft")))


# ================= 参数解析 =================

def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="memory", description="记忆/知识管理系统")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser("rebuild", help="从 knowledge/*.md 全量重建索引")
    p_rebuild.set_defaults(func=cmd_rebuild)

    p_create = sub.add_parser("create", help="收录一条知识")
    p_create.add_argument("--type", required=True, help="event|method|fact")
    p_create.add_argument("--scope", required=True, help="universal|domain|personal")
    p_create.add_argument("--title", required=True, help="标题")
    p_create.add_argument("--summary", default="", help="判断用摘要")
    p_create.add_argument("--body", default="", help="正文")
    p_create.set_defaults(func=cmd_create)

    p_search = sub.add_parser("search", help="检索（字面+语义）")
    p_search.add_argument("query", help="查询词")
    p_search.add_argument("--limit", type=int, default=10, help="返回条数")
    p_search.add_argument("--multi", action="store_true",
                          help="多关键词：按空格拆词，跨词 RRF 融合（多词同命中前置）")
    p_search.set_defaults(func=cmd_search)

    p_traverse = sub.add_parser("traverse", help="图遍历")
    p_traverse.add_argument("id", help="起始节点 id")
    p_traverse.add_argument("--depth", type=int, default=2, help="扩展跳数")
    p_traverse.set_defaults(func=cmd_traverse)

    p_get = sub.add_parser("get", help="取完整知识")
    p_get.add_argument("id", help="节点 id")
    p_get.set_defaults(func=cmd_get)

    p_import = sub.add_parser("import", help="存量导入历史会话")
    p_import.add_argument("--dir", required=True, help="历史会话目录（含 .jsonl）")
    p_import.add_argument("--mode", default="backfill", help="backfill|incremental")
    p_import.set_defaults(func=cmd_import)

    return parser


def main() -> None:
    """入口：解析参数并分发到对应命令。"""
    parser = build_parser()
    args = parser.parse_args()
    ctx = build_components()
    args.func(args, ctx)


if __name__ == "__main__":
    main()
