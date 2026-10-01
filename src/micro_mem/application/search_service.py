"""检索服务：机械检索出候选（检索归系统），get 从真值组装全文（D3）。

读路径责任（D6）：index 出候选，truth 出全文，只在本服务一处组装。
语义兜底策略（D8）：on_zero_hit（零命中才兜底）| always（总双路）| off（仅字面）。
"""
from ..application.ports import Embedder, IndexStore, TruthStore
from ..domain.models import (
    SEMANTIC_FALLBACKS,
    EdgeType,
    Knowledge,
    KnowledgeType,
    NodeRecord,
    Scope,
    SearchHit,
)

_RRF_K = 60   # RRF 融合常数（两路分数不可比：FTS rank vs 向量距离，用排名融合）


class SearchService:
    """检索服务：search / search_multi / traverse / get。"""

    def __init__(self, truth: TruthStore, index: IndexStore,
                 embedder: Embedder | None = None,
                 semantic_fallback: str = "on_zero_hit"):
        """依赖注入：真值端口 + 索引端口 + 向量化器（可选）+ 语义兜底策略。"""
        if semantic_fallback not in SEMANTIC_FALLBACKS:
            raise ValueError(
                f"semantic_fallback 非法值 {semantic_fallback!r}，可选: {SEMANTIC_FALLBACKS}")
        self._truth = truth
        self._index = index
        self._embedder = embedder
        self._fallback = semantic_fallback

    # ---------------- 检索 ----------------

    def search(self, query: str, limit: int = 10,
               type: KnowledgeType | None = None,
               scope: Scope | None = None) -> list[SearchHit]:
        """检索：字面（关键词）主召回 + 按 D8 策略的语义兜底，RRF 融合。"""
        type_filter = self._enum_value(type)
        scope_filter = self._enum_value(scope)
        kw_nodes = self._index.search_keyword(
            query, limit=limit * 2, type_filter=type_filter, scope_filter=scope_filter)
        sem_nodes: list[NodeRecord] = []
        if self._embedder is not None and self._should_run_semantic(kw_nodes):
            # 占位 HashEmbedder 无真实语义，其"最近邻"会灌入无关节点；
            # on_zero_hit 下仅在关键词零命中时兜底，不参与有命中时的排序
            sem_nodes = self._index.search_semantic(
                self._embedder.embed(query), limit=limit * 2,
                type_filter=type_filter, scope_filter=scope_filter)
        merged = self._rrf_merge(kw_nodes, sem_nodes)
        return [self._to_hit(n, score) for n, score in merged[:limit]]

    def search_multi(self, terms: list[str], limit: int = 10) -> list[SearchHit]:
        """多关键词：每词召回 → 全局跨词 RRF 融合（多词同命中前置）。"""
        terms = [t for t in terms if t.strip()]
        if not terms:
            return []
        scores: dict[str, float] = {}
        order: dict[str, SearchHit] = {}
        for term in terms:
            for i, hit in enumerate(self.search(term, limit=limit * 2)):
                scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (_RRF_K + i + 1)
                order.setdefault(hit.id, hit)
        ranked = [order[nid] for nid in sorted(scores, key=lambda nid: scores[nid], reverse=True)]
        for hit in ranked:
            hit.score = scores[hit.id]
        return ranked[:limit]

    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[EdgeType] | None = None) -> list[tuple[str, str, int]]:
        """图遍历：从 start_id 沿边双向扩展 N 跳，返回 (节点id, 边类型, 跳数)。"""
        types = ([et.value if isinstance(et, EdgeType) else str(et)
                  for et in edge_types] if edge_types else None)
        return self._index.traverse(start_id, depth=depth, edge_types=types)

    def get(self, id: str) -> Knowledge | None:
        """按 id 取完整知识：全文从真值组装（body/sources/关联都在真值，D3）。

        索引缺失（如 delete 移出工作集后）→ None，与"检索不到"语义一致（D7）。
        """
        if self._index.get_node(id) is None:
            return None
        return self._truth.get_knowledge(id)

    # ---------------- 私有 ----------------

    def _should_run_semantic(self, kw_nodes: list[NodeRecord]) -> bool:
        """D8：on_zero_hit 零命中才兜底；always 总跑；off 不跑。"""
        if self._fallback == "off":
            return False
        if self._fallback == "always":
            return True
        return not kw_nodes

    @staticmethod
    def _rrf_merge(kw_nodes: list[NodeRecord],
                   sem_nodes: list[NodeRecord]) -> list[tuple[NodeRecord, float]]:
        """RRF 排名融合：两路分数不可比（FTS rank vs 向量距离），用排名融合。"""
        scores: dict[str, float] = {}
        order: dict[str, NodeRecord] = {}
        for i, n in enumerate(kw_nodes):
            scores[n.id] = scores.get(n.id, 0.0) + 1.0 / (_RRF_K + i + 1)
            order[n.id] = n
        for i, n in enumerate(sem_nodes):
            scores[n.id] = scores.get(n.id, 0.0) + 1.0 / (_RRF_K + i + 1)
            order.setdefault(n.id, n)
        ranked = sorted(scores, key=lambda nid: scores[nid], reverse=True)
        return [(order[nid], scores[nid]) for nid in ranked]

    @staticmethod
    def _to_hit(node: NodeRecord, score: float = 0.0) -> SearchHit:
        """节点记录 → SearchHit（轻量字段，供模型判断）。"""
        return SearchHit(
            id=node.id, title=node.title, summary=node.summary, file=node.file,
            type=KnowledgeType(node.type), scope=Scope(node.scope), score=score)

    @staticmethod
    def _enum_value(v) -> str:
        """枚举/字符串 → value 字符串（空返回空串）。"""
        if v is None:
            return ""
        return v.value if hasattr(v, "value") else str(v)
