"""IndexStore 的内存实现：单测基建（毫秒级，不起 SQLite）。

检索语义刻意简单：关键词 = 大小写不敏感的子串包含（title/summary/body），
语义 = L2 最近邻。不追求与 SQLite 实现（FTS5 trigram + rank）逐结果一致——
端口级契约约束的是"找得到/找不到"的行为面，排序细节归各实现。
"""
import math
from dataclasses import replace

from ..application.ports import IndexStore
from ..domain.models import NodeRecord


class InMemoryIndexStore(IndexStore):
    """内存索引库：dict + set 实现节点/边/外部锚点/向量。"""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeRecord] = {}
        self._bodies: dict[str, str] = {}              # 索引用 body 副本（非数据源，D3）
        self._edges: set[tuple[str, str, str]] = set()  # (from_id, to_id, edge_type)
        self._refs: dict[str, set[tuple[str, str]]] = {}  # node_id → {(ref_type, ref_value)}
        self._vectors: dict[str, list[float]] = {}

    # ---------------- 节点 ----------------

    def upsert_node(self, node: NodeRecord, body: str = "") -> None:
        """全量覆盖（端口契约：调用方给完整 body）。"""
        self._nodes[node.id] = replace(node)
        self._bodies[node.id] = body

    def delete_node(self, id: str) -> None:
        """连带边 / 外部锚点 / 向量一并清除。"""
        self._nodes.pop(id, None)
        self._bodies.pop(id, None)
        self._refs.pop(id, None)
        self._vectors.pop(id, None)
        self._edges = {e for e in self._edges if e[0] != id and e[1] != id}

    def get_node(self, id: str) -> NodeRecord | None:
        n = self._nodes.get(id)
        return replace(n) if n is not None else None

    # ---------------- 检索 ----------------

    def search_keyword(self, query: str, limit: int = 10,
                       type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """子串包含匹配（title/summary/body），按 id 排序保证结果稳定。"""
        q = query.lower()
        hits = []
        for nid in sorted(self._nodes):
            n = self._nodes[nid]
            if type_filter and n.type != type_filter:
                continue
            if scope_filter and n.scope != scope_filter:
                continue
            haystack = f"{n.title}\n{n.summary}\n{self._bodies.get(nid, '')}".lower()
            if q in haystack:
                hits.append(replace(n))
            if len(hits) >= limit:
                break
        return hits

    def search_semantic(self, query_vec: list[float], limit: int = 10,
                        type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """L2 最近邻（与 sqlite-vec 默认距离对齐）；无向量返回空。"""
        scored = []
        for nid, vec in self._vectors.items():
            n = self._nodes.get(nid)
            if n is None:
                continue
            if type_filter and n.type != type_filter:
                continue
            if scope_filter and n.scope != scope_filter:
                continue
            scored.append((self._l2(query_vec, vec), nid))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [replace(self._nodes[nid]) for _, nid in scored[:limit]]

    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[str] | None = None) -> list[tuple[str, str, int]]:
        """BFS 双向扩展（出边 + 入边），同节点只取首次到达的跳数。"""
        visited = {start_id}
        result: list[tuple[str, str, int]] = []
        frontier = [(start_id, 0)]
        while frontier:
            nid, d = frontier.pop(0)
            if d >= depth:
                continue
            for f, t, et in sorted(self._edges):
                if edge_types and et not in edge_types:
                    continue
                neighbor = ""
                if f == nid and t not in visited:
                    neighbor = t
                elif t == nid and f not in visited:
                    neighbor = f
                if neighbor:
                    visited.add(neighbor)
                    result.append((neighbor, et, d + 1))
                    frontier.append((neighbor, d + 1))
        return result

    # ---------------- 边 ----------------

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        self._edges.add((from_id, to_id, edge_type))

    def remove_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        self._edges.discard((from_id, to_id, edge_type))

    def get_edges(self, node_id: str, edge_types: list[str] | None = None) -> list[tuple[str, str]]:
        edges = [(t, et) for f, t, et in sorted(self._edges)
                 if f == node_id and (not edge_types or et in edge_types)]
        return edges

    def get_all_edges(self) -> list[tuple[str, str, str]]:
        return sorted(self._edges, key=lambda e: (e[2], e[0], e[1]))

    def has_parent_edge(self, node_id: str) -> bool:
        return any(t == node_id and et == "parent" for _, t, et in self._edges)

    # ---------------- 外部锚点 ----------------

    def add_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        self._refs.setdefault(node_id, set()).add((ref_type, ref_value))

    def remove_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        refs = self._refs.get(node_id)
        if refs is not None:
            refs.discard((ref_type, ref_value))

    def get_external_refs(self, node_id: str) -> list[tuple[str, str]]:
        return sorted(self._refs.get(node_id, set()))

    # ---------------- 向量 / 重建 ----------------

    def save_vector(self, node_id: str, embedding: list[float]) -> None:
        if node_id in self._nodes:
            self._vectors[node_id] = list(embedding)

    def get_all_nodes(self) -> list[NodeRecord]:
        return [replace(self._nodes[nid]) for nid in sorted(self._nodes)]

    def clear_all(self) -> None:
        self._nodes.clear()
        self._bodies.clear()
        self._edges.clear()
        self._refs.clear()
        self._vectors.clear()

    # ---------------- 私有 ----------------

    @staticmethod
    def _l2(a: list[float], b: list[float]) -> float:
        """L2 距离；维度不一致按短者截断（防御，正常不应发生）。"""
        n = min(len(a), len(b))
        return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(n)))
