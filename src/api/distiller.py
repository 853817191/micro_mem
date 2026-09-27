"""业务蒸馏接口 Distiller：AI 产出候选 → 用户 review → 确认入库。

流程：你说"帮我蒸馏" → AI 读对话（当前上下文或锚点）整体整理 → 产出候选清单
      → 你 review 勾选（KEEP/EDIT/REJECT）→ confirm_distill 落库 + 建关联。
第一原则：蒸馏的"提炼判断"是 AI 做的（模型），工具负责落库与检索。
"""
import os

from ..config import Config
from ..store.base import NetworkStore
from ..types import Decision, EdgeType, Knowledge, SourceType


class Distiller:
    """蒸馏接口：distill 读取对话供 AI 提炼，confirm_distill 落库。"""

    def __init__(self, config: Config, store: NetworkStore, writer, reader):
        """依赖注入：配置 + 引擎 + 写接口 + 读接口。"""
        self.config = config
        self.store = store
        self.writer = writer
        self.reader = reader

    # ================= 蒸馏（AI 提炼） =================

    def distill(self, anchor_id: str = None, topic: str = None) -> str:
        """读取对话（锚点），返回对话文本供 AI 提炼候选。

        - anchor_id 为空：当前上下文由调用方（Claude 会话）直接提供
        - anchor_id 指定：读取锚点文件的完整对话（早期/存量）
        实际 AI 提炼由调用方完成；工具负责提供上下文与落库。
        """
        if anchor_id:
            return self._read_anchor(anchor_id)
        return ""

    def _read_anchor(self, anchor_id: str) -> str:
        """读锚点文件内容（保真对话）。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return ""

    # ================= 确认入库 =================

    def confirm_distill(self, candidates: list) -> list[str]:
        """用户确认后的候选落库：KEEP/EDIT → create_knowledge + 建关联；REJECT → 跳过。

        幂等：同 title 且 sources.ref 有交集的已存在知识 → 跳过，返回已有 id。
        溯源校验：sources.ref 指向锚点的候选，锚点文件必须存在，否则抛错（不落库）。
        返回入库（含幂等命中）的知识 id 列表。
        """
        created_ids = []
        for c in candidates:
            if c.decision == Decision.REJECT:
                continue
            existing = self._find_existing(c)    # 1. 幂等去重
            if existing:
                created_ids.append(existing)
                continue
            self._validate_sources(c)            # 2. 溯源校验（ref → 真实锚点）
            k = self._to_knowledge(c)            # 3. 候选 → 知识对象
            kid = self.writer.create_knowledge(k)  # 4. 落库（md + 索引 + 向量）
            self._build_suggested_edges(kid, c)   # 5. 按确认的关联建议建边
            created_ids.append(kid)
        return created_ids

    def _find_existing(self, c) -> str:
        """幂等：同 title 且 sources.ref 有交集 → 返回已有知识 id，否则空字符串。"""
        refs = {s.ref for s in c.sources if s.ref}
        if not c.title or not refs:
            return ""
        for rec in self.store.get_all_nodes():
            try:
                k = self.reader.get(rec.id)
            except Exception:
                continue
            if k and k.title == c.title:
                krefs = {s.ref for s in k.sources}
                if refs & krefs:
                    return k.id
        return ""

    def _validate_sources(self, c) -> None:
        """溯源校验：指向锚点的 sources.ref 必须存在对应锚点文件。"""
        for s in c.sources:
            ref = (s.ref or "").strip()
            if not ref:
                continue
            norm = ref.replace("\\", "/")
            last = norm.split("/")[-1]
            # 仅校验看起来指向锚点的 ref（含 anchors/ 或以 s- 开头）
            if "anchors/" not in norm and not last.startswith("s-"):
                continue
            anchor_id = last.replace(".md", "")
            path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
            if not os.path.exists(path):
                raise ValueError(
                    f"候选「{c.title}」的 sources.ref 指向不存在的锚点: {ref}")

    def _to_knowledge(self, c) -> Knowledge:
        """候选 → Knowledge（sources 原样带，parents/links 用建议值）。"""
        return Knowledge(
            type=c.type, scope=c.scope, title=c.title,
            summary=c.summary, body=c.body,
            sources=c.sources,
            parents=c.suggested_parents,
            links=c.suggested_links)

    def _build_suggested_edges(self, kid: str, c) -> None:
        """按确认的关联建议建边：suggested_parents → parent、suggested_links → link。"""
        for p in c.suggested_parents:
            self.writer.add_edge(kid, p, EdgeType.PARENT)
        for link in c.suggested_links:
            self.writer.add_edge(kid, link, EdgeType.LINK)
