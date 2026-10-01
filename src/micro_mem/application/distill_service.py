"""蒸馏服务：anchor → prepare → confirm 三段编排（D9：编排全部收编进服务层）。

第一原则：蒸馏的"提炼判断"是 AI 做的（判断归模型），本服务负责上下文供给与落库编排。
收编内容（原 cli/distill.py 的 CLI 私货）：
- MAIN 两段式：第一条候选（主事件）先落库，其余候选的 "MAIN" 占位替换为其实际 id
- 蒸馏游标回写：confirm 成功后按 prepare 写下的状态回写锚点 distilled_until
- 自动挂靠：落库后对新知识尝试挂靠到聚合主题节点
"""
import json
import os
from dataclasses import dataclass, field

from ..application.knowledge_service import KnowledgeService
from ..application.ports import IndexStore, TruthStore
from ..application.search_service import SearchService
from ..domain.models import Anchor, Decision, DistillCandidate, EdgeType, Knowledge
from ..infrastructure import claude_jsonl


@dataclass
class DistillContext:
    """prepare 的产出：增量蒸馏上下文（供 AI 提炼候选）。"""
    anchor_id: str
    distilled_until: int                       # 已蒸馏到的轮次（游标）
    processed_until: int                       # 本次处理到的轮次
    delta_turns: list[tuple[int, str]] = field(default_factory=list)   # 增量轮次
    full_text: str = ""                        # 整体锚点上下文（保真全文）


@dataclass
class ConfirmResult:
    """confirm 的产出。"""
    created_ids: list[str] = field(default_factory=list)   # 入库（含幂等命中）的知识 id
    attached: dict[str, str] = field(default_factory=dict)  # 自动挂靠 {新知识id: 父id}
    cursor_updated: str = ""                                # 已回写游标的锚点 id


class DistillService:
    """蒸馏服务：锚点保真 → 增量准备 → 候选落库。"""

    def __init__(self, truth: TruthStore, index: IndexStore,
                 knowledge: KnowledgeService, search: SearchService,
                 state_path: str = ""):
        """依赖注入：端口 + 协作服务 + 状态文件路径。

        state_path：跨进程暂存（prepare 与 confirm 是两次 CLI 调用），
        由装配根注入（生产 = config.temp_dir()/distill_state.json；空串 = 不落状态）。
        """
        self._truth = truth
        self._index = index
        self._knowledge = knowledge
        self._search = search
        self._state_path = state_path

    @property
    def state_path(self) -> str:
        """蒸馏状态文件路径（prepare 写、confirm 消费；空串 = 不落状态）。"""
        return self._state_path

    # ================= 一段：锚点 =================

    def anchor(self, jsonl_path: str, title: str = "") -> str:
        """会话 jsonl → 保真锚点。返回 anchor_id（无对话内容返回空串）。"""
        text = claude_jsonl.parse_session(jsonl_path)
        if not text:
            return ""
        return self._truth.save_anchor(Anchor(
            id="", title=title or os.path.basename(jsonl_path), date="",
            content=text, source=os.path.abspath(jsonl_path)))

    # ================= 二段：增量蒸馏准备 =================

    def prepare(self, anchor_id: str) -> DistillContext:
        """增量蒸馏准备：重同步锚点（含新增轮次）→ 算增量范围 → 写状态供 confirm 回写。"""
        anchor = self._truth.get_anchor(anchor_id)
        if anchor is None:
            raise KeyError(f"锚点不存在: {anchor_id}")
        if not anchor.source or not os.path.exists(anchor.source):
            raise ValueError(f"锚点 {anchor_id} 无有效 jsonl 源（{anchor.source or '空'}），"
                             "无法做增量蒸馏")
        # 1. 重同步锚点：整段会话（含新增轮次）保真全文
        text = claude_jsonl.parse_session(anchor.source)
        turns = claude_jsonl.parse_turns(anchor.source)
        self._truth.resync_anchor(anchor_id, text)
        # 2. 增量范围
        cursor = self._truth.get_distill_cursor(anchor_id)
        delta = [(i, t) for i, t in turns if i > cursor]
        processed_until = max((i for i, _ in delta), default=cursor)
        # 3. 写状态（confirm 据此回写游标）
        self._write_state(anchor_id, processed_until)
        return DistillContext(
            anchor_id=anchor_id, distilled_until=cursor,
            processed_until=processed_until, delta_turns=delta, full_text=text)

    # ================= 三段：确认入库 =================

    def confirm(self, candidates: list[DistillCandidate]) -> ConfirmResult:
        """落库编排：MAIN 两段式 → 逐条幂等/溯源校验/落库 → 回写游标 → 自动挂靠。

        溯源校验失败等异常会中断落库（调用方保证此时不回写游标——
        游标回写在全部落库成功之后，见下）。
        """
        result = ConfirmResult()
        if not candidates:
            return result
        # MAIN 两段式：第一条（主事件）先落库；其余候选的 "MAIN" 占位 → 其实际 id
        ids = self._confirm_batch([candidates[0]])
        main_id = ids[0] if ids else ""
        if len(candidates) > 1:
            rest = [self._replace_main(c, main_id) for c in candidates[1:]]
            ids += self._confirm_batch(rest)
        result.created_ids = ids
        # 回写蒸馏游标（消费 prepare 写下的状态；落库全部成功才走到这）
        state = self._read_state()
        if state.get("anchor_id"):
            self._truth.set_distill_cursor(state["anchor_id"],
                                           int(state.get("processed_until", -1)))
            result.cursor_updated = state["anchor_id"]
            self._clear_state()
        # 自动挂靠（现状启发式）
        result.attached = self._auto_attach_all(ids)
        return result

    # ---------------- confirm 私有 ----------------

    @staticmethod
    def _replace_main(c: DistillCandidate, main_id: str) -> DistillCandidate:
        """MAIN 占位替换：suggested_parents/links 里的 "MAIN" 换为主事件实际 id。"""
        if not main_id:
            return c
        c.suggested_parents = [main_id if p == "MAIN" else p for p in c.suggested_parents]
        c.suggested_links = [main_id if link == "MAIN" else link for link in c.suggested_links]
        return c

    def _confirm_batch(self, candidates: list[DistillCandidate]) -> list[str]:
        """逐条落库：REJECT 跳过；EDIT 更新已有；KEEP 幂等去重 → 溯源校验 → 收录。"""
        ids: list[str] = []
        for c in candidates:
            if c.decision == Decision.REJECT:
                continue
            if c.decision == Decision.EDIT:
                ids.append(self._apply_edit(c))
                continue
            existing = self._find_existing(c)
            if existing:
                ids.append(existing)
                continue
            self._validate_sources(c)
            ids.append(self._knowledge.create(Knowledge(
                type=c.type, scope=c.scope, title=c.title,
                summary=c.summary, body=c.body, sources=c.sources,
                parents=c.suggested_parents, links=c.suggested_links)))
        return ids

    def _find_existing(self, c: DistillCandidate) -> str:
        """幂等：同 title 且 sources.ref 有交集 → 返回已有知识 id，否则空串。"""
        refs = {s.ref for s in c.sources if s.ref}
        if not c.title or not refs:
            return ""
        for node in self._index.get_all_nodes():
            if node.title != c.title:
                continue
            k = self._truth.get_knowledge(node.id)
            if k is not None and refs & {s.ref for s in k.sources}:
                return k.id
        return ""

    def _validate_sources(self, c: DistillCandidate) -> None:
        """溯源校验：看起来指向锚点的 sources.ref（含 anchors/ 或 basename 以 s- 开头）
        必须存在对应锚点（锚归原文）。"""
        for s in c.sources:
            ref = (s.ref or "").strip()
            if not ref:
                continue
            norm = ref.replace("\\", "/")
            if "anchors/" not in norm and not norm.split("/")[-1].startswith("s-"):
                continue
            if not self._truth.anchor_exists(ref):
                raise ValueError(f"候选「{c.title}」的 sources.ref 指向不存在的锚点: {ref}")

    def _apply_edit(self, c: DistillCandidate) -> str:
        """EDIT：更新已有知识（type/scope/title/summary/body/关联），保留 id 与 sources。"""
        if not c.edit_id:
            raise ValueError(f"候选「{c.title}」decision=edit 但缺 edit_id")
        if self._index.get_node(c.edit_id) is None:
            raise ValueError(f"edit_id 指向不存在的节点: {c.edit_id}")
        self._knowledge.update(
            c.edit_id, type=c.type, scope=c.scope, title=c.title,
            summary=c.summary, body=c.body,
            parents=c.suggested_parents, links=c.suggested_links)
        return c.edit_id

    # ---------------- 自动挂靠（现状启发式） ----------------

    # TODO(自动挂靠专题): 挂靠判定权归属（系统启发式 vs 模型建议）、主题节点生命周期、
    # 跨领域 link 推荐机制——待「知识关联建立」专题重设计，见架构文档第十一章。
    _ATTACH_OVERLAP = 0.3   # 标题字符重叠门槛：避免挂到不相关的聚合主题

    def _auto_attach_all(self, ids: list[str]) -> dict[str, str]:
        """对每条新知识，用标题检索知识库，挂靠到"聚合主题"节点（同类聚合）。"""
        idset = set(ids)   # 同批新知识不作为父主题候选（避免同批互挂）
        result: dict[str, str] = {}
        for kid in ids:
            k = self._search.get(kid)
            if not k or not k.title:
                continue
            parent = self._auto_attach(kid, k.title, exclude=idset)
            if parent:
                result[kid] = parent
        return result

    def _auto_attach(self, kid: str, title: str, exclude: set[str]) -> str:
        """单条自动挂靠：只挂到"聚合主题"节点（有 parent 入边）且标题字符重叠达标。"""
        try:
            hits = self._search.search(title, limit=10)
        except Exception:
            return ""
        q_chars = {ch for ch in title if ch.strip()}
        if not q_chars:
            return ""
        for h in hits:
            if h.id == kid or h.id in exclude:
                continue
            if self._index.has_parent_edge(h.id):
                overlap = len(q_chars & set(h.title)) / len(q_chars)
                if overlap >= self._ATTACH_OVERLAP:
                    self._knowledge.add_edge(kid, h.id, EdgeType.PARENT)
                    return h.id
        return ""

    # ---------------- 蒸馏状态（跨进程暂存） ----------------

    def _write_state(self, anchor_id: str, processed_until: int) -> None:
        """写蒸馏状态文件（confirm 据此回写游标）；state_path 为空则跳过。"""
        if not self._state_path:
            return
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump({"anchor_id": anchor_id, "processed_until": processed_until},
                      f, ensure_ascii=False)

    def _read_state(self) -> dict:
        """读蒸馏状态；无状态文件返回空 dict。"""
        if not self._state_path or not os.path.exists(self._state_path):
            return {}
        with open(self._state_path, encoding="utf-8") as f:
            return json.load(f)

    def _clear_state(self) -> None:
        """消费后删除状态文件（防脏状态误回写）。"""
        if self._state_path and os.path.exists(self._state_path):
            os.remove(self._state_path)
