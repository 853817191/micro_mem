"""知识写服务：收录/更新/废弃/删除——真值与索引双源协调的唯一责任点（D6）。

写路径铁律：先 truth 后 index，只在本服务一处协调；
index 侧失败由 RebuildService 从真值兜底恢复（系统既有容错设计，文档化）。

一致性约定：
- parents/links 的真值在 Knowledge（md frontmatter），索引边是其投影
- 独立建/删边（add_edge/remove_edge）对 parent/link 同步真值——
  否则 rebuild（从真值重建）会丢失独立建立的边
- trace 边仅索引层（溯源真值由 Knowledge.sources 承担）
"""
from ..application.ports import Embedder, IndexStore, TruthStore
from ..domain.models import EdgeType, ExternalRef, Knowledge, NodeRecord, Status

# update 可改字段（None = 不变）
_UPDATABLE = ("title", "summary", "body", "status", "type", "scope", "aspect",
              "sources", "parents", "links", "external_refs")
# 边类型 → Knowledge 关联字段（trace 无真值字段，仅索引层）
_EDGE_FIELD = {EdgeType.PARENT.value: "parents", EdgeType.LINK.value: "links"}


class KnowledgeService:
    """知识写服务：面向"收录/关联"用例的写入口。"""

    def __init__(self, truth: TruthStore, index: IndexStore,
                 embedder: Embedder | None = None):
        """依赖注入：真值端口 + 索引端口 + 向量化器（可选，语义检索用）。"""
        self._truth = truth
        self._index = index
        self._embedder = embedder

    # ---------------- 收录 ----------------

    def create(self, k: Knowledge) -> str:
        """收录一条知识：校验 → 写真值（分配 id/时间戳）→ 同步索引投影。返回 id。"""
        self._validate(k)
        kid = self._truth.save_knowledge(k)
        self._sync_index(kid)
        return kid

    # ---------------- 更新 / 废弃 ----------------

    def update(self, id: str, **changes) -> None:
        """更新知识：只改指定字段（缺省/None = 不变）；真值与索引同步。"""
        k = self._truth.get_knowledge(id)
        if k is None:
            raise KeyError(f"知识不存在: {id}")
        for field_name in _UPDATABLE:
            if field_name in changes and changes[field_name] is not None:
                setattr(k, field_name, changes[field_name])
        self._truth.save_knowledge(k)
        self._sync_index(id)

    def deprecate(self, id: str) -> None:
        """废弃：status → deprecated（留痕不删）。"""
        self.update(id, status=Status.DEPRECATED)

    # ---------------- 删除（D7 语义） ----------------

    def delete(self, id: str) -> bool:
        """删除 = 移出索引工作集，真值档案保留（D7 用户拍板）。

        rebuild 会按真值全量恢复；想彻底消失：deprecate 或手动删真值文件。
        """
        if self._index.get_node(id) is None:
            return False
        self._index.delete_node(id)
        return True

    # ---------------- 关联 / 外部锚点 ----------------

    def add_edge(self, from_id: str, to_id: str, edge_type: EdgeType) -> None:
        """建边：索引 + （parent/link 时）同步真值关联字段。"""
        self._index.add_edge(from_id, to_id, edge_type.value)
        self._sync_edge_to_truth(from_id, to_id, edge_type, add=True)

    def remove_edge(self, from_id: str, to_id: str, edge_type: EdgeType) -> None:
        """删边：索引 + （parent/link 时）同步真值关联字段。"""
        self._index.remove_edge(from_id, to_id, edge_type.value)
        self._sync_edge_to_truth(from_id, to_id, edge_type, add=False)

    def add_external_ref(self, node_id: str, ref: ExternalRef) -> None:
        """挂外部锚点：索引 + 同步真值。"""
        self._index.add_external_ref(node_id, ref.type.value, ref.value)
        k = self._truth.get_knowledge(node_id)
        if k is not None and ref not in k.external_refs:
            k.external_refs.append(ref)
            self._truth.save_knowledge(k)

    def remove_external_ref(self, node_id: str, ref: ExternalRef) -> None:
        """移除外部锚点：索引 + 同步真值。"""
        self._index.remove_external_ref(node_id, ref.type.value, ref.value)
        k = self._truth.get_knowledge(node_id)
        if k is not None and ref in k.external_refs:
            k.external_refs.remove(ref)
            self._truth.save_knowledge(k)

    # ---------------- 私有 ----------------

    @staticmethod
    def _validate(k: Knowledge) -> None:
        """必填校验。"""
        if not k.title:
            raise ValueError("title 不能为空")
        if k.type is None or k.scope is None:
            raise ValueError("type/scope 必填")

    def _sync_index(self, kid: str) -> None:
        """从真值读全量 → 投影到索引（节点 + 边 + 外部锚点 + 向量，全量替换语义）。

        索引只是真值的投影：永远以刚写入的真值为准重建该节点的索引面，
        不存在"改 A 忘改 B"的缝隙。
        """
        k = self._truth.get_knowledge(kid)
        assert k is not None  # 刚写入真值，必然存在
        node = NodeRecord(
            id=k.id, file=self._truth.knowledge_ref(k.id), title=k.title,
            summary=k.summary, type=k.type.value, scope=k.scope.value,
            status=k.status.value, created=k.created, updated=k.updated,
            aspect=k.aspect)
        self._index.upsert_node(node, body=k.body)
        # 边全量替换（parent/link；不动 trace）
        for to_id, et in self._index.get_edges(kid, list(_EDGE_FIELD)):
            self._index.remove_edge(kid, to_id, et)
        for parent in k.parents:
            self._index.add_edge(kid, parent, EdgeType.PARENT.value)
        for link in k.links:
            self._index.add_edge(kid, link, EdgeType.LINK.value)
        # 外部锚点全量替换
        for rt, rv in self._index.get_external_refs(kid):
            self._index.remove_external_ref(kid, rt, rv)
        for r in k.external_refs:
            self._index.add_external_ref(kid, r.type.value, r.value)
        # 向量：对 summary 算 embedding（有向量化器且 summary 非空时）
        if self._embedder is not None and k.summary:
            self._index.save_vector(kid, self._embedder.embed(k.summary))

    def _sync_edge_to_truth(self, from_id: str, to_id: str,
                            edge_type: EdgeType, add: bool) -> None:
        """parent/link 边同步真值关联字段（trace 无真值字段，跳过）。"""
        field_name = _EDGE_FIELD.get(edge_type.value)
        if not field_name:
            return
        k = self._truth.get_knowledge(from_id)
        if k is None:
            return
        targets: list = getattr(k, field_name)
        changed = (to_id not in targets) if add else (to_id in targets)
        if changed:
            targets.append(to_id) if add else targets.remove(to_id)
            self._truth.save_knowledge(k)
