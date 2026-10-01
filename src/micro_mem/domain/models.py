"""领域模型：纯 dataclass + 枚举，零依赖。

约定：
- 业务层用枚举（类型安全）；序列化用枚举 value（字符串）
- to_dict / from_dict 全字段对称：X.from_dict(x.to_dict()) == x
- 领域对象不知道自己怎么变成 YAML frontmatter / SQLite 行——
  序列化是基础设施的实现细节（架构决策 D2）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ==================== 枚举 ====================


class KnowledgeType(Enum):
    """认识论类型：回答什么问题（知识的内容形态）。"""
    EVENT = "event"              # 发生了什么——一件事的完整过程
    MODEL = "model"              # 这是什么系统/领域——领域模型（结构/状态机/主流程/生命周期）
    FACT = "fact"                # 是什么（单条）——一条规则/约束/结论
    METHOD = "method"            # 怎么办——一套做法/流程


class Scope(Enum):
    """成立范围：在哪里成立（谱系：个人→领域→普世）。"""
    UNIVERSAL = "universal"      # 普世
    DOMAIN = "domain"            # 领域 / 项目组
    PERSONAL = "personal"        # 个人


class Status(Enum):
    """成熟度：短期确定、长期演进。"""
    DRAFT = "draft"              # 新建未确认
    EVOLVING = "evolving"        # 演进中
    SETTLED = "settled"          # 已确认
    DEPRECATED = "deprecated"    # 已废弃（留痕不删）


class EdgeType(Enum):
    """网的关系类型。"""
    PARENT = "parent"            # 挂靠（父子/包含）
    LINK = "link"                # 关联（引用）
    TRACE = "trace"              # 溯源（回锚点）


class RefType(Enum):
    """外部引用类型。"""
    IDEV = "idev"                # 需求单号
    MR = "mr"                    # MR 链接
    UAT = "uat"                  # UAT 地址
    URL = "url"                  # 通用 URL


class SourceType(Enum):
    """知识来源类型（可扩展接口的当前实现）。"""
    USER_DECLARED = "user_declared"               # 用户主动告知
    CONVERSATION_DISTILLED = "conversation_distilled"  # 对话蒸馏


class Decision(Enum):
    """蒸馏候选的 review 结果。"""
    KEEP = "keep"                # 保留
    EDIT = "edit"                # 修改后保留
    REJECT = "reject"            # 不要


# 语义兜底策略合法值（D8，领域语言；config 解析与 SearchService 共用）
SEMANTIC_FALLBACKS = ("on_zero_hit", "always", "off")


# ==================== 值对象 ====================


@dataclass
class Source:
    """知识来源：类型 + 锚点引用。"""
    type: SourceType
    ref: str = ""

    def to_dict(self) -> dict:
        """转 dict（与 from_dict 对称）。"""
        return {"type": self.type.value, "ref": self.ref}

    @classmethod
    def from_dict(cls, d: dict) -> Source:
        """从 dict 还原。"""
        return cls(SourceType(d.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                   d.get("ref", ""))


@dataclass
class ExternalRef:
    """外部锚点：知识 ↔ 真实世界。"""
    type: RefType
    value: str

    def to_dict(self) -> dict:
        """转 dict。"""
        return {"type": self.type.value, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> ExternalRef:
        """从 dict 还原。"""
        return cls(RefType(d["type"]), d["value"])


# ==================== 知识 / 锚点 ====================


@dataclass
class Knowledge:
    """知识本体（收录/蒸馏/更新的主体）。

    id 为空 = 待 TruthStore 分配；created/updated 由 TruthStore 管理。
    """
    type: KnowledgeType
    scope: Scope
    title: str
    summary: str = ""
    body: str = ""
    sources: list[Source] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    external_refs: list[ExternalRef] = field(default_factory=list)
    status: Status = Status.DRAFT
    id: str = ""
    created: str = ""
    updated: str = ""

    def to_dict(self) -> dict:
        """转 dict（全字段，与 from_dict 对称）。

        落盘时哪些字段进 frontmatter 由 TruthStore 实现决定（D2）。
        """
        return {
            "id": self.id,
            "type": self.type.value,
            "scope": self.scope.value,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "sources": [s.to_dict() for s in self.sources],
            "parents": list(self.parents),
            "links": list(self.links),
            "external_refs": [r.to_dict() for r in self.external_refs],
            "status": self.status.value,
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Knowledge:
        """从 dict 还原（与 to_dict 对称）。"""
        return cls(
            id=d.get("id", ""),
            type=KnowledgeType(d["type"]),
            scope=Scope(d["scope"]),
            title=d["title"],
            summary=d.get("summary", ""),
            body=d.get("body", ""),
            sources=[Source.from_dict(s) for s in (d.get("sources") or [])],
            parents=list(d.get("parents") or []),
            links=list(d.get("links") or []),
            external_refs=[ExternalRef.from_dict(r) for r in (d.get("external_refs") or [])],
            status=Status(d.get("status", Status.DRAFT.value)),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
        )


@dataclass
class Anchor:
    """锚点：对话原文保真存档（正文不可变，游标可推进）。"""
    id: str
    title: str
    date: str
    content: str
    source: str = ""            # 会话 jsonl 源路径（增量蒸馏定位增量轮次用）
    distilled_until: int = -1   # 蒸馏游标：已蒸馏到的最后轮次号（-1 = 未蒸馏）

    def to_dict(self) -> dict:
        """转 dict（全字段，与 from_dict 对称）。"""
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "content": self.content,
            "source": self.source,
            "distilled_until": self.distilled_until,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Anchor:
        """从 dict 还原。"""
        return cls(
            id=d.get("id", ""),
            title=d.get("title", ""),
            date=d.get("date", ""),
            content=d.get("content", ""),
            source=d.get("source", ""),
            distilled_until=int(d.get("distilled_until", -1)),
        )


# ==================== 蒸馏候选 ====================


@dataclass
class DistillCandidate:
    """蒸馏产出的单条候选：知识本体 + 关联建议 + review 结果。

    type/scope/title/summary/body 知识本体（AI 产出，review 可改）；
    suggested_parents/suggested_links 关联建议（AI 给出，review 可改）；
    decision=EDIT 时 edit_id 指向被更新的已有知识 id。
    """
    type: KnowledgeType
    scope: Scope
    title: str
    summary: str = ""
    body: str = ""
    suggested_parents: list[str] = field(default_factory=list)
    suggested_links: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    decision: Decision = Decision.KEEP
    edit_id: str = ""

    def to_dict(self) -> dict:
        """转 dict（便于命令行/JSON 传递候选）。"""
        return {
            "type": self.type.value,
            "scope": self.scope.value,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "suggested_parents": list(self.suggested_parents),
            "suggested_links": list(self.suggested_links),
            "sources": [s.to_dict() for s in self.sources],
            "decision": self.decision.value,
            "edit_id": self.edit_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DistillCandidate:
        """从 dict 还原（与 to_dict 对称）。"""
        return cls(
            type=KnowledgeType(d["type"]),
            scope=Scope(d["scope"]),
            title=d["title"],
            summary=d.get("summary", ""),
            body=d.get("body", ""),
            suggested_parents=list(d.get("suggested_parents") or []),
            suggested_links=list(d.get("suggested_links") or []),
            sources=[Source.from_dict(s) for s in (d.get("sources") or [])],
            decision=Decision(d.get("decision", Decision.KEEP.value)),
            edit_id=d.get("edit_id", ""),
        )


# ==================== 索引记录 / 检索候选 ====================


@dataclass
class NodeRecord:
    """索引节点记录（IndexStore 的基本行）。

    rowid 等物理键收回 IndexStore 实现内部，不进入领域模型。
    file 指向真值路径（索引节点 → 真值的链接）。
    """
    id: str                  # 业务主键：k-0003
    file: str                # 真值路径（TruthStore 实现定义其形态）
    title: str
    summary: str
    type: str                # event | model | fact | method（枚举 value）
    scope: str               # universal | domain | personal
    status: str = "draft"    # draft | evolving | settled | deprecated
    created: str = ""
    updated: str = ""


@dataclass
class SearchHit:
    """search 返回的候选条目（轻量字段，供模型判断，不读全文）。"""
    id: str
    title: str
    summary: str
    file: str                # 下钻路径
    type: KnowledgeType
    scope: Scope
    score: float = 0.0       # 融合分（多路检索时）

    def to_dict(self) -> dict:
        """转 dict（便于命令行输出 / 模型读取）。"""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "file": self.file,
            "type": self.type.value,
            "scope": self.scope.value,
            "score": round(self.score, 4),
        }
