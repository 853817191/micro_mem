"""应用层端口（ABC）：用例声明"我需要什么样的外部世界"，基础设施去实现（D1 依赖倒置）。

三个端口：
- TruthStore  真值存取：知识/锚点的唯一权威来源（当前实现 = Markdown 文件）
- IndexStore  索引存取：检索/图/向量（可从真值重建，不是权威，随时可弃）
- Embedder    文本向量化（语义检索的输入）

写路径一致性责任（D6）：先 truth 后 index，只在 KnowledgeService 一处协调；
读路径（D3）：index 出候选，truth 出全文，只在 SearchService 一处组装。
"""
from abc import ABC, abstractmethod

from ..domain.models import Anchor, DistillPlan, Knowledge, NodeRecord


class TruthStore(ABC):
    """真值存取端口：知识/锚点的唯一权威来源。

    - get/list 返回的对象必须含全量内容（body、sources、游标），
      调用方不再、也不得从 IndexStore 读正文（D3）。
    - id 分配归本端口（id 的形态是真值实现的一部分，如 md 文件名）。
    - 时间戳（created/updated）归本端口管理：created 首次保存时写入，
      updated 每次保存刷新。
    """

    # ---------------- 知识 ----------------

    @abstractmethod
    def save_knowledge(self, k: Knowledge) -> str:
        """保存知识：id 为空则分配新 id，否则按 id 覆盖（upsert）。返回 id。

        create 与 update 共用此入口——"哪些字段变了"是应用层的 diff 结果，
        真值层只负责完整落盘。
        """

    @abstractmethod
    def get_knowledge(self, id: str) -> Knowledge | None:
        """按 id 取知识全文（含 body + sources）；不存在返回 None。"""

    @abstractmethod
    def knowledge_ref(self, id: str) -> str:
        """知识的真值引用（写入 NodeRecord.file / SearchHit.file，供下钻与重建定位）。

        形态由实现定义（Markdown 实现 = 相对 data_dir 的路径，如 knowledge/k-0001_x.md）；
        不存在返回空串。
        """

    @abstractmethod
    def list_knowledge(self) -> list[Knowledge]:
        """取全部知识（rebuild 用：索引是可真值重建的投影）。"""

    @abstractmethod
    def delete_knowledge(self, id: str) -> bool:
        """物理删除真值。返回是否存在并已删除。

        谨慎暴露（D7）：常规 delete 语义 = 只移出索引工作集、真值档案保留，
        由 KnowledgeService 保证不调用本方法；本方法仅为"彻底销毁"预留。
        """

    # ---------------- 锚点 ----------------

    @abstractmethod
    def save_anchor(self, a: Anchor) -> str:
        """保存锚点：id 为空则分配（s-<yyyymmdd>-<序号>）。返回 id。"""

    @abstractmethod
    def get_anchor(self, id: str) -> Anchor | None:
        """按 id 取锚点（含正文与蒸馏游标）；不存在返回 None。"""

    @abstractmethod
    def list_anchors(self) -> list[Anchor]:
        """取全部锚点。"""

    @abstractmethod
    def search_anchors(self, query: str, limit: int = 10) -> list[Anchor]:
        """锚点检索：title/正文子串匹配（主题驱动的相关锚点清单供给）。

        先具体后抽象：锚点规模小（几十个），子串扫足够；规模上来再升 FTS 表。
        """

    @abstractmethod
    def anchor_exists(self, ref: str) -> bool:
        """溯源校验：ref 指向的锚点是否存在。

        ref 兼容两种形态：纯 id（s-20260929-002）或引用路径（anchors/s-20260929-002.md）。
        """

    @abstractmethod
    def resync_anchor(self, id: str, content: str) -> bool:
        """重同步锚点正文（会话增长时更新保真全文），保留游标等元数据。

        返回锚点是否存在并已更新。
        """

    @abstractmethod
    def get_distill_cursor(self, anchor_id: str) -> int:
        """读蒸馏游标（已蒸馏到的最后轮次号）；锚点不存在返回 -1。"""

    @abstractmethod
    def set_distill_cursor(self, anchor_id: str, turn: int) -> None:
        """写蒸馏游标。锚点不存在时静默忽略（编排层先 get_anchor 确认存在）。"""

    # ---------------- 蒸馏计划档案 ----------------

    @abstractmethod
    def save_plan(self, p: DistillPlan) -> str:
        """保存蒸馏计划：plan_id 为空则分配（plan-<yyyymmdd>-<seq>），否则按 id 覆盖。

        计划是过程档案（负知识载体 + 审计链），与知识/锚点同归真值层管理——
        data/ 下一切文件读写收口径不变（真值没有家是旧病，不复发）。返回 plan_id。
        """

    @abstractmethod
    def get_plan(self, plan_id: str) -> DistillPlan | None:
        """按 id 取计划；不存在返回 None。"""

    @abstractmethod
    def list_plans(self) -> list[DistillPlan]:
        """取全部计划（审计 / 反馈回路统计用）。"""


class SourceParser(ABC):
    """素材解析端口（S0）：任意输入格式 → 统一 turn 序列。

    蒸馏协议的唯一入口。每种输入格式一个实现（jsonl/md/html/url…），
    输出统一 [(轮次号, 轮内文本)]：轮次号从 1 连续；轮内文本含 role 段落头
    （文档类输入整轮标 "## user"）。turn 序列由 anchor_format 唯一序列化成
    锚点正文——parser 不拼格式、不碰存储。
    零轮（空素材）由实现返回 []，调用方拒收。
    """

    @abstractmethod
    def supports(self, source: str) -> bool:
        """该 source（文件路径或 URL）是否归本解析器处理。"""

    @abstractmethod
    def parse_turns(self, source: str) -> list[tuple[int, str]]:
        """解析为 turn 序列；解析失败抛 ValueError（带格式上下文）。"""


class IndexStore(ABC):
    """索引端口：检索/图/向量的存取。

    - 索引是真值的投影，随时可由 RebuildService 从 TruthStore 全量重建；
      任何调用方不把索引内容（含 body 副本）当数据源（D3）。
    - rowid 等物理键收回实现内部，不出现在 NodeRecord（领域模型无感知）。
    - D7：常规 delete 只调本端口的 delete_node（移出索引工作集），
      不动真值；rebuild 会按真值全量恢复。想彻底消失：deprecate 或删真值。
    """

    # ---------------- 节点 ----------------

    @abstractmethod
    def upsert_node(self, node: NodeRecord, body: str = "") -> None:
        """写入/更新节点（合一入口，全量语义）：node 按 id 覆盖，body 整体替换。

        调用方必须给出完整 body（从 TruthStore 读到的全量）——
        "缺省 = 不变"的暧昧不存在于端口层，B2 类数据丢失从结构上根除。
        """

    @abstractmethod
    def delete_node(self, id: str) -> None:
        """删除节点：连带其边 / 外部锚点 / 向量。"""

    @abstractmethod
    def get_node(self, id: str) -> NodeRecord | None:
        """按 id 取节点（不含 body——body 全文走 TruthStore）。"""

    # ---------------- 检索 ----------------

    @abstractmethod
    def search_keyword(self, query: str, limit: int = 10,
                       type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """关键词检索：字面匹配 title/summary/body，可按 type/scope 过滤。"""

    @abstractmethod
    def search_semantic(self, query_vec: list[float], limit: int = 10,
                        type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """语义检索：向量最近邻，可按 type/scope 过滤。

        无向量能力或无结果的实现返回空列表（语义兜底策略由 SearchService 按配置决定，D8）。
        """

    @abstractmethod
    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[str] | None = None) -> list[tuple[str, str, int]]:
        """图遍历：从 start_id 沿边双向扩展 N 跳。

        返回 (节点id, 边类型, 跳数)，不含起点，按跳数升序；同一节点只出现一次。
        """

    # ---------------- 边 ----------------

    @abstractmethod
    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """建边（parent / link / trace），重复建幂等。"""

    @abstractmethod
    def remove_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """删边（不存在则静默）。"""

    @abstractmethod
    def get_edges(self, node_id: str, edge_types: list[str] | None = None) -> list[tuple[str, str]]:
        """取节点所有出边（to_id, edge_type），可过滤边类型。"""

    @abstractmethod
    def get_all_edges(self) -> list[tuple[str, str, str]]:
        """取全部边（from_id, to_id, edge_type），可视化/统计用。"""

    @abstractmethod
    def has_parent_edge(self, node_id: str) -> bool:
        """节点是否有 parent 入边（被其他节点挂靠 = 聚合主题）。"""

    # ---------------- 外部锚点 ----------------

    @abstractmethod
    def add_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """挂外部锚点（idev / mr / uat / url），重复挂幂等。"""

    @abstractmethod
    def remove_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """移除外部锚点（不存在则静默）。"""

    @abstractmethod
    def get_external_refs(self, node_id: str) -> list[tuple[str, str]]:
        """取节点所有外部锚点（ref_type, ref_value）。"""

    # ---------------- 向量 / 重建 ----------------

    @abstractmethod
    def save_vector(self, node_id: str, embedding: list[float]) -> None:
        """存节点向量（节点不存在则静默忽略）。"""

    @abstractmethod
    def get_all_nodes(self) -> list[NodeRecord]:
        """取全部节点（可视化/统计用）。"""

    @abstractmethod
    def clear_all(self) -> None:
        """清空全部索引（rebuild 的第一步；不动真值）。"""


class Embedder(ABC):
    """文本向量化端口。"""

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """文本 → 向量。"""
