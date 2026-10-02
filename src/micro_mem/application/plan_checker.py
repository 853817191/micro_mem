"""蒸馏计划校验器：规则列表模式（能机械检查的，绝不依赖自觉）。

组织方式（模式扩展点，见设计文档第十一章）：
- 每条规则声明两个维度：适用模式（modes）× 适用阶段（stage）
- modes：通用规则含全部模式；模式专属规则只含自己的目标 mode——
  事件蒸馏落地时只需往 RULES 加 ({"event"}, ...) 条目，框架不动
- stage："draft" = 定位期可跑（submit 时校验坐标合法性）；
  "final" = 正文填充后才可跑（confirm 全量校验）
  submit 跑 draft 期，confirm 跑全部（防批准后手改坐标）

规则代码化原则：校验失败给拒收（error）或警告（warning，--force 放行），
不给"系统猜一下"的第三条路。
"""
from collections.abc import Callable
from dataclasses import dataclass

from ..application.ports import IndexStore, TruthStore
from ..domain.models import (
    ROOT_PLACEHOLDER,
    DistillDriver,
    DistillPlan,
    EdgeType,
    PlanAction,
    Status,
    parse_axis_placeholder,
)


@dataclass
class CheckIssue:
    """一条校验发现。"""
    level: str               # "error"（拒收）| "warning"（需 --force 放行）
    rule: str                # 规则名（消息定位用）
    message: str
    item_index: int = -1     # -1 = 计划级问题

    def format(self) -> str:
        """CLI 打印形态。"""
        where = f"items[{self.item_index}] " if self.item_index >= 0 else ""
        return f"[{self.level}] {self.rule}: {where}{self.message}"


class PlanRejectedError(Exception):
    """校验拒收：携带全部发现（一次性报全，不逐条试错）。

    need_force=True 表示只有 warning 级问题，调用方确认后可带 force 重试。
    """

    def __init__(self, issues: list[CheckIssue], need_force: bool = False):
        self.issues = issues
        self.need_force = need_force
        super().__init__("; ".join(i.format() for i in issues))


@dataclass
class CheckContext:
    """校验规则的运行时依赖。"""
    truth: TruthStore
    index: IndexStore
    aspects: list[str]       # 领域切面值域（空 = 未配置，跳过值域校验）


def _err(rule: str, message: str, item_index: int = -1) -> CheckIssue:
    return CheckIssue("error", rule, message, item_index)


def _warn(rule: str, message: str, item_index: int = -1) -> CheckIssue:
    return CheckIssue("warning", rule, message, item_index)


Rule = Callable[[DistillPlan, CheckContext], list[CheckIssue]]

# ==================== 通用规则（domain / event 均适用） ====================


def _plan_anchor_exists(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """session 驱动必须有存在且未作废的计划级锚点（游标回写目标）。"""
    if plan.driver is not DistillDriver.SESSION:
        return []
    if not plan.anchor:
        return [_err("plan_anchor", "session 驱动的计划缺 anchor 字段")]
    if not ctx.truth.anchor_active(plan.anchor):
        return [_err("plan_anchor", f"锚点不存在或已作废: {plan.anchor}")]
    return []


def _item_anchor_exists(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """每项的 source.anchor 必填、存在且未作废（skip 也必填——它是"哪几轮不蒸"的载体）。"""
    issues = []
    for i, item in enumerate(plan.items):
        if not item.source_anchor:
            issues.append(_err("item_anchor", "缺 source.anchor", i))
        elif not ctx.truth.anchor_active(item.source_anchor):
            issues.append(_err("item_anchor",
                               f"source.anchor 指向不存在或已作废的锚点: "
                               f"{item.source_anchor}", i))
    return issues


def _edit_target_exists(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """edit/move 项的 edit_id 必填且指向存在的节点。"""
    issues = []
    for i, item in enumerate(plan.items):
        if item.action not in (PlanAction.EDIT, PlanAction.MOVE):
            continue
        if not item.edit_id:
            issues.append(_err("edit_target", f"{item.action.value} 项缺 edit_id", i))
        elif ctx.index.get_node(item.edit_id) is None:
            issues.append(_err("edit_target",
                               f"edit_id 指向不存在的节点: {item.edit_id}", i))
    return issues


def _parent_valid(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """create/move 项的 parent 必填且合法：存在的节点、$ROOT（仅新建领域根时可用）
    或 $AXIS:<轴>（占位专查归 _axis_placeholder）。move 不许 $ROOT。"""
    issues = []
    root_create = plan.domain_root.action == "create"
    for i, item in enumerate(plan.items):
        if item.action not in (PlanAction.CREATE, PlanAction.MOVE):
            continue
        if not item.parent:
            issues.append(_err("parent_valid",
                               f"{item.action.value} 项缺 parent（领域树节点必须挂靠坐标）", i))
        elif item.parent == ROOT_PLACEHOLDER:
            if item.action is PlanAction.MOVE:
                issues.append(_err("parent_valid",
                                   "move 的 parent 不允许 $ROOT"
                                   "（目标应为轴节点或已有节点）", i))
            elif not root_create:
                issues.append(_err(
                    "parent_valid",
                    f"parent={ROOT_PLACEHOLDER} 但 domain_root.action 不是 create", i))
        elif parse_axis_placeholder(item.parent) is not None:
            continue  # $AXIS 占位由 _axis_placeholder 规则专查
        elif ctx.index.get_node(item.parent) is None:
            issues.append(_err("parent_valid",
                               f"parent 指向不存在的节点: {item.parent}", i))
    return issues


def _turn_coverage(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """轮次覆盖完整性：items 的 turns 并集必须覆盖 expected_turns（治"悄悄漏掉"）。

    skip 项的 turns 计入覆盖——"明确不蒸"是合法决策，漏标才是病。
    """
    if not plan.expected_turns:
        return []
    covered = {t for item in plan.items for t in item.source_turns}
    missing = [t for t in plan.expected_turns if t not in covered]
    if missing:
        return [_warn("turn_coverage",
                      f"增量轮次未被任何计划项覆盖: {missing}（显式 skip 才算决策）")]
    return []


def _turns_annotated(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """非 skip 项应标注轮次（R3 聚焦输入 + 落库后轮次级溯源）。"""
    return [_warn("turns_annotated", "未标注 source.turns（溯源粒度降级为整段锚点）", i)
            for i, item in enumerate(plan.items)
            if item.action is not PlanAction.SKIP and not item.source_turns]


def _elaborated_title(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """非 skip/move 项 title 必填——title 是 R1 定位产物（树节点的名字），不是 R3 正文。"""
    return [_err("elaborated_title", "缺 title（R1 定位不完整）", i)
            for i, item in enumerate(plan.items)
            if item.action in (PlanAction.CREATE, PlanAction.EDIT) and not item.title]


def _elaborated_body(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """R3 完成度：非 skip/move 项应有 summary/body（空缺给警告）。"""
    issues = []
    for i, item in enumerate(plan.items):
        if item.action in (PlanAction.SKIP, PlanAction.MOVE):
            continue
        if not item.summary:
            issues.append(_warn("elaborated_body", "缺 summary", i))
        if not item.body:
            issues.append(_warn("elaborated_body", "缺 body", i))
    return issues


# ==================== 领域模式专属规则 ====================


def _domain_root_valid(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """领域根声明合法：existing 指向存在的 model 根；create 不得与已有根重名（治 E2 平行树）。"""
    root = plan.domain_root
    if root.action == "existing":
        if not root.id:
            return [_err("domain_root", "domain_root.action=existing 但缺 id")]
        node = ctx.index.get_node(root.id)
        if node is None:
            return [_err("domain_root", f"domain_root.id 不存在: {root.id}")]
        if node.type != "model":
            return [_err("domain_root",
                         f"领域根必须是 model 类型，{root.id} 是 {node.type}")]
        return []
    if root.action == "create":
        if not root.title:
            return [_err("domain_root", "create 领域根缺 title")]
        for node in ctx.index.get_all_nodes():
            if node.title != root.title or node.type != "model":
                continue
            if node.status == "deprecated":
                continue
            k = ctx.truth.get_knowledge(node.id)
            if k is not None and not k.parents:  # 无 parent 的 model = 领域根
                return [_err("domain_root",
                             f"同 title 领域根已存在: {node.id}（平行树红线——"
                             "改用 existing 挂到它，或先 deprecate 旧根）")]
        return []
    return [_err("domain_root",
                 f"domain_root.action 非法: {root.action!r}（existing|create）")]


def _aspect_vocabulary(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """create/edit 项 aspect 必填且在值域内；move 项不强制（跨轴移动时才带），
    带了就校验值域（值域空 = 未配置，跳过）。"""
    if not ctx.aspects:
        return []
    issues = []
    for i, item in enumerate(plan.items):
        if item.action in (PlanAction.SKIP, PlanAction.MOVE):
            if item.action is PlanAction.MOVE and item.aspect \
                    and item.aspect not in ctx.aspects:
                issues.append(_err("aspect_vocabulary",
                                   f"aspect {item.aspect!r} 不在值域 {ctx.aspects}", i))
            continue
        if not item.aspect:
            issues.append(_err("aspect_vocabulary",
                               f"缺 aspect（领域树节点必须标轴，值域: {ctx.aspects}）", i))
        elif item.aspect not in ctx.aspects:
            issues.append(_err("aspect_vocabulary",
                               f"aspect {item.aspect!r} 不在值域 {ctx.aspects}", i))
    return issues


def _duplicate_title(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """警告：create 项 title 与现有活跃节点重名（提示该 edit 而非新建）。"""
    existing = {n.title: n.id for n in ctx.index.get_all_nodes()
                if n.status != "deprecated"}
    return [_warn("duplicate_title",
                  f"同 title 节点已存在: {existing[item.title]}（确认是 create 而非 edit？）", i)
            for i, item in enumerate(plan.items)
            if item.action is PlanAction.CREATE and item.title and item.title in existing]


def _aspect_chain(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """警告：create/move 项 aspect 与父节点不一致（坐标纠错；$AXIS 占位归专查规则）。"""
    issues = []
    for i, item in enumerate(plan.items):
        if item.action not in (PlanAction.CREATE, PlanAction.MOVE) or not item.aspect:
            continue
        if not item.parent or item.parent == ROOT_PLACEHOLDER \
                or parse_axis_placeholder(item.parent) is not None:
            continue  # 根的第一层切面 / 轴占位：无父链可比或归 _axis_placeholder
        parent = ctx.truth.get_knowledge(item.parent)
        if parent is not None and parent.aspect and parent.aspect != item.aspect:
            issues.append(_warn("aspect_chain",
                                f"aspect={item.aspect} 与父节点 {item.parent} 的 "
                                f"aspect={parent.aspect} 不一致", i))
    return issues


# ==================== 轴占位 / move 专属规则 ====================


def _axis_placeholder(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """$AXIS:<轴> 占位合法性（error）：①与 item.aspect 一致；②不悬空
    （同计划建该轴节点，或库中已存在——existing 根可查库）。"""
    issues = []
    for i, item in enumerate(plan.items):
        if item.action not in (PlanAction.CREATE, PlanAction.MOVE):
            continue
        axis = parse_axis_placeholder(item.parent)
        if axis is None:
            continue
        if item.aspect and item.aspect != axis:
            issues.append(_err("axis_placeholder",
                               f"parent=$AXIS:{axis} 与 aspect={item.aspect} 不一致", i))
        planned = any(a.action is PlanAction.CREATE
                      and a.parent == ROOT_PLACEHOLDER
                      and a.aspect == axis for a in plan.items)
        exists = (not planned
                  and plan.domain_root.action == "existing"
                  and _axis_in_library(ctx, plan.domain_root.id, axis))
        if not planned and not exists:
            issues.append(_err("axis_placeholder",
                               f"$AXIS:{axis} 悬空：计划未建该轴节点且库中不存在", i))
    return issues


def _axis_in_library(ctx: CheckContext, root_id: str, axis: str) -> bool:
    """库中该轴节点已存在：parent=根 + aspect=轴 + 非 deprecated。"""
    for from_id, to_id, et in ctx.index.get_all_edges():
        if et == EdgeType.PARENT.value and to_id == root_id:
            node = ctx.index.get_node(from_id)
            if (node is not None and node.aspect == axis
                    and node.status != Status.DEPRECATED.value):
                return True
    return False


def _leaf_direct_on_root(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """警告：aspect 非空的 create/move 项直接挂根——疑似叶子平铺，应挂 $AXIS:<轴>。

    豁免（新根场景）：挂根项的 aspect 被本计划 $AXIS:<轴> 引用 = 它是在建的轴节点
    （否则 $AXIS 悬空会被 _axis_placeholder 拦截，不可能漏判为轴）。
    existing 根场景：轴已在库中，挂根项一律视为平铺（轴节点不会重复建）。
    """
    root_id = plan.domain_root.id if plan.domain_root.action == "existing" else ""
    axes_referenced = {parse_axis_placeholder(a.parent)
                       for a in plan.items
                       if a.action in (PlanAction.CREATE, PlanAction.MOVE)}
    axes_referenced.discard(None)
    issues = []
    for i, item in enumerate(plan.items):
        if item.action not in (PlanAction.CREATE, PlanAction.MOVE) or not item.aspect:
            continue
        on_root = (item.parent == ROOT_PLACEHOLDER
                   or (root_id and item.parent == root_id))
        if not on_root:
            continue
        if plan.domain_root.action == "create" \
                and item.aspect in axes_referenced:
            continue  # 在建轴节点：合法
        issues.append(_warn("leaf_direct_on_root",
                            f"aspect={item.aspect} 的项直接挂根：疑似叶子平铺，"
                            f"应挂 $AXIS:{item.aspect}", i))
    return issues


def _move_no_cycle(plan: DistillPlan, ctx: CheckContext) -> list[CheckIssue]:
    """move 成环检查（error）：新 parent 的上游路径经过被挪节点自身即环。
    仅查非占位 parent（$AXIS 占位解析后由 confirm 的 _apply_move 兜底）。"""
    issues = []
    for i, item in enumerate(plan.items):
        if item.action is not PlanAction.MOVE:
            continue
        if parse_axis_placeholder(item.parent) is not None:
            continue
        cursor = ctx.truth.get_knowledge(item.parent)
        while cursor is not None:
            if cursor.id == item.edit_id:
                issues.append(_err("move_no_cycle",
                                   f"move 成环：{item.parent} 位于 {item.edit_id} 的子树下", i))
                break
            cursor = (ctx.truth.get_knowledge(cursor.parents[0])
                      if cursor.parents else None)
    return issues


# ==================== 规则表与入口 ====================

# (适用模式集, 适用阶段, 规则函数)；stage: draft=定位期可跑 / final=正文填充后才跑
RULES: list[tuple[frozenset, str, Rule]] = [
    (frozenset({"domain", "event"}), "draft", _plan_anchor_exists),
    (frozenset({"domain", "event"}), "draft", _item_anchor_exists),
    (frozenset({"domain", "event"}), "draft", _edit_target_exists),
    (frozenset({"domain", "event"}), "draft", _parent_valid),
    (frozenset({"domain", "event"}), "draft", _elaborated_title),
    (frozenset({"domain", "event"}), "draft", _turn_coverage),
    (frozenset({"domain", "event"}), "draft", _turns_annotated),
    (frozenset({"domain", "event"}), "final", _elaborated_body),
    (frozenset({"domain"}), "draft", _domain_root_valid),
    (frozenset({"domain"}), "draft", _aspect_vocabulary),
    (frozenset({"domain"}), "draft", _duplicate_title),
    (frozenset({"domain"}), "draft", _aspect_chain),
    (frozenset({"domain"}), "draft", _axis_placeholder),
    (frozenset({"domain"}), "draft", _leaf_direct_on_root),
    (frozenset({"domain", "event"}), "draft", _move_no_cycle),
]


def check_plan(plan: DistillPlan, ctx: CheckContext, stage: str = "final") -> list[CheckIssue]:
    """按计划 mode 选规则集、按 stage 过滤，返回全部发现（一次报全）。

    stage="draft"：submit 时的定位期校验（跳过 final 级）；
    stage="final"：confirm 时的全量校验（含防批准后手改坐标的重跑）。
    """
    mode = plan.mode.value
    issues: list[CheckIssue] = []
    for modes, rule_stage, fn in RULES:
        if mode not in modes:
            continue
        if stage == "draft" and rule_stage == "final":
            continue
        issues.extend(fn(plan, ctx))
    return issues
