"""核心类型：枚举 + 值对象，所有接口参数的基础。

约定：业务层用枚举（类型安全）；落盘/序列化用枚举的 value（字符串）。
"""
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


class DistillCandidate:
    """蒸馏产出的单条候选：知识本体 + 关联建议 + review 结果。"""

    def __init__(self, type: KnowledgeType, scope: Scope, title: str,
                 summary: str = "", body: str = "",
                 suggested_parents=None, suggested_links=None,
                 sources=None, decision: Decision = Decision.KEEP,
                 edit_id: str = ""):
        """蒸馏候选：type/scope/title/summary/body 知识本体（AI 产出，review 可改）；
        suggested_parents/suggested_links 关联建议（AI 给出，review 可改）；
        sources 来源；decision review 结果（KEEP/EDIT/REJECT）；
        edit_id 当 decision=EDIT 时指向被更新的已有知识 id。"""
        self.type = type
        self.scope = scope
        self.title = title
        self.summary = summary
        self.body = body
        self.suggested_parents = suggested_parents or []
        self.suggested_links = suggested_links or []
        self.sources = sources or []
        self.decision = decision
        self.edit_id = edit_id

    def to_dict(self) -> dict:
        """转 dict（便于命令行/JSON 传递候选）。"""
        return {
            "type": self.type.value,
            "scope": self.scope.value,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "suggested_parents": self.suggested_parents,
            "suggested_links": self.suggested_links,
            "sources": [s.to_dict() for s in self.sources],
            "decision": self.decision.value,
            "edit_id": self.edit_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DistillCandidate":
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


# ==================== 值对象 ====================

class Source:
    """知识来源：类型 + 锚点引用。"""

    def __init__(self, type: SourceType, ref: str = ""):
        """来源：type 来源类型；ref 锚点引用（sources.ref）。"""
        self.type = type
        self.ref = ref

    def to_dict(self) -> dict:
        """转 dict（用于 frontmatter / 序列化）。"""
        return {"type": self.type.value, "ref": self.ref}

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        """从 dict 还原。"""
        return cls(SourceType(d.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                   d.get("ref", ""))


class ExternalRef:
    """外部锚点：知识 ↔ 真实世界。"""

    def __init__(self, type: RefType, value: str):
        """外部引用：type 引用类型；value 引用值。"""
        self.type = type
        self.value = value

    def to_dict(self) -> dict:
        """转 dict。"""
        return {"type": self.type.value, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "ExternalRef":
        """从 dict 还原。"""
        return cls(RefType(d["type"]), d["value"])


class Knowledge:
    """知识本体（create/update 的主体）。

    id / created / updated 由接口内部管理，构造时不传。
    """

    def __init__(self, type: KnowledgeType, scope: Scope, title: str,
                 summary: str = "", body: str = "",
                 sources=None, parents=None, links=None,
                 external_refs=None, status: Status = Status.DRAFT, id: str = "",
                 created: str = "", updated: str = ""):
        """知识本体：type 认识论类型；scope 成立范围；title 标题；summary 判断摘要；body 正文；
        sources 来源列表；parents 多父挂靠；links 关联；external_refs 外部锚点；status 成熟度；
        id 可选（缺省由接口生成）；created/updated 由接口管理。"""
        self.type = type
        self.scope = scope
        self.title = title
        self.summary = summary
        self.body = body
        self.sources = sources or []
        self.parents = parents or []
        self.links = links or []
        self.external_refs = external_refs or []
        self.status = status
        self.id = id
        self.created = created
        self.updated = updated

    def to_dict(self) -> dict:
        """转 dict（不含 id/时间，供调用方构造）。"""
        return {
            "type": self.type.value,
            "scope": self.scope.value,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "sources": [s.to_dict() for s in self.sources],
            "parents": self.parents,
            "links": self.links,
            "external_refs": [r.to_dict() for r in self.external_refs],
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Knowledge":
        """从 dict 还原（与 to_dict 对称；不含 id/created/updated）。"""
        return cls(
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
        )


class SearchHit:
    """search 返回的候选条目（轻量字段，供模型判断，不读全文）。"""

    def __init__(self, id: str, title: str, summary: str, file: str,
                 type: KnowledgeType, scope: Scope, score: float = 0.0):
        """检索候选：id 知识 id；title 标题；summary 判断摘要；file 下钻路径；
        type 类型；scope 范围；score RRF 融合分。"""
        self.id = id
        self.title = title
        self.summary = summary
        self.file = file
        self.type = type
        self.scope = scope
        self.score = score

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
