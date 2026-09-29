"""业务写接口 MemoryWriter：数据进系统（新增/更新）。

职责：保证 md 真值 + 五张表一致性（写入的唯一入口）。
编码约定：主方法从上到下编排子方法，每个子方法单一职责 + 注释。
"""
import glob
import os
import re
from datetime import datetime

from ..common.config import Config
from ..domain.types import (
    EdgeType,
    ExternalRef,
    Knowledge,
    KnowledgeType,
    RefType,
    Scope,
    Status,
)
from ..store.base import NetworkStore, NodeRecord


class MemoryWriter:
    """写接口：面向四操作（记忆/蒸馏/收录/关联）的写入口。"""

    def __init__(self, config: Config, store: NetworkStore, embedder=None):
        """依赖注入：配置 + 引擎实现 + 向量化器（可选，用于语义检索）。"""
        self.config = config
        self.store = store
        self.embedder = embedder
        os.makedirs(config.anchors_dir(), exist_ok=True)
        os.makedirs(config.knowledge_dir(), exist_ok=True)

    # ================= 记忆：锚点 =================

    def save_anchor(self, content: str, title: str | None = None, source: str | None = None) -> str:
        """记忆：对话 → 锚点文件（保真，不可变）。返回 anchor_id。

        source: 会话 jsonl 源路径（供增量蒸馏定位增量轮次）。
        """
        anchor_id = self._generate_anchor_id()     # 1. 生成锚点 id
        title = title or f"会话 {anchor_id}"
        self._write_anchor_file(anchor_id, title, content, source=source)  # 2. 写锚点文件
        return anchor_id

    def _generate_anchor_id(self) -> str:
        """生成锚点 id：s-<日期>-<序号>（取今日最大序号 +1，避免数量不连续时覆盖旧锚点）。"""
        date = datetime.now().strftime("%Y%m%d")
        max_seq = 0
        pattern = os.path.join(self.config.anchors_dir(), f"s-{date}-*.md")
        for path in glob.glob(pattern):
            m = re.match(rf"s-{date}-(\d+)", os.path.basename(path))
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"s-{date}-{max_seq + 1:03d}"

    def _write_anchor_file(
            self, anchor_id: str, title: str, content: str, source: str | None = None) -> None:
        """写锚点文件：frontmatter（id/title/date/distilled_until/source）+ 对话正文（保真）。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        lines = [
            "---",
            f"id: {anchor_id}",
            f"title: {title}",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            "distilled_until: -1",   # 蒸馏游标：已蒸馏到的最后轮次号（-1 = 未蒸馏）
        ]
        if source:
            lines.append(f"source: {source}")
        lines.append("---")
        lines.append("")
        lines.append(content)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ================= 蒸馏游标（增量蒸馏防重复） =================

    def resync_anchor(self, anchor_id: str, content: str) -> bool:
        """重同步锚点正文（会话增长时更新保真全文），保留蒸馏游标等元数据。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^(---\n.*?\n---\n)\n?(.*)$", text, flags=re.S)
        if not m:
            return False
        with open(path, "w", encoding="utf-8") as f:
            f.write(m.group(1) + "\n" + content)
        return True

    def mark_distilled(self, anchor_id: str, until_turn: int) -> bool:
        """更新锚点蒸馏游标：distilled_until = until_turn。返回锚点是否存在并已更新。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new = re.sub(r"^distilled_until:\s*\S+", f"distilled_until: {int(until_turn)}",
                     text, flags=re.M)
        if new == text:  # 字段缺失，插入 frontmatter 首行后
            new = text.replace("---\n", f"---\ndistilled_until: {int(until_turn)}\n", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return True

    def get_anchor_distilled_until(self, anchor_id: str) -> int:
        """读锚点蒸馏游标；锚点不存在或字段缺失返回 -1。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        if not os.path.exists(path):
            return -1
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^distilled_until:\s*(\S+)", text, flags=re.M)
        if not m:
            return -1
        try:
            return int(m.group(1))
        except ValueError:
            return -1

    def get_anchor_source(self, anchor_id: str) -> str:
        """读锚点源 jsonl 路径；无则返回空。"""
        path = os.path.join(self.config.anchors_dir(), f"{anchor_id}.md")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^source:\s*(.+)$", text, flags=re.M)
        return m.group(1).strip() if m else ""

    # ================= 蒸馏/收录：知识 =================

    def create_knowledge(self, k: Knowledge, skip_md: bool = False) -> str:
        """蒸馏/收录：写一条知识。主流程：校验→补元数据→写真值→同步索引→建边→外部锚点。

        skip_md=True 时跳过写真值文件（rebuild 用：md 已存在，只重建索引，省 IO）。
        """
        self._validate(k)                # 1. 校验参数
        self._assign_meta(k)             # 2. 补全 id/时间
        if not skip_md:
            self._write_md_file(k)       # 3. 写真值文件（确定 file 路径）
        self._sync_indexes(k)            # 4. 同步 nodes + FTS（事务，rowid 对齐）
        self._build_edges(k)             # 5. 建关联边（parents/links）
        self._link_external_refs(k)      # 6. 挂外部锚点
        return k.id

    def _validate(self, k: Knowledge) -> None:
        """校验参数：必填字段。"""
        if not k.title:
            raise ValueError("title 不能为空")
        if k.type is None or k.scope is None:
            raise ValueError("type/scope 必填")

    def _assign_meta(self, k: Knowledge) -> None:
        """补全元数据：id 缺省则生成，并写 created/updated 时间。"""
        if not k.id:
            k.id = self._generate_knowledge_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        k.created = now
        k.updated = now

    def _generate_knowledge_id(self) -> str:
        """生成知识 id：k-<序号>（扫描 knowledge 目录取最大序号 +1）。"""
        max_seq = 0
        pattern = os.path.join(self.config.knowledge_dir(), "k-*.md")
        for path in glob.glob(pattern):
            m = re.match(r"k-(\d+)", os.path.basename(path))
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"k-{max_seq + 1:04d}"

    def _file_path(self, k: Knowledge) -> str:
        """知识 md 文件相对路径：knowledge/k-{id}_{title}.md。"""
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", k.title)
        return os.path.join("knowledge", f"{k.id}_{safe_title}.md")

    def _write_md_file(self, k: Knowledge) -> None:
        """写真值文件：frontmatter（全部元数据）+ 正文。"""
        path = os.path.join(self.config.data_dir, self._file_path(k))
        lines = [
            "---",
            f"id: {k.id}",
            f"type: {k.type.value}",
            f"scope: {k.scope.value}",
            f"title: {k.title}",
            f"summary: {k.summary or ''}",
        ]
        if k.sources:
            lines.append("sources:")
            for s in k.sources:
                lines.append(f"  - type: {s.type.value}")
                if s.ref:
                    lines.append(f"    ref: {s.ref}")
        else:
            lines.append("sources: []")
        lines.append(f"parents: {list(k.parents)}")
        lines.append(f"links: {list(k.links)}")
        if k.external_refs:
            lines.append("external_refs:")
            for r in k.external_refs:
                lines.append(f"  - type: {r.type.value}")
                lines.append(f"    value: {r.value}")
        lines.append(f"status: {k.status.value}")
        lines.append(f"created: {k.created}")
        lines.append(f"updated: {k.updated}")
        lines.append("---")
        lines.append("")
        lines.append(k.body or "")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _sync_indexes(self, k: Knowledge) -> None:
        """同步索引：nodes + FTS + 向量（rowid 对齐由引擎内部完成，业务层不感知）。"""
        node = NodeRecord(
            id=k.id, file=self._file_path(k), title=k.title, summary=k.summary,
            type=k.type.value, scope=k.scope.value, status=k.status.value,
            created=k.created, updated=k.updated)
        self.store.create_node(node, body=k.body)
        # 向量：对 summary 算 embedding（有向量化器且 summary 非空时）
        if self.embedder is not None and k.summary:
            self.store.save_vector(k.id, self.embedder.embed(k.summary))

    def _build_edges(self, k: Knowledge) -> None:
        """建关联边：parents → parent 边（挂靠）、links → link 边（引用）。"""
        for p in k.parents:
            self.store.add_edge(k.id, p, EdgeType.PARENT.value)
        for link in k.links:
            self.store.add_edge(k.id, link, EdgeType.LINK.value)

    def _link_external_refs(self, k: Knowledge) -> None:
        """挂外部锚点：external_refs → external_refs 表。"""
        for r in k.external_refs:
            self.store.add_external_ref(k.id, r.type.value, r.value)

    # ================= 更新 =================

    def update_knowledge(self, id: str, **changes) -> None:
        """更新知识：只改指定字段（缺省/None = 不变）。
        主流程：校验存在→更新 nodes/FTS→同步边→同步外部锚点→重写真值。"""
        if self.store.get_node(id) is None:
            raise KeyError(f"节点不存在: {id}")
        self._apply_meta_update(id, changes)   # 1. 更新 nodes + FTS（含 body）
        self._apply_edge_changes(id, changes)  # 2. 同步边（parents/links 全量替换）
        self._apply_ref_changes(id, changes)   # 3. 同步外部锚点
        self._rewrite_md_file(id)              # 4. 重写真值文件

    def _apply_meta_update(self, id: str, changes: dict) -> None:
        """更新 nodes 元数据 + FTS 正文；字段缺省/None 保持原值（旧 body 由引擎内部读）。"""
        rec = self.store.get_node(id)
        if rec is None:
            raise KeyError(f"节点不存在: {id}")
        new_body = changes["body"] if changes.get("body") is not None else ""
        node = NodeRecord(
            id=id,
            file=changes.get("file", rec.file) or rec.file,
            title=self._changed(changes, "title", rec.title),
            summary=self._changed(changes, "summary", rec.summary),
            type=self._enum_str(changes, "type", rec.type, KnowledgeType),
            scope=self._enum_str(changes, "scope", rec.scope, Scope),
            status=self._enum_str(changes, "status", rec.status, Status),
            updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.store.update_node(node, new_body=new_body)

    def _apply_edge_changes(self, id: str, changes: dict) -> None:
        """同步边：changes 含 parents/links 时全量替换对应边。"""
        if "parents" in changes and changes["parents"] is not None:
            self._replace_edges(id, EdgeType.PARENT, changes["parents"])
        if "links" in changes and changes["links"] is not None:
            self._replace_edges(id, EdgeType.LINK, changes["links"])

    def _replace_edges(self, id: str, edge_type: EdgeType, new_targets: list) -> None:
        """全量替换某类边：先删旧的，再建新的。"""
        for to_id, _ in self.store.get_edges(id, [edge_type.value]):
            self.store.remove_edge(id, to_id, edge_type.value)
        for target in new_targets:
            self.store.add_edge(id, target, edge_type.value)

    def _apply_ref_changes(self, id: str, changes: dict) -> None:
        """同步外部锚点：changes 含 external_refs 时全量替换。"""
        if "external_refs" not in changes or changes["external_refs"] is None:
            return
        for ref_type, ref_value in self.store.get_external_refs(id):
            self.store.remove_external_ref(id, ref_type, ref_value)
        for r in changes["external_refs"]:
            self.store.add_external_ref(id, r.type.value, r.value)

    def _rewrite_md_file(self, id: str) -> None:
        """重写真值文件：从 store 读回完整状态重新生成 md；文件名变化时删除旧文件。

        否则同 id 会残留多个 md 文件，导致 rebuild 时 id 冲突。
        """
        rec = self.store.get_node(id)
        if rec is None:
            raise KeyError(f"节点不存在: {id}")
        old_file = rec.file
        new_k = self._build_knowledge_from_store(id)
        self._write_md_file(new_k)
        new_file = self._file_path(new_k)
        if new_file != old_file:
            old_path = os.path.join(self.config.data_dir, old_file)
            if os.path.exists(old_path):
                os.remove(old_path)

    def _build_knowledge_from_store(self, id: str) -> Knowledge:
        """从 store 读回节点 + 边 + 外部锚点，构造 Knowledge（重写 md 用）。"""
        rec = self.store.get_node(id)
        if rec is None:
            raise KeyError(f"节点不存在: {id}")
        parents = [t for t, et in self.store.get_edges(id, [EdgeType.PARENT.value])]
        links = [t for t, et in self.store.get_edges(id, [EdgeType.LINK.value])]
        refs = [ExternalRef(RefType(rt), rv)
                for rt, rv in self.store.get_external_refs(id)]
        return Knowledge(
            type=KnowledgeType(rec.type), scope=Scope(rec.scope), title=rec.title,
            summary=rec.summary, body=self.store.get_fts_body(id),
            parents=parents, links=links, external_refs=refs,
            status=Status(rec.status), id=rec.id,
            created=rec.created, updated=rec.updated)

    # ================= 废弃 =================

    def deprecate_knowledge(self, id: str) -> None:
        """废弃：status → deprecated（留痕不删）。"""
        self.update_knowledge(id, status=Status.DEPRECATED)

    # ================= 删除 =================

    def delete_knowledge(self, id: str) -> bool:
        """删除节点：级联清除其所有边 / 外部锚点 / FTS / 向量索引。

        返回节点是否真实存在并已删除（不存在返回 False）。
        """
        if self.store.get_node(id) is None:
            return False
        self.store.delete_node(id)
        return True

    # ================= 关联 / 外部锚点（直接转发引擎） =================

    def add_edge(self, from_id: str, to_id: str, edge_type: EdgeType) -> None:
        """建边。"""
        self.store.add_edge(from_id, to_id, edge_type.value)

    def remove_edge(self, from_id: str, to_id: str, edge_type: EdgeType) -> None:
        """删边。"""
        self.store.remove_edge(from_id, to_id, edge_type.value)

    def add_external_ref(self, node_id: str, ref_type: RefType, ref_value: str) -> None:
        """挂外部锚点。"""
        self.store.add_external_ref(node_id, ref_type.value, ref_value)

    def remove_external_ref(self, node_id: str, ref_type: RefType, ref_value: str) -> None:
        """移除外部锚点。"""
        self.store.remove_external_ref(node_id, ref_type.value, ref_value)

    # ================= 私有工具 =================

    @staticmethod
    def _changed(changes: dict, key: str, default: str) -> str:
        """取变更字段：缺省或 None 返回原值。"""
        return changes[key] if key in changes and changes[key] is not None else default

    @staticmethod
    def _enum_str(changes: dict, key: str, default: str, enum_cls) -> str:
        """取变更的枚举字段并转 value 字符串。"""
        v = changes[key] if key in changes and changes[key] is not None else default
        return v.value if isinstance(v, enum_cls) else str(v)
