"""索引重建服务：从真值全量重建索引投影（架构自证链路：索引随时可弃、可重建）。

rebuild = TruthStore → IndexStore 的全量投影。真值是唯一权威；
索引只是它的衍生品，损坏/漂移/换引擎都通过本服务重生。
"""
from ..application.ports import Embedder, IndexStore, TruthStore
from ..domain.models import EdgeType, NodeRecord


class RebuildService:
    """重建服务：清空索引 → 遍历真值 → 逐条投影（节点/边/外部锚点/向量）。"""

    def __init__(self, truth: TruthStore, index: IndexStore,
                 embedder: Embedder | None = None):
        """依赖注入：真值端口 + 索引端口 + 向量化器（可选）。"""
        self._truth = truth
        self._index = index
        self._embedder = embedder

    def rebuild(self) -> int:
        """全量重建索引，返回重建的知识条数。

        真值中同 id 出现两次（数据损坏信号）→ 显式报错，不静默覆盖。
        """
        self._index.clear_all()
        count = 0
        seen: set[str] = set()
        for k in self._truth.list_knowledge():
            if k.id in seen:
                raise ValueError(f"rebuild 发现重复知识 id: {k.id}（真值数据损坏）")
            seen.add(k.id)
            node = NodeRecord(
                id=k.id, file=self._truth.knowledge_ref(k.id), title=k.title,
                summary=k.summary, type=k.type.value, scope=k.scope.value,
                status=k.status.value, created=k.created, updated=k.updated)
            self._index.upsert_node(node, body=k.body)
            for parent in k.parents:
                self._index.add_edge(k.id, parent, EdgeType.PARENT.value)
            for link in k.links:
                self._index.add_edge(k.id, link, EdgeType.LINK.value)
            for r in k.external_refs:
                self._index.add_external_ref(k.id, r.type.value, r.value)
            if self._embedder is not None and k.summary:
                self._index.save_vector(k.id, self._embedder.embed(k.summary))
            count += 1
        return count
