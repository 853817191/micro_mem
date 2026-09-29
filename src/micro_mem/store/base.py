"""引擎接口：NetworkStore（端口）。

契约定死、实现可插拔（sqlite / neo4j / memory）。
业务接口层（Writer/Reader）只依赖此接口，换引擎不动上层。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NodeRecord:
    """引擎层的节点记录（对应 nodes 表一行）。"""
    id: str                  # 业务主键：k-0003
    file: str                # 指向 md 真值路径
    title: str
    summary: str
    type: str                # event | method | fact（枚举 value）
    scope: str               # universal | domain | personal
    status: str = "draft"    # draft | evolving | settled | deprecated
    created: str = ""
    updated: str = ""
    rowid: int | None = None        # 物理关联键（FTS/vec 对齐用）


class NetworkStore(ABC):
    """网存储接口（端口）。实现可插拔。"""

    # ---------------- 构建 / 维护 ----------------

    @abstractmethod
    def create_node(self, node: NodeRecord, body: str = "") -> None:
        """新增节点；body 非空则同步 FTS 索引（rowid 由引擎内部对齐，不外泄）。"""

    @abstractmethod
    def update_node(self, node: NodeRecord, new_body: str = "") -> None:
        """更新节点；title/body 变化时自动同步 FTS（new_body 缺省=不变，旧 body 由引擎内部读）。"""

    @abstractmethod
    def delete_node(self, id: str) -> None:
        """删除节点（连带 FTS / edges / external_refs）。"""

    @abstractmethod
    def get_node(self, id: str) -> NodeRecord | None:
        """按 id 取节点。"""

    @abstractmethod
    def get_fts_body(self, id: str) -> str:
        """取节点的 FTS 正文（索引副本，读 body 用；无则返回空串）。"""

    # ---------------- 检索 ----------------

    @abstractmethod
    def search_keyword(self, query: str, limit: int = 10,
                       type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """关键词检索（FTS5 字面），返回节点列表。"""

    @abstractmethod
    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[str] | None = None) -> list[tuple[str, str, int]]:
        """图遍历：从 start_id 沿边扩展 N 跳，返回 (节点id, 边类型, 跳数)。"""

    # ---------------- 边 ----------------

    @abstractmethod
    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """建边（parent / link / trace）。"""

    @abstractmethod
    def remove_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """删边。"""

    @abstractmethod
    def get_edges(self, node_id: str, edge_types: list[str] | None = None) -> list[tuple[str, str]]:
        """取节点所有出边，返回 (to_id, edge_type)。"""

    @abstractmethod
    def get_all_nodes(self) -> list[NodeRecord]:
        """取全部节点（可视化/统计用）。"""

    @abstractmethod
    def get_all_edges(self) -> list[tuple[str, str, str]]:
        """取全部边（可视化用），返回 (from_id, to_id, edge_type)。"""

    @abstractmethod
    def has_parent_edge(self, node_id: str) -> bool:
        """判断节点是否有 parent 入边（其他节点挂靠它 = 聚合主题）。"""

    # ---------------- 外部锚点 ----------------

    @abstractmethod
    def add_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """挂外部锚点（idev / mr / uat / url）。"""

    @abstractmethod
    def remove_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """移除外部锚点。"""

    @abstractmethod
    def get_external_refs(self, node_id: str) -> list[tuple[str, str]]:
        """取节点所有外部锚点，返回 (ref_type, ref_value)。"""

    # ---------------- 向量检索 ----------------

    @abstractmethod
    def save_vector(self, node_id: str, embedding: list) -> None:
        """存向量（引擎内部按 node_id 定位 rowid 关联 nodes）。"""

    @abstractmethod
    def search_semantic(self, query_vec: list, limit: int = 10,
                        type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """语义检索：向量最近邻。"""

    # ---------------- 重建 ----------------

    @abstractmethod
    def clear_all(self) -> None:
        """清空所有表数据（rebuild 用，从真值文件重建）。"""
