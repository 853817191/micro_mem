"""业务读接口 MemoryReader：检索（数据出系统）。

职责：系统机械检索，返回轻量字段供模型判断（第一原则：检索归系统、判断归模型）。
"""
import os

from ..config import Config
from ..md_parser import parse_frontmatter
from ..store.base import NetworkStore, NodeRecord
from ..types import (EdgeType, ExternalRef, Knowledge, KnowledgeType,
                     RefType, Scope, SearchHit, Source, SourceType, Status)


class MemoryReader:
    """读接口：检索入口。"""

    def __init__(self, config: Config, store: NetworkStore, embedder=None):
        """依赖注入：配置 + 引擎实现 + 向量化器（可选，语义检索）。"""
        self.config = config
        self.store = store
        self.embedder = embedder

    # ================= 检索 =================

    def search(self, query: str, limit: int = 10,
               type: KnowledgeType = None, scope: Scope = None) -> list[SearchHit]:
        """检索：字面（FTS5+LIKE）+ 语义（向量）双路召回 + RRF 合并 → 候选（轻量字段）。"""
        type_filter = self._enum_value(type)
        scope_filter = self._enum_value(scope)
        # 路 1：字面（关键词）
        kw_nodes = self.store.search_keyword(
            query, limit=limit * 2, type_filter=type_filter, scope_filter=scope_filter)
        # 路 2：语义（向量化器存在时）
        sem_nodes = []
        if self.embedder is not None:
            qvec = self.embedder.embed(query)
            sem_nodes = self.store.search_semantic(
                qvec, limit=limit * 2, type_filter=type_filter, scope_filter=scope_filter)
        # RRF 合并：双路都命中的排前面
        merged = self._rrf_merge(kw_nodes, sem_nodes)
        return [self._to_hit(n) for n in merged[:limit]]

    def search_multi(self, terms: list[str], limit: int = 10, k: int = 60) -> list[SearchHit]:
        """多关键词：每词双路召回 → 全局跨词 RRF 融合。

        多词不再要求连续出现；同时命中多个词的候选（如"记忆+开源"）得分更高，自动前置。
        复用 search 的返回列表顺序作为该词内的排名（rank）。
        """
        terms = [t for t in terms if t.strip()]
        if not terms:
            return []
        scores: dict[str, float] = {}
        order: dict[str, SearchHit] = {}
        for term in terms:
            for i, hit in enumerate(self.search(term, limit=limit * 2)):
                scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + i + 1)
                order.setdefault(hit.id, hit)
        ranked = [order[nid] for nid in sorted(scores, key=scores.get, reverse=True)]
        return ranked[:limit]

    def _rrf_merge(self, kw_nodes, sem_nodes, k: int = 60) -> list:
        """RRF 排名融合：两路分数不可比（FTS rank vs 向量距离），用排名融合。"""
        scores = {}
        order = {}
        for i, n in enumerate(kw_nodes):
            scores[n.id] = scores.get(n.id, 0.0) + 1.0 / (k + i + 1)
            order[n.id] = n
        for i, n in enumerate(sem_nodes):
            scores[n.id] = scores.get(n.id, 0.0) + 1.0 / (k + i + 1)
            order.setdefault(n.id, n)
        return [order[nid] for nid in sorted(scores, key=scores.get, reverse=True)]

    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[EdgeType] = None) -> list[tuple[str, str, int]]:
        """图遍历：从 start_id 沿边扩展 N 跳，返回 (节点id, 边类型, 跳数)。"""
        types = ([et.value if isinstance(et, EdgeType) else str(et)
                  for et in edge_types] if edge_types else None)
        return self.store.traverse(start_id, depth=depth, edge_types=types)

    def get(self, id: str) -> Knowledge | None:
        """按 id 取完整知识（含 sources/parents/links/external_refs/body）。"""
        rec = self.store.get_node(id)
        if rec is None:
            return None
        return self._build_knowledge(rec)

    # ================= 私有工具 =================

    def _to_hit(self, node: NodeRecord) -> SearchHit:
        """节点记录 → SearchHit（轻量字段）。"""
        return SearchHit(
            id=node.id, title=node.title, summary=node.summary, file=node.file,
            type=KnowledgeType(node.type), scope=Scope(node.scope))

    def _build_knowledge(self, rec: NodeRecord) -> Knowledge:
        """从 store 读回节点 + 边 + 外部锚点 + 溯源 sources，构造完整 Knowledge。"""
        parents = [t for t, _ in self.store.get_edges(rec.id, [EdgeType.PARENT.value])]
        links = [t for t, _ in self.store.get_edges(rec.id, [EdgeType.LINK.value])]
        refs = [ExternalRef(RefType(rt), rv)
                for rt, rv in self.store.get_external_refs(rec.id)]
        return Knowledge(
            type=KnowledgeType(rec.type), scope=Scope(rec.scope), title=rec.title,
            summary=rec.summary, body=self.store.get_fts_body(rec.id),
            parents=parents, links=links, external_refs=refs,
            sources=self._read_sources(rec.file),
            status=Status(rec.status), id=rec.id,
            created=rec.created, updated=rec.updated)

    def _read_sources(self, file: str) -> list:
        """sources 真值在 md frontmatter（不在 SQLite 索引），按 file 回读补全。"""
        path = os.path.join(self.config.data_dir, file)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                meta, _ = parse_frontmatter(f.read())
            return [Source(SourceType(s.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                           s.get("ref", "")) for s in (meta.get("sources") or [])]
        return []

    @staticmethod
    def _enum_value(v) -> str:
        """枚举/字符串 → value 字符串（空返回空串）。"""
        if v is None:
            return ""
        return v.value if hasattr(v, "value") else str(v)
