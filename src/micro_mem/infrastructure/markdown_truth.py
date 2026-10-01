"""TruthStore 的 Markdown 实现：知识/锚点的 md 文件 IO 唯一收口（D2）。

红线：frontmatter 字段与顺序和旧版（api/writer.py）字节级一致——
既有 data/ 是真值档案，格式漂移会污染历史数据
（阶段5 验收：新架构对真实 data/ rebuild 后与旧索引逐节点比对）。

文件形态：
- 知识：knowledge/k-<id>_<安全标题>.md（yaml frontmatter + 空行 + body）
- 锚点：anchors/s-<yyyymmdd>-<序号>.md（定序行 frontmatter + 空行 + 正文）

游标/重同步用正则定点改写 frontmatter 文本（不重排其他字段的字节），
保真是锚点的第一职责。
"""
import glob
import os
import re
from datetime import datetime

import yaml

from ..application.ports import TruthStore
from ..domain.models import (
    Anchor,
    ExternalRef,
    Knowledge,
    KnowledgeType,
    RefType,
    Scope,
    Source,
    SourceType,
    Status,
)

_NOW = "%Y-%m-%d %H:%M:%S"


class MarkdownTruthStore(TruthStore):
    """Markdown 文件真值库：知识/锚点的唯一权威来源。"""

    def __init__(self, data_dir: str):
        """以数据根目录初始化（不识 Config——路径解析是 config 的事）。"""
        self._data_dir = data_dir
        self._knowledge_dir = os.path.join(data_dir, "knowledge")
        self._anchors_dir = os.path.join(data_dir, "anchors")
        os.makedirs(self._knowledge_dir, exist_ok=True)
        os.makedirs(self._anchors_dir, exist_ok=True)

    # ================= 知识 =================

    def save_knowledge(self, k: Knowledge) -> str:
        """保存知识：分配 id（如需）→ 补时间戳 → 写 md；标题变化导致改名时删旧文件。

        否则同 id 会残留多个 md 文件，rebuild 时 id 冲突。
        """
        if not k.id:
            k.id = self._next_knowledge_id()
        now = datetime.now().strftime(_NOW)
        if not k.created:
            k.created = now
        k.updated = now
        new_rel = self._knowledge_rel_path(k.id, k.title)
        old_rel = self._find_knowledge_file(k.id)
        if old_rel is not None and old_rel != new_rel:
            os.remove(os.path.join(self._data_dir, old_rel))
        self._write_text(os.path.join(self._data_dir, new_rel), self._render_knowledge(k))
        return k.id

    def get_knowledge(self, id: str) -> Knowledge | None:
        """按 id 定位文件并解析全文（含 body/sources/created/updated）。"""
        rel = self._find_knowledge_file(id)
        if rel is None:
            return None
        with open(os.path.join(self._data_dir, rel), encoding="utf-8") as f:
            return self._knowledge_from_text(f.read())

    def knowledge_ref(self, id: str) -> str:
        """真值引用 = 相对 data_dir 的文件路径（如 knowledge/k-0001_标题.md）。"""
        return self._find_knowledge_file(id) or ""

    def list_knowledge(self) -> list[Knowledge]:
        """全量知识（rebuild 用）；按文件名排序保证确定性。"""
        result = []
        for path in sorted(glob.glob(os.path.join(self._knowledge_dir, "k-*.md"))):
            with open(path, encoding="utf-8") as f:
                result.append(self._knowledge_from_text(f.read()))
        return result

    def delete_knowledge(self, id: str) -> bool:
        """物理删除真值文件（谨慎暴露，见端口契约 D7 注释）。"""
        rel = self._find_knowledge_file(id)
        if rel is None:
            return False
        os.remove(os.path.join(self._data_dir, rel))
        return True

    # ---------------- 知识：私有 ----------------

    def _knowledge_rel_path(self, kid: str, title: str) -> str:
        """知识 md 相对路径：knowledge/k-<id>_<安全标题>.md（安全规则与旧版一致）。"""
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        return os.path.join("knowledge", f"{kid}_{safe_title}.md")

    def _find_knowledge_file(self, kid: str) -> str | None:
        """按 id 前缀定位知识文件，返回相对路径（无则 None）。"""
        pattern = os.path.join(self._knowledge_dir, f"{kid}_*.md")
        matches = sorted(glob.glob(pattern))
        if not matches:
            return None
        return os.path.relpath(matches[0], self._data_dir)

    def _next_knowledge_id(self) -> str:
        """k-<序号>：扫 knowledge 目录取最大序号 +1（四位补零），与旧版一致。"""
        max_seq = 0
        for path in glob.glob(os.path.join(self._knowledge_dir, "k-*.md")):
            m = re.match(r"k-(\d+)", os.path.basename(path))
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"k-{max_seq + 1:04d}"

    def _render_knowledge(self, k: Knowledge) -> str:
        """序列化为 md 文本（frontmatter 字段/顺序/转义与旧版字节级一致，红线）。"""
        meta = {
            "id": k.id,
            "type": k.type.value,
            "scope": k.scope.value,
            "title": k.title,
            "summary": k.summary or "",
            "sources": [s.to_dict() for s in k.sources],
            "parents": list(k.parents),
            "links": list(k.links),
            "external_refs": [r.to_dict() for r in k.external_refs],
            "status": k.status.value,
            "created": k.created,
            "updated": k.updated,
        }
        fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
        return f"---\n{fm}---\n\n{k.body or ''}"

    @staticmethod
    def _knowledge_from_text(text: str) -> Knowledge:
        """md 文本 → Knowledge（全字段回读，含旧版丢失的 created/updated）。"""
        meta, body = _parse_frontmatter(text)
        return Knowledge(
            id=meta.get("id", ""),
            type=KnowledgeType(meta.get("type", "fact")),
            scope=Scope(meta.get("scope", "domain")),
            title=meta.get("title", ""),
            summary=meta.get("summary", ""),
            body=body,
            sources=[Source(SourceType(s.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                            s.get("ref", "")) for s in (meta.get("sources") or [])],
            parents=list(meta.get("parents") or []),
            links=list(meta.get("links") or []),
            external_refs=[ExternalRef(RefType(r["type"]), r["value"])
                           for r in (meta.get("external_refs") or [])],
            status=Status(meta.get("status", "draft")),
            created=str(meta.get("created", "")),
            updated=str(meta.get("updated", "")))

    # ================= 锚点 =================

    def save_anchor(self, a: Anchor) -> str:
        """保存锚点：id 空则分配 s-<当日>-<序号>；frontmatter 定序行 + 空行 + 正文。"""
        if not a.id:
            a.id = self._next_anchor_id(datetime.now().strftime("%Y%m%d"))
        if not a.date:
            a.date = datetime.now().strftime("%Y-%m-%d")
        lines = [
            "---",
            f"id: {a.id}",
            f"title: {a.title}",
            f"date: {a.date}",
            f"distilled_until: {a.distilled_until}",
        ]
        if a.source:
            lines.append(f"source: {a.source}")
        lines.append("---")
        lines.append("")
        lines.append(a.content)
        self._write_text(os.path.join(self._anchors_dir, f"{a.id}.md"), "\n".join(lines))
        return a.id

    def get_anchor(self, id: str) -> Anchor | None:
        """按 id 读锚点（frontmatter + 正文全文）。"""
        path = os.path.join(self._anchors_dir, f"{id}.md")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            meta, content = _parse_frontmatter(f.read())
        return Anchor(
            id=meta.get("id", id),
            title=meta.get("title", ""),
            date=str(meta.get("date", "")),
            content=content,
            source=meta.get("source", "") or "",
            distilled_until=self._read_cursor(path))

    def list_anchors(self) -> list[Anchor]:
        """全量锚点（按文件名排序）。"""
        result = []
        for path in sorted(glob.glob(os.path.join(self._anchors_dir, "s-*.md"))):
            anchor_id = os.path.basename(path)[:-3]
            anchor = self.get_anchor(anchor_id)
            if anchor is not None:
                result.append(anchor)
        return result

    def anchor_exists(self, ref: str) -> bool:
        """兼容纯 id / 引用路径 / 任意前缀路径（取 basename 归一化）。"""
        anchor_id = ref.replace("\\", "/").split("/")[-1]
        if anchor_id.endswith(".md"):
            anchor_id = anchor_id[:-3]
        return os.path.exists(os.path.join(self._anchors_dir, f"{anchor_id}.md"))

    def resync_anchor(self, id: str, content: str) -> bool:
        """重同步锚点正文：frontmatter 字节不动，只换正文区。"""
        path = os.path.join(self._anchors_dir, f"{id}.md")
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"^(---\n.*?\n---\n)\n?(.*)$", text, flags=re.S)
        if not m:
            return False
        self._write_text(path, m.group(1) + "\n" + content)
        return True

    def get_distill_cursor(self, anchor_id: str) -> int:
        """读蒸馏游标；锚点不存在或字段缺失/非法返回 -1。"""
        path = os.path.join(self._anchors_dir, f"{anchor_id}.md")
        if not os.path.exists(path):
            return -1
        return self._read_cursor(path)

    def set_distill_cursor(self, anchor_id: str, turn: int) -> None:
        """定点改写游标行（正则替换，其余字节不动）；字段缺失则插入 frontmatter 首行后。"""
        path = os.path.join(self._anchors_dir, f"{anchor_id}.md")
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new = re.sub(r"^distilled_until:\s*\S+", f"distilled_until: {int(turn)}",
                     text, flags=re.M)
        if new == text:
            new = text.replace("---\n", f"---\ndistilled_until: {int(turn)}\n", 1)
        self._write_text(path, new)

    # ---------------- 锚点：私有 ----------------

    def _next_anchor_id(self, date: str) -> str:
        """s-<date>-<序号>：取当日最大序号 +1（三位补零），与旧版一致。"""
        max_seq = 0
        pattern = os.path.join(self._anchors_dir, f"s-{date}-*.md")
        for path in glob.glob(pattern):
            m = re.match(rf"s-{date}-(\d+)", os.path.basename(path))
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"s-{date}-{max_seq + 1:03d}"

    @staticmethod
    def _read_cursor(path: str) -> int:
        """从锚点文件读 distilled_until（正则定点读，不整体 yaml 解析）。"""
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^distilled_until:\s*(\S+)", text, flags=re.M)
        if not m:
            return -1
        try:
            return int(m.group(1))
        except ValueError:
            return -1

    # ---------------- 通用私有 ----------------

    @staticmethod
    def _write_text(path: str, content: str) -> None:
        """写文本文件（确保父目录存在）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 md 文本：返回 (meta, body)；无 frontmatter 返回 ({}, 全文)。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body
