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


# 语义兜底策略合法值（D8，领域语言；config 解析与 SearchService 共用）
SEMANTIC_FALLBACKS = ("on_zero_hit", "always", "off")


# ==================== 值对象 ====================


@dataclass
class Source:
    """知识来源：类型 + 锚点引用 + 可选的轮次级定位。

    turns：溯源到锚点内的具体轮次号（如 [3, 4]）；空 = 锚点整段。
    """
    type: SourceType
    ref: str = ""
    turns: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转 dict（与 from_dict 对称；turns 为空不输出，保持 frontmatter 干净）。"""
        d: dict = {"type": self.type.value, "ref": self.ref}
        if self.turns:
            d["turns"] = list(self.turns)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Source:
        """从 dict 还原。"""
        return cls(SourceType(d.get("type", SourceType.CONVERSATION_DISTILLED.value)),
                   d.get("ref", ""),
                   [int(t) for t in (d.get("turns") or [])])


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
    aspect: str = ""    # 领域切面（组织维度）：flow|structure|boundary|constraint|…
                        # 值域规则层约定、可扩展；空 = 非领域树节点 / 领域根

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
            "aspect": self.aspect,
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
            aspect=d.get("aspect", ""),
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


# ==================== 蒸馏计划（v2 契约） ====================


class PlanAction(Enum):
    """计划项动作：素材命运的全集（蒸馏判断的产出就这三种）。"""
    CREATE = "create"            # 新建知识节点
    EDIT = "edit"                # 更新已有节点
    SKIP = "skip"                # 明确不蒸（负知识留痕，治"悄悄漏掉 vs 明确不蒸"之辨）


class DistillDriver(Enum):
    """驱动方式：数据从哪来。"""
    SESSION = "session"          # 会话驱动：增量维护（mem distill <anchor>）
    TOPIC = "topic"              # 主题驱动：盘点建树（mem distill --domain）


class DistillMode(Enum):
    """蒸馏模式：蒸成什么形态（校验器按此选规则集）。"""
    DOMAIN = "domain"            # 领域蒸馏（本期实现）
    EVENT = "event"              # 事件蒸馏（预留枚举位，不实现——先具体后抽象）


class ItemResult(Enum):
    """计划项的落库结果（confirm 回写）。"""
    CREATED = "created"
    EDITED = "edited"
    SKIPPED = "skipped"


# parent 字段的保留字：引用本次计划新建的领域根（confirm 先落根再替换为实际 id）——
# 旧流程 MAIN 两段式 hack 的正式化：显式、只在新建根时出现、经计划审查
ROOT_PLACEHOLDER = "$ROOT"

# 计划状态机：draft（AI 手写）→ approved（submit 归档）→ confirmed（confirm 落库回写）
PLAN_STATUSES = ("draft", "approved", "confirmed")


@dataclass
class DomainRoot:
    """计划挂靠的领域根声明。

    action=existing：挂到已有领域根（id 必填）；
    action=create：新建领域根（title/summary/body 填全，confirm 先落库）。
    """
    action: str = ""             # "existing" | "create"（空值由校验器拦截提示）
    id: str = ""
    title: str = ""
    summary: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        """转 dict（与 from_dict 对称；空字段不输出，保持计划文件干净）。"""
        d: dict = {"action": self.action}
        if self.id:
            d["id"] = self.id
        if self.title:
            d["title"] = self.title
        if self.summary:
            d["summary"] = self.summary
        if self.body:
            d["body"] = self.body
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DomainRoot:
        """从 dict 还原。"""
        return cls(action=d.get("action", ""), id=d.get("id", ""),
                   title=d.get("title", ""), summary=d.get("summary", ""),
                   body=d.get("body", ""))


@dataclass
class PlanItem:
    """蒸馏计划的单条项，按多轮协议逐段填充：

    R1 定位：action/gist/坐标（parent|edit_id, aspect）/source（锚点+轮次）
    R3 成型：title/summary/body（edit 项全量替换语义，不留"缺省=不变"暧昧）
    R4 回写：result/result_knowledge_id（落库结果，计划成为审计档案）
    """
    action: PlanAction
    gist: str = ""                           # 一句话概要（R2 审查对象）
    source_anchor: str = ""                  # 来源锚点（skip 也必填——它正是"哪几轮不蒸"的载体）
    source_turns: list[int] = field(default_factory=list)
    # create 坐标
    title: str = ""
    aspect: str = ""                         # 领域切面（值域 config 可配）
    parent: str = ""                         # 父节点 id 或 "$ROOT"
    # edit 坐标
    edit_id: str = ""
    # R3 填充的正文
    summary: str = ""
    body: str = ""
    # R4 回写
    result: ItemResult | None = None
    result_knowledge_id: str = ""

    def to_dict(self) -> dict:
        """转 dict——落盘形态即契约形态（嵌套 source，AI 手写/人审友好）。"""
        source: dict = {"anchor": self.source_anchor}
        if self.source_turns:
            source["turns"] = list(self.source_turns)
        d: dict = {"action": self.action.value, "gist": self.gist, "source": source}
        if self.title:
            d["title"] = self.title
        if self.aspect:
            d["aspect"] = self.aspect
        if self.parent:
            d["parent"] = self.parent
        if self.edit_id:
            d["edit_id"] = self.edit_id
        if self.summary:
            d["summary"] = self.summary
        if self.body:
            d["body"] = self.body
        if self.result is not None:
            d["result"] = {"status": self.result.value,
                           "knowledge_id": self.result_knowledge_id}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PlanItem:
        """从 dict 还原（与 to_dict 对称）。"""
        source = d.get("source") or {}
        result = d.get("result") or {}
        return cls(
            action=PlanAction(d.get("action", PlanAction.CREATE.value)),
            gist=d.get("gist", ""),
            source_anchor=source.get("anchor", ""),
            source_turns=[int(t) for t in (source.get("turns") or [])],
            title=d.get("title", ""),
            aspect=d.get("aspect", ""),
            parent=d.get("parent", ""),
            edit_id=d.get("edit_id", ""),
            summary=d.get("summary", ""),
            body=d.get("body", ""),
            result=ItemResult(result["status"]) if result.get("status") else None,
            result_knowledge_id=result.get("knowledge_id", ""))


@dataclass
class DistillPlan:
    """蒸馏计划：多轮协议的中间产物与审计档案（负知识载体）。

    单文件演进：draft（R1 定位）→ approved（R2 批准归档，系统回填 expected_turns）
    → elaborated（R3 逐项填正文，文件原地更新）→ confirmed（R4 落库回写 result）。
    expected_turns：submit 时系统回填的增量轮次全集，confirm 的轮次覆盖校验基线。
    """
    plan_id: str = ""            # TruthStore 分配：plan-<yyyymmdd>-<seq>
    driver: DistillDriver = DistillDriver.SESSION
    mode: DistillMode = DistillMode.DOMAIN
    domain_root: DomainRoot = field(default_factory=DomainRoot)
    anchor: str = ""             # session 驱动必填（游标回写目标）
    items: list[PlanItem] = field(default_factory=list)
    expected_turns: list[int] = field(default_factory=list)
    status: str = "draft"        # draft | approved | confirmed
    created_at: str = ""

    def to_dict(self) -> dict:
        """转 dict（与 from_dict 对称）。"""
        return {
            "plan_id": self.plan_id,
            "driver": self.driver.value,
            "mode": self.mode.value,
            "domain_root": self.domain_root.to_dict(),
            "anchor": self.anchor,
            "items": [i.to_dict() for i in self.items],
            "expected_turns": list(self.expected_turns),
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DistillPlan:
        """从 dict 还原（与 to_dict 对称）。"""
        return cls(
            plan_id=d.get("plan_id", ""),
            driver=DistillDriver(d.get("driver", DistillDriver.SESSION.value)),
            mode=DistillMode(d.get("mode", DistillMode.DOMAIN.value)),
            domain_root=DomainRoot.from_dict(d.get("domain_root") or {}),
            anchor=d.get("anchor", ""),
            items=[PlanItem.from_dict(i) for i in (d.get("items") or [])],
            expected_turns=[int(t) for t in (d.get("expected_turns") or [])],
            status=d.get("status", "draft"),
            created_at=d.get("created_at", ""))


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
    aspect: str = ""         # 领域切面（Knowledge.aspect 的索引投影）


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
