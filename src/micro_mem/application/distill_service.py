"""蒸馏服务：锚点保真 → 增量准备 → 计划提交/落库编排（D9：编排全部收编进服务层）。

第一原则：蒸馏的"提炼判断"是 AI 做的（判断归模型），本服务负责上下文供给、
计划校验与落库编排（能机械检查的全部下沉为系统硬约束，见 plan_checker）。

v2 计划契约流程（相对旧 candidates 流程的变化）：
- 单文件演进：DistillPlan 从 draft（定位）→ approved（归档）→ confirmed（落库回写）
- $ROOT 两段式：新建领域根先落库，items 里的 "$ROOT" 占位替换为实际 id
  （旧 MAIN 隐式位置约定的正式化收编）
- 游标语义：confirm 成功后 cursor = max(items 覆盖的 turns，skip 同计）；
  expected_turns（submit 时系统回填）支撑轮次覆盖校验
- 已砍除：_find_existing 幂等（坐标归计划审查，重复暴露为警告）、
  自动挂靠启发式（parent 是计划必填坐标）、distill_state.json（计划自包含）
"""
import os
import re
from dataclasses import dataclass, field

from ..application import distill_view
from ..application.knowledge_service import KnowledgeService
from ..application.plan_checker import (
    CheckContext,
    CheckIssue,
    PlanRejectedError,
    check_plan,
)
from ..application.ports import IndexStore, SourceParser, TruthStore
from ..application.search_service import SearchService
from ..domain.anchor_format import (
    parse_turns as parse_anchor_turns,
    serialize_turns,
    wrap_turn,
)
from ..domain.models import (
    ROOT_PLACEHOLDER,
    Anchor,
    DistillDriver,
    DistillPlan,
    EdgeType,
    ItemResult,
    Knowledge,
    KnowledgeType,
    NodeRecord,
    PlanAction,
    PlanItem,
    Scope,
    Source,
    SourceType,
    Status,
    parse_axis_placeholder,
)


@dataclass
class DistillContext:
    """prepare 的产出：R1 定位素材（增量视图 + 预检索子树）。"""
    anchor_id: str
    distilled_until: int                       # 已蒸馏到的轮次（游标）
    processed_until: int                       # 本次处理到的轮次
    delta_turns: list[tuple[int, str]] = field(default_factory=list)   # 增量轮次（原文）
    full_text: str = ""                        # 整体锚点上下文（保真全文）
    view_turns: list[tuple[int, str]] = field(default_factory=list)   # R1 视图（全量/压缩）
    view_compressed: bool = False              # 是否触发了预算压缩（界面提示用）
    related: list[Knowledge] = field(default_factory=list)            # 预检索子树


@dataclass
class ConfirmResult:
    """confirm_plan 的产出。"""
    plan_id: str = ""
    created_ids: list[str] = field(default_factory=list)   # 新建（含领域根）
    edited_ids: list[str] = field(default_factory=list)    # 更新
    skipped: int = 0                                       # 显式跳过的项数
    cursor_updated: str = ""                               # 已回写游标的锚点 id
    warnings: list[str] = field(default_factory=list)      # force 放行的警告（留痕）


@dataclass
class DomainDistillContext:
    """prepare_domain 的产出：主题驱动 R1 素材（领域盘点报告数据）。

    root_id 为空 = 领域根未定位（歧义/零命中），candidates 填候选供用户重选。
    """
    root_id: str = ""
    root_title: str = ""
    tree: list[Knowledge] = field(default_factory=list)          # 领域树全景（含根，BFS 序）
    anchors: list[Anchor] = field(default_factory=list)          # 相关锚点（含蒸馏游标）
    gaps: list[str] = field(default_factory=list)                # 机械空缺统计（判断归 AI）
    candidates: list[NodeRecord] = field(default_factory=list)   # 未定位时的候选领域根


class DistillService:
    """蒸馏服务：锚点保真 → 增量准备 → 计划提交与落库。"""

    def __init__(self, truth: TruthStore, index: IndexStore,
                 knowledge: KnowledgeService, search: SearchService | None = None,
                 aspects: list[str] | None = None,
                 view_config: dict[str, int] | None = None,
                 parsers: list[SourceParser] | None = None):
        """依赖注入：端口 + 协作服务 + 切面值域 + R1 视图预算（config 注入）。

        search 为 None 时跳过预检索供给（退化回纯轮次输出，便于测试隔离）。
        parsers 为 S0 素材解析注册表（有序探测）；空列表时 anchor 拒收一切输入。
        """
        self._truth = truth
        self._index = index
        self._knowledge = knowledge
        self._search = search
        self._aspects = list(aspects or [])
        self._view = {**distill_view.DEFAULTS, **(view_config or {})}
        self._parsers = list(parsers or [])

    # ================= 一段：锚点（S0+S1：解析 → 确认闸门在 CLI） =================

    def anchor(self, source: str, title: str = "") -> str:
        """任意素材（文件路径 / URL）→ 保真锚点。返回 anchor_id（零轮返回空串）。

        S0 解析：按注册表顺序探测 parser；turn 序列经 anchor_format 唯一序列化。
        """
        parser = self._pick_parser(source)
        turns = parser.parse_turns(source)
        if not turns:
            return ""
        return self._save_turns(turns, title or self.default_title(source), source)

    def anchor_text(self, text: str, title: str = "") -> str:
        """对话中直接给的一段描述（--text 通道）：整段 = 1 turn。

        多段描述需要按段切轮时，写成 md 文件走 MarkdownParser（标题切轮）。
        """
        body = text.strip()
        if not body:
            return ""
        turns = [(1, wrap_turn(body))]
        return self._save_turns(turns, title or body[:20], "inline-text")

    def preview_turns(self, source: str = "", text: str = "") -> list[tuple[int, str]]:
        """确认闸门（S0 输出预览）：不落库，返回 turn 序列供渲染核对。"""
        if text:
            body = text.strip()
            return [(1, wrap_turn(body))] if body else []
        return self._pick_parser(source).parse_turns(source)

    def _save_turns(self, turns: list[tuple[int, str]], title: str,
                    source: str) -> str:
        return self._truth.save_anchor(Anchor(
            id="", title=title, date="",
            content=serialize_turns(turns), source=source))

    def _pick_parser(self, source: str) -> SourceParser:
        for parser in self._parsers:
            if parser.supports(source):
                return parser
        raise ValueError(
            f"不支持的素材格式: {source}"
            "（支持: .jsonl / .md / .html / http(s):// URL；或 --text 直接输入文本）")

    @staticmethod
    def default_title(source: str) -> str:
        """素材缺省标题：URL 取域名，文件取 basename（--text 由 anchor_text 截前 20 字）。"""
        if source.startswith(("http://", "https://")):
            return source.split("//", 1)[1].split("/")[0]
        return os.path.basename(source)

    # ================= 二段：增量蒸馏准备 =================

    def prepare(self, anchor_id: str) -> DistillContext:
        """增量蒸馏准备：读锚点 → 算增量 → 供给 R1 素材（视图 + 预检索子树）。"""
        anchor = self._truth.get_anchor(anchor_id)
        if anchor is None:
            raise KeyError(f"锚点不存在: {anchor_id}")
        # 锚点自足（D3）：turn 切分只认锚点正文（anchor_format 唯一读方），
        # 不回读源文件——源只是 S0 的输入，S1 落盘后生命周期结束。
        text = anchor.content
        turns = parse_anchor_turns(text)
        cursor = self._truth.get_distill_cursor(anchor_id)
        delta = [(i, t) for i, t in turns if i > cursor]
        processed_until = max((i for i, _ in delta), default=cursor)
        # R1 素材一：增量视图（全量优先，超预算才压缩——见 distill_view）
        view_turns, compressed = distill_view.render_view(
            delta, self._view["context_budget_chars"],
            self._view["assistant_chars"], self._view["tool_input_chars"])
        # R1 素材二：预检索子树（parent/edit_id 坐标的参照系；事件蒸馏落地再参数化）
        related: list[Knowledge] = []
        if self._search is not None and delta:
            query = distill_view.extract_user_text(delta)
            related = self._search.related_subtree(
                query, hit_limit=self._view["preretrieve_hits"],
                max_nodes=self._view["subtree_max"])
        return DistillContext(
            anchor_id=anchor_id, distilled_until=cursor,
            processed_until=processed_until, delta_turns=delta, full_text=text,
            view_turns=view_turns, view_compressed=compressed, related=related)

    # ================= 二段之主题驱动：领域盘点（--domain） =================

    def prepare_domain(self, domain_ref: str, anchor_limit: int = 10) -> DomainDistillContext:
        """主题驱动盘点准备：领域根解析 → 树全景 → 相关锚点清单 → 机械空缺统计。

        只读不写：盘点不动真值、不推游标（游标是会话驱动的增量边界，
        topic 计划天然无计划级 anchor，confirm 的游标推进对它不生效）。
        """
        root, candidates = self._resolve_domain_root(domain_ref)
        if root is None:
            return DomainDistillContext(candidates=candidates)
        tree = self._domain_tree(root.id)
        anchors = self._truth.search_anchors(root.title, limit=anchor_limit)
        return DomainDistillContext(
            root_id=root.id, root_title=root.title, tree=tree, anchors=anchors,
            gaps=self._domain_gaps(tree, anchors))

    # ---------------- prepare_domain 私有 ----------------

    def _resolve_domain_root(self, ref: str) -> tuple[NodeRecord | None, list[NodeRecord]]:
        """领域根解析：id 精确 → title 精确 → title 子串 → search_keyword 模糊 → 列候选。

        检索复用 index.search_keyword（FTS 短语 + LIKE 双路，summary/body 也召回）；
        裁决（根过滤 + 确定性优先级）是本方法的新增薄层——检索归系统，定身份归这里。
        """
        ref = ref.strip()
        roots = self._domain_roots()
        if not ref:
            return None, roots
        if re.fullmatch(r"k-\d+", ref):                        # ① id 精确
            hit = next((n for n in roots if n.id == ref), None)
            if hit is not None:
                return hit, []
        exact = [n for n in roots if n.title == ref]           # ② title 精确
        if len(exact) == 1:
            return exact[0], []
        substr = [n for n in roots if ref in n.title]          # ③ title 子串
        if len(substr) == 1:
            return substr[0], []
        root_ids = {n.id for n in roots}                       # ④ 模糊兜底（复用双路检索）
        fuzzy = [n for n in self._index.search_keyword(
            ref, limit=50, type_filter=KnowledgeType.MODEL.value,
            scope_filter=Scope.DOMAIN.value) if n.id in root_ids]
        if len(fuzzy) == 1:
            return fuzzy[0], []
        return None, (exact or substr or fuzzy or roots)       # 歧义/零命中 → 列候选

    def _domain_roots(self) -> list[NodeRecord]:
        """全部活跃领域根：model/domain + aspect 空 + 无 parent 出边 + 非 deprecated。"""
        has_parent = {f for f, _t, et in self._index.get_all_edges()
                      if et == EdgeType.PARENT.value}
        return [n for n in self._index.get_all_nodes()
                if n.type == KnowledgeType.MODEL.value
                and n.scope == Scope.DOMAIN.value
                and n.status != Status.DEPRECATED.value
                and not n.aspect
                and n.id not in has_parent]

    def _domain_tree(self, root_id: str) -> list[Knowledge]:
        """领域树全景：从根沿 parent 边向下 BFS（边方向 子→父，反查得子节点）。

        deprecated 节点不进全景（已归档不算现状），其子节点仍向下遍历。
        """
        children_of: dict[str, list[str]] = {}
        for from_id, to_id, et in self._index.get_all_edges():
            if et == EdgeType.PARENT.value:
                children_of.setdefault(to_id, []).append(from_id)
        tree: list[Knowledge] = []
        seen = {root_id}
        queue = [root_id]
        while queue:
            kid = queue.pop(0)
            k = self._truth.get_knowledge(kid)
            if k is not None and k.status is not Status.DEPRECATED:
                tree.append(k)
            for c in children_of.get(kid, []):
                if c not in seen:
                    seen.add(c)
                    queue.append(c)
        return tree

    def _domain_gaps(self, tree: list[Knowledge], anchors: list[Anchor]) -> list[str]:
        """机械空缺统计（只报数：轴覆盖计数 + 锚点游标状态；缺口判断归 AI）。"""
        counts: dict[str, int] = {}
        for k in tree:
            if k.aspect:
                counts[k.aspect] = counts.get(k.aspect, 0) + 1
        gaps = [f"{axis} 轴无节点" if counts.get(axis, 0) == 0
                else f"{axis} 轴仅 1 节点"
                for axis in self._aspects if counts.get(axis, 0) <= 1]
        undistilled = sum(1 for a in anchors if a.distilled_until < 0)
        if undistilled:
            gaps.append(f"{undistilled} 个相关锚点游标为 -1（从未蒸馏）")
        return gaps

    # ================= 三段：计划提交（R2 批准动作） =================

    def submit_plan(self, plan: DistillPlan) -> tuple[DistillPlan, list[CheckIssue]]:
        """提交计划：draft 级校验 → 回填 expected_turns → 归档（分配 plan_id）。

        error 级问题抛 PlanRejectedError 拒收；warning 级放行并随返回值带出。
        归档后 status=approved，R3 在归档文件上原地填充正文。
        """
        if plan.plan_id:
            raise ValueError(f"计划已有 plan_id（{plan.plan_id}），"
                             "修订请直接编辑归档文件，不要重复提交")
        ctx = CheckContext(self._truth, self._index, self._aspects)
        issues = check_plan(plan, ctx, stage="draft")
        errors = [i for i in issues if i.level == "error"]
        if errors:
            raise PlanRejectedError(errors)
        plan.expected_turns = self._expected_turns(plan)
        plan.status = "approved"
        self._truth.save_plan(plan)
        return plan, [i for i in issues if i.level == "warning"]

    def _expected_turns(self, plan: DistillPlan) -> list[int]:
        """本次增量的轮次全集（session 驱动）：游标之后到最新轮次。

        轮次覆盖校验的基线；锚点自足，切轮只认锚点正文。
        """
        if plan.driver is not DistillDriver.SESSION or not plan.anchor:
            return []
        anchor = self._truth.get_anchor(plan.anchor)
        if anchor is None:
            return []
        cursor = self._truth.get_distill_cursor(plan.anchor)
        return [i for i, _ in parse_anchor_turns(anchor.content) if i > cursor]

    # ================= 四段：确认落库（R4） =================

    def confirm_plan(self, plan_id: str, force: bool = False) -> ConfirmResult:
        """落库编排：全量校验 → $ROOT 两段式落库 → 游标推进 → 计划回写归档。

        error 级拒收（不落库、不动游标）；warning 级需 force 放行，
        放行的警告记入 ConfirmResult 留痕。
        """
        plan = self._truth.get_plan(plan_id)
        if plan is None:
            raise KeyError(f"计划不存在: {plan_id}")
        if plan.status == "confirmed":
            raise ValueError(f"计划已落库，不得重复 confirm: {plan_id}")
        ctx = CheckContext(self._truth, self._index, self._aspects)
        issues = check_plan(plan, ctx, stage="final")
        errors = [i for i in issues if i.level == "error"]
        if errors:
            raise PlanRejectedError(errors)
        warnings = [i for i in issues if i.level == "warning"]
        if warnings and not force:
            raise PlanRejectedError(warnings, need_force=True)

        result = ConfirmResult(plan_id=plan_id,
                               warnings=[i.format() for i in warnings])
        root_id = self._resolve_root(plan, result)
        axis_map = self._resolve_axis_map(root_id)
        # 三段式落库：先轴节点（parent=$ROOT 的 create 项，登记 axis_map）
        # → 再其余 create 项（解析 $AXIS 占位）→ 最后 edit/move（move 依赖轴节点已落）
        for item in plan.items:
            if item.action is PlanAction.SKIP:
                item.result = ItemResult.SKIPPED
                result.skipped += 1
            elif (item.action is PlanAction.CREATE
                    and item.parent == ROOT_PLACEHOLDER):
                kid = self._create_item(item, root_id)
                if item.aspect:
                    axis_map.setdefault(item.aspect, kid)
                item.result, item.result_knowledge_id = ItemResult.CREATED, kid
                result.created_ids.append(kid)
        for item in plan.items:
            if item.action is not PlanAction.CREATE or item.parent == ROOT_PLACEHOLDER:
                continue
            kid = self._create_item(item, root_id, axis_map)
            item.result, item.result_knowledge_id = ItemResult.CREATED, kid
            result.created_ids.append(kid)
        for item in plan.items:
            if item.action is PlanAction.EDIT:
                self._apply_edit(item)
                item.result, item.result_knowledge_id = ItemResult.EDITED, item.edit_id
                result.edited_ids.append(item.edit_id)
            elif item.action is PlanAction.MOVE:
                self._apply_move(item, axis_map)
                item.result, item.result_knowledge_id = ItemResult.MOVED, item.edit_id
                result.edited_ids.append(item.edit_id)

        # 游标推进：max(items 覆盖的轮次)；skip 项同样计入（明确不蒸 = 已决策）
        covered = [t for item in plan.items for t in item.source_turns]
        if covered and plan.anchor:
            self._truth.set_distill_cursor(plan.anchor, max(covered))
            result.cursor_updated = plan.anchor

        plan.status = "confirmed"
        self._truth.save_plan(plan)
        return result

    # ---------------- confirm 私有 ----------------

    def _resolve_root(self, plan: DistillPlan, result: ConfirmResult) -> str:
        """$ROOT 第一段：existing → 直接返回 id；create → 先落根并回写实际 id 到计划。"""
        root = plan.domain_root
        if root.action == "existing":
            return root.id
        covered = sorted({t for item in plan.items for t in item.source_turns})
        kid = self._knowledge.create(Knowledge(
            type=KnowledgeType.MODEL, scope=Scope.DOMAIN,
            title=root.title, summary=root.summary, body=root.body,
            sources=[Source(SourceType.CONVERSATION_DISTILLED, plan.anchor, covered)]))
        root.id = kid   # 回写：计划档案记录新建根的实际 id
        result.created_ids.append(kid)
        return kid

    def _create_item(self, item: PlanItem, root_id: str,
                     axis_map: dict[str, str] | None = None) -> str:
        """create 项落库：领域树节点（model/domain），$ROOT/$AXIS 占位替换为实际 id。"""
        parent = self._resolve_parent(item.parent, root_id, axis_map or {})
        return self._knowledge.create(Knowledge(
            type=KnowledgeType.MODEL, scope=Scope.DOMAIN,
            title=item.title, summary=item.summary, body=item.body,
            aspect=item.aspect,
            sources=[Source(SourceType.CONVERSATION_DISTILLED,
                            item.source_anchor, sorted(item.source_turns))],
            parents=[parent]))

    def _resolve_axis_map(self, root_id: str) -> dict[str, str]:
        """库中已有轴节点（existing 根场景）：parent=根 + aspect=X + 非 deprecated。"""
        children: dict[str, list[str]] = {}
        for from_id, to_id, et in self._index.get_all_edges():
            if et == EdgeType.PARENT.value:
                children.setdefault(to_id, []).append(from_id)
        axis_map: dict[str, str] = {}
        for child_id in children.get(root_id, []):
            node = self._index.get_node(child_id)
            if (node is not None and node.aspect
                    and node.status != Status.DEPRECATED.value):
                axis_map.setdefault(node.aspect, child_id)
        return axis_map

    @staticmethod
    def _resolve_parent(parent: str, root_id: str,
                        axis_map: dict[str, str]) -> str:
        """parent 占位符解析：$ROOT → 根 id；$AXIS:<轴> → 轴节点 id（须在 axis_map）。"""
        if parent == ROOT_PLACEHOLDER:
            return root_id
        axis = parse_axis_placeholder(parent)
        if axis is not None:
            if axis not in axis_map:
                raise ValueError(f"轴节点不可用: $AXIS:{axis}"
                                 f"（计划未建该轴且库中不存在）")
            return axis_map[axis]
        return parent

    def _apply_move(self, item: PlanItem, axis_map: dict[str, str]) -> None:
        """move 落库：挪 parents 坐标（title/summary/body 不动），可带 aspect 跨轴换轴。

        成环防御：沿新 parent 向上遍历，路径经过被挪节点自身即拒
        （draft 阶段 checker 已对非占位 parent 做过同款检查，这里兜底占位解析后的）。
        """
        k = self._truth.get_knowledge(item.edit_id)
        assert k is not None  # 校验已保证 edit_id 存在
        if item.parent == ROOT_PLACEHOLDER:
            raise ValueError("move 的 parent 不允许 $ROOT（挪到根下无意义，目标应为轴节点）")
        new_parent = self._resolve_parent(item.parent, "", axis_map)
        cursor = self._truth.get_knowledge(new_parent)
        while cursor is not None:
            if cursor.id == item.edit_id:
                raise ValueError(f"move 成环: {item.edit_id} 不能挂到自己的子孙下")
            cursor = (self._truth.get_knowledge(cursor.parents[0])
                      if cursor.parents else None)
        sources = self._merge_sources(k.sources, item.source_anchor,
                                      item.source_turns)
        self._knowledge.update(item.edit_id, parents=[new_parent],
                               aspect=item.aspect or None, sources=sources)

    def _apply_edit(self, item: PlanItem) -> None:
        """edit 落库：title/summary/body/aspect 全量替换 + 溯源累积合并。

        挂靠坐标（parents/links）不动——挪树是显式的关联操作，不是蒸馏的本职。
        """
        k = self._truth.get_knowledge(item.edit_id)
        assert k is not None  # 校验已保证 edit_id 存在
        sources = self._merge_sources(k.sources, item.source_anchor, item.source_turns)
        self._knowledge.update(item.edit_id, title=item.title, summary=item.summary,
                               body=item.body, aspect=item.aspect, sources=sources)

    @staticmethod
    def _merge_sources(existing: list[Source], anchor: str,
                       turns: list[int]) -> list[Source]:
        """edit 溯源累积：同锚点合并 turns（去重排序），新锚点追加一条。"""
        merged = [Source(s.type, s.ref, list(s.turns)) for s in existing]
        for s in merged:
            if s.ref == anchor:
                s.turns = sorted(set(s.turns) | set(turns))
                return merged
        merged.append(Source(SourceType.CONVERSATION_DISTILLED, anchor,
                             sorted(set(turns))))
        return merged
