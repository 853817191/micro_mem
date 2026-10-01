"""应用服务单测：全部打在内存端口上（毫秒级，不碰盘、不起 SQLite）。

覆盖架构决策的行为面：
- D6 写路径：create/update 双源协调（真值 + 索引同步）
- D7 删除语义：delete 只移出索引工作集，rebuild 从真值复活
- D3 读路径：get 全文从真值组装，索引缺失 → None
- D8 语义兜底：off / always / on_zero_hit 三种策略
- D9 蒸馏编排：计划契约（submit 校验归档 → confirm 落库回写）、$ROOT 两段式、
  游标覆盖推进、edit 溯源合并
"""
import json

import pytest

from micro_mem.application.plan_checker import PlanRejectedError
from micro_mem.composition import assemble_inmemory
from micro_mem.domain.models import (
    Anchor,
    DistillDriver,
    DistillPlan,
    DomainRoot,
    EdgeType,
    ExternalRef,
    ItemResult,
    Knowledge,
    KnowledgeType,
    PlanAction,
    PlanItem,
    RefType,
    Scope,
    Source,
    SourceType,
    Status,
)


@pytest.fixture
def comp():
    """内存装配（带领域切面值域，贴近生产配置）。"""
    return assemble_inmemory(aspects=["flow", "structure", "boundary", "constraint"])


def _mk(title="示例知识", **kw) -> Knowledge:
    """造一条合法 Knowledge（可被 kw 覆盖）。"""
    kw.setdefault("type", KnowledgeType.FACT)
    kw.setdefault("scope", Scope.DOMAIN)
    return Knowledge(title=title, **kw)


def _write_jsonl(path, turns: list[list[tuple[str, str]]]) -> None:
    """造会话 jsonl：turns = [[(role, text), ...], ...]，每个 user 发言起新轮次。"""
    lines = []
    for turn in turns:
        for role, text in turn:
            lines.append(json.dumps(
                {"message": {"role": role, "content": text}}, ensure_ascii=False))
    path.write_text("\n".join(lines), encoding="utf-8")


def _anchor_for(comp, tmp_path, turns=None) -> str:
    """造会话 jsonl 并锚定，返回 anchor_id。"""
    turns = turns or [
        [("user", "第一轮问题"), ("assistant", "第一轮回答")],
        [("user", "第二轮问题"), ("assistant", "第二轮回答")],
    ]
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, turns)
    anchor_id = comp.distill.anchor(str(p))
    assert anchor_id
    return anchor_id


def _mk_root(comp, title="测试领域") -> str:
    """造一个已有领域根（model，无 parent），返回 id。"""
    return comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title=title, summary="根"))


def _mk_item(anchor_id: str, action=PlanAction.CREATE, **kw) -> PlanItem:
    """造一条定位合法的计划项（R1 形态：action/gist/title/坐标/source）。"""
    kw.setdefault("gist", "一句话概要")
    kw.setdefault("source_anchor", anchor_id)
    kw.setdefault("source_turns", [1, 2])
    if action is not PlanAction.SKIP:
        kw.setdefault("title", "节点标题")
    if action is PlanAction.CREATE:
        kw.setdefault("aspect", "flow")
    return PlanItem(action=action, **kw)


def _mk_plan(anchor_id: str, items: list[PlanItem], root_id: str, **kw) -> DistillPlan:
    """造一份挂已有根的 draft 计划（root_id 必填：挂靠坐标必须显式）。"""
    kw.setdefault("domain_root", DomainRoot(action="existing", id=root_id))
    return DistillPlan(anchor=anchor_id, items=items, **kw)


def _elaborate(comp, plan_id: str) -> None:
    """模拟 R3：取归档计划，逐项填 summary/body，原地存回（单文件演进）。"""
    plan = comp.truth.get_plan(plan_id)
    for i, item in enumerate(plan.items):
        if item.action is not PlanAction.SKIP:
            item.summary = item.summary or f"摘要{i}"
            item.body = item.body or f"正文{i}"
    comp.truth.save_plan(plan)


# ==================== KnowledgeService：收录/更新（D6） ====================


def test_create_assigns_id_timestamps_and_indexes(comp):
    kid = comp.knowledge.create(_mk(summary="一条摘要"))
    assert kid == "k-0001"
    k = comp.search.get(kid)
    assert k is not None and k.title == "示例知识"
    assert k.created and k.updated                       # 时间戳由真值分配
    assert comp.index.get_node(kid).summary == "一条摘要"  # 索引同步投影
    assert comp.index.get_node(kid).file == f"memory://knowledge/{kid}"


def test_create_validates_required_fields(comp):
    with pytest.raises(ValueError, match="title"):
        comp.knowledge.create(_mk(title=""))


def test_update_partial_none_means_unchanged(comp):
    kid = comp.knowledge.create(_mk(summary="旧摘要", body="旧正文"))
    comp.knowledge.update(kid, summary="新摘要")          # 只改 summary
    k = comp.search.get(kid)
    assert k.summary == "新摘要" and k.body == "旧正文"
    comp.knowledge.update(kid, summary=None, body="新正文")  # None = 不变
    k = comp.search.get(kid)
    assert k.summary == "新摘要" and k.body == "新正文"
    assert comp.index.get_node(kid).summary == "新摘要"   # 索引同步


def test_update_missing_raises(comp):
    with pytest.raises(KeyError):
        comp.knowledge.update("k-9999", summary="x")


def test_deprecate_marks_status(comp):
    kid = comp.knowledge.create(_mk())
    comp.knowledge.deprecate(kid)
    assert comp.search.get(kid).status == Status.DEPRECATED
    assert comp.index.get_node(kid).status == "deprecated"


# ==================== D7：delete = 移出索引，真值保留，rebuild 复活 ====================


def test_delete_removes_from_index_but_keeps_truth(comp):
    kid = comp.knowledge.create(_mk(body="正文还在真值里"))
    assert comp.knowledge.delete(kid) is True
    assert comp.index.get_node(kid) is None              # 索引工作集已移除
    assert comp.search.get(kid) is None                  # 检索不到（与语义一致）
    assert comp.truth.get_knowledge(kid).body == "正文还在真值里"  # 真值档案保留
    assert comp.knowledge.delete(kid) is False           # 重复删除幂等


def test_rebuild_resurrects_deleted(comp):
    kid = comp.knowledge.create(_mk(summary="复活验证", body="B"))
    comp.knowledge.delete(kid)
    assert comp.rebuild.rebuild() == 1
    assert comp.search.get(kid).body == "B"              # 从真值复活
    assert [h.id for h in comp.search.search("复活验证")] == [kid]


# ==================== 边 / 外部锚点：真值同步（rebuild 不丢） ====================


def test_edge_syncs_truth_and_survives_rebuild(comp):
    a = comp.knowledge.create(_mk(title="甲"))
    b = comp.knowledge.create(_mk(title="乙"))
    comp.knowledge.add_edge(a, b, EdgeType.PARENT)
    assert comp.truth.get_knowledge(a).parents == [b]    # 同步真值
    assert (b, "parent") in comp.index.get_edges(a)      # 同步索引
    comp.rebuild.rebuild()                               # 从真值重建
    assert (b, "parent") in comp.index.get_edges(a)      # 边不丢
    comp.knowledge.remove_edge(a, b, EdgeType.PARENT)
    assert comp.truth.get_knowledge(a).parents == []
    assert comp.index.get_edges(a) == []


def test_trace_edge_is_index_only(comp):
    a = comp.knowledge.create(_mk(title="甲"))
    b = comp.knowledge.create(_mk(title="乙"))
    comp.knowledge.add_edge(a, b, EdgeType.TRACE)
    assert (b, "trace") in comp.index.get_edges(a)
    k = comp.truth.get_knowledge(a)                      # trace 不进真值
    assert k.parents == [] and k.links == []


def test_external_ref_syncs_truth(comp):
    kid = comp.knowledge.create(_mk())
    ref = ExternalRef(RefType.IDEV, "TCX-12345")
    comp.knowledge.add_external_ref(kid, ref)
    assert ("idev", "TCX-12345") in comp.index.get_external_refs(kid)
    assert comp.truth.get_knowledge(kid).external_refs == [ref]
    comp.knowledge.remove_external_ref(kid, ref)
    assert comp.index.get_external_refs(kid) == []
    assert comp.truth.get_knowledge(kid).external_refs == []


# ==================== SearchService：D3 / D8 ====================


def test_get_reads_full_body_from_truth(comp):
    """D3 证据：body 只从真值出；索引丢了 body 也不影响 get。"""
    kid = comp.knowledge.create(_mk(body="这段正文只应来自真值"))
    node = comp.index.get_node(kid)
    comp.index.upsert_node(node, body="")                # 模拟索引 body 漂移/丢失
    k = comp.search.get(kid)
    assert k is not None and k.body == "这段正文只应来自真值"


def test_semantic_fallback_off_never_runs(comp):
    comp = assemble_inmemory(semantic_fallback="off")
    comp.knowledge.create(_mk(title="完全无关的标题", summary="s"))
    assert comp.search.search("零命中词xyz") == []        # 零命中也不兜底


def test_semantic_fallback_on_zero_hit(comp):
    comp = assemble_inmemory(semantic_fallback="on_zero_hit")
    comp.knowledge.create(_mk(title="记忆系统设计", summary="关于记忆系统"))
    # 有命中：纯字面结果（语义不掺和排序）
    hits = comp.search.search("记忆系统")
    assert len(hits) == 1 and hits[0].title == "记忆系统设计"
    # 零命中：语义兜底可能召回（HashEmbedder 无真语义，只验证"跑了兜底"不报错）
    comp.search.search("零命中词xyz")                    # 不异常即可


def test_semantic_fallback_invalid_raises():
    with pytest.raises(ValueError, match="semantic_fallback"):
        assemble_inmemory(semantic_fallback="bogus")


def test_search_multi_and_traverse(comp):
    a = comp.knowledge.create(_mk(title="alpha beta"))
    b = comp.knowledge.create(_mk(title="beta gamma"))
    hits = comp.search.search_multi(["alpha", "beta"])
    assert hits and hits[0].id == a                      # 双词同命中前置
    assert hits[0].score > 0
    comp.knowledge.add_edge(a, b, EdgeType.LINK)
    got = comp.search.traverse(a, depth=1)
    assert (b, "link", 1) in got
    got = comp.search.traverse(a, depth=1, edge_types=[EdgeType.PARENT])
    assert got == []                                     # 边类型过滤生效


# ==================== RebuildService ====================


def test_rebuild_preserves_node_fields_and_edges(comp):
    a = comp.knowledge.create(_mk(title="甲", summary="S", body="B"))
    b = comp.knowledge.create(_mk(title="乙"))
    comp.knowledge.add_edge(a, b, EdgeType.PARENT)
    comp.knowledge.add_external_ref(a, ExternalRef(RefType.URL, "https://x"))
    before = comp.truth.get_knowledge(a)
    assert comp.rebuild.rebuild() == 2
    node = comp.index.get_node(a)
    assert node.created == before.created and node.updated == before.updated
    assert node.file == f"memory://knowledge/{a}"
    assert (b, "parent") in comp.index.get_edges(a)
    assert ("url", "https://x") in comp.index.get_external_refs(a)
    assert comp.search.get(a).body == "B"                # 重建后 get 仍通


# ==================== ImportService ====================


def test_import_history_creates_anchors_and_tolerates_errors(comp, tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    _write_jsonl(d / "s1.jsonl", [[("user", "问"), ("assistant", "答")]])
    (d / "broken.jsonl").write_text("{不是合法json", encoding="utf-8")
    (d / "empty.jsonl").write_text("", encoding="utf-8")  # 无内容 → 不生成锚点
    result = comp.importer.import_history(str(d))
    assert result.anchors_created == 1
    assert result.knowledge_created == 0                 # 导入阶段不产知识
    anchors = comp.truth.list_anchors()
    assert len(anchors) == 1 and "问" in anchors[0].content
    assert anchors[0].source.endswith("s1.jsonl")        # 源路径记录（增量蒸馏用）


# ==================== DistillService：锚点与增量准备 ====================


def test_anchor_stores_abspath_source(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    a = comp.truth.get_anchor(anchor_id)
    assert a is not None and "第一轮问题" in a.content
    assert a.distilled_until == -1                       # 未蒸馏


def test_prepare_delta_and_cursor(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    ctx = comp.distill.prepare(anchor_id)
    assert ctx.distilled_until == -1                     # 游标初始
    assert ctx.processed_until == 2                      # 两轮都未蒸馏
    assert [i for i, _ in ctx.delta_turns] == [1, 2]
    assert "第一轮问题" in ctx.full_text
    # 游标推进后，prepare 只给增量
    comp.truth.set_distill_cursor(anchor_id, 1)
    ctx = comp.distill.prepare(anchor_id)
    assert [i for i, _ in ctx.delta_turns] == [2]


def test_prepare_missing_anchor_raises(comp):
    with pytest.raises(KeyError):
        comp.distill.prepare("s-99999999-999")


def test_prepare_supplies_view_and_related_subtree(comp, tmp_path):
    """R1 供给：预算内视图全量 + 预检索子树（命中 + 祖先链 + 命中节点的直接子节点）。"""
    root = _mk_root(comp, "国际机票")
    child = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="创建需求单流程",
        summary="需求单主流程", aspect="flow", parents=[root]))
    grandchild = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="报价通知环节",
        aspect="flow", parents=[child]))
    comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="财务对账"))
    anchor_id = _anchor_for(comp, tmp_path, turns=[
        [("user", "创建需求单流程怎么补字段"), ("assistant", "答")]])
    ctx = comp.distill.prepare(anchor_id)
    assert ctx.view_compressed is False                      # 预算内全量
    assert "创建需求单流程怎么补字段" in ctx.view_turns[0][1]
    related_ids = [k.id for k in ctx.related]
    assert child in related_ids                              # 对话点名 → 命中
    assert root in related_ids                               # 祖先链
    assert grandchild in related_ids                         # 命中节点的直接子节点
    assert all(k.title != "财务对账" for k in ctx.related)   # 无关节点不进


def test_prepare_empty_delta_supplies_nothing(comp, tmp_path):
    """空增量：零供给（无视图、无预检索）。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.truth.set_distill_cursor(anchor_id, 2)
    ctx = comp.distill.prepare(anchor_id)
    assert ctx.view_turns == [] and ctx.related == []


# ==================== DistillService：计划契约（submit → confirm，D9） ====================


def test_submit_plan_archives_and_backfills_expected_turns(comp, tmp_path):
    """submit：draft 校验通过 → 归档（分配 id）→ 系统回填增量轮次全集。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    root = _mk_root(comp)
    plan, warnings = comp.distill.submit_plan(_mk_plan(anchor_id, [
        _mk_item(anchor_id, parent=root),
        _mk_item(anchor_id, action=PlanAction.SKIP, gist="环境调试"),
    ], root))
    assert plan.plan_id.startswith("plan-")
    assert plan.status == "approved"
    assert plan.expected_turns == [1, 2]                 # 游标 -1 → 增量全集
    assert warnings == []                                # turns 全覆盖，无警告
    archived = comp.truth.get_plan(plan.plan_id)
    assert archived is not None and archived.status == "approved"
    assert [i.gist for i in archived.items] == ["一句话概要", "环境调试"]


def test_submit_plan_rejects_bad_coordinates(comp, tmp_path):
    """坐标硬校验：parent/锚点/$ROOT 非法 → 拒收（不落档）。"""
    anchor_id = _anchor_for(comp, tmp_path)
    root = _mk_root(comp)
    with pytest.raises(PlanRejectedError) as ei:
        comp.distill.submit_plan(_mk_plan(
            anchor_id, [_mk_item(anchor_id, parent="k-9999")], root))
    assert any(i.rule == "parent_valid" for i in ei.value.issues)
    with pytest.raises(PlanRejectedError):               # $ROOT 但根是 existing
        comp.distill.submit_plan(_mk_plan(
            anchor_id, [_mk_item(anchor_id, parent="$ROOT")], root))
    with pytest.raises(PlanRejectedError):               # 锚点不存在
        comp.distill.submit_plan(_mk_plan(
            "s-99999999-999", [_mk_item("s-99999999-999", parent=root)], root))
    with pytest.raises(PlanRejectedError):               # aspect 不在值域
        comp.distill.submit_plan(_mk_plan(
            anchor_id, [_mk_item(anchor_id, parent=root, aspect="bogus")], root))
    assert comp.truth.list_plans() == []                 # 全部拒收，零归档
    # 重复提交（计划已有 id）走另一路拒绝
    plan, _ = comp.distill.submit_plan(_mk_plan(
        anchor_id, [_mk_item(anchor_id, parent=root)], root))
    with pytest.raises(ValueError, match="重复提交"):
        comp.distill.submit_plan(plan)


def test_submit_plan_rejects_duplicate_domain_root(comp, tmp_path):
    """治 E2 平行树：create 同 title 的活跃 model 根 → 拒收；旧根 deprecate 后放行。"""
    anchor_id = _anchor_for(comp, tmp_path)
    old_root = _mk_root(comp, "国际机票需求单领域")
    with pytest.raises(PlanRejectedError) as ei:
        comp.distill.submit_plan(DistillPlan(
            anchor=anchor_id,
            domain_root=DomainRoot(action="create", title="国际机票需求单领域"),
            items=[_mk_item(anchor_id, action=PlanAction.SKIP)]))
    assert any("平行树" in i.message for i in ei.value.issues)
    # 旧根废弃后允许重建同名树
    comp.knowledge.deprecate(old_root)
    plan, _ = comp.distill.submit_plan(DistillPlan(
        anchor=anchor_id,
        domain_root=DomainRoot(action="create", title="国际机票需求单领域"),
        items=[_mk_item(anchor_id, action=PlanAction.SKIP)]))
    assert plan.status == "approved"


def test_confirm_creates_tree_with_root_placeholder(comp, tmp_path):
    """$ROOT 两段式：新根先落库，子节点 parent 占位替换为实际 id；计划回写归档。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    plan, _ = comp.distill.submit_plan(DistillPlan(
        anchor=anchor_id,
        domain_root=DomainRoot(action="create", title="测试领域",
                               summary="根摘要", body="根正文"),
        items=[
            _mk_item(anchor_id, parent="$ROOT", title="创建流程", aspect="flow"),
            _mk_item(anchor_id, parent="$ROOT", title="需求单实体",
                     aspect="structure", source_turns=[2]),
            _mk_item(anchor_id, action=PlanAction.SKIP, gist="闲聊",
                     source_turns=[]),
        ]))
    _elaborate(comp, plan.plan_id)
    result = comp.distill.confirm_plan(plan.plan_id)
    assert len(result.created_ids) == 3                  # 根 + 2 子节点
    root_id = result.created_ids[0]
    root = comp.search.get(root_id)
    assert root.title == "测试领域" and root.type is KnowledgeType.MODEL
    assert root.aspect == ""                             # 领域根不带切面
    assert root.sources[0].turns == [1, 2]               # 根溯源 = 计划轮次并集
    flow_node = comp.search.get(result.created_ids[1])
    assert flow_node.parents == [root_id]                # $ROOT → 实际 id
    assert flow_node.aspect == "flow"
    assert flow_node.sources[0].ref == anchor_id
    assert flow_node.sources[0].turns == [1, 2]          # 轮次级溯源落库
    assert result.skipped == 1
    assert result.cursor_updated == anchor_id
    assert comp.truth.get_distill_cursor(anchor_id) == 2  # max(items turns)
    # 计划回写：status + 每项 result + 新建根 id
    archived = comp.truth.get_plan(plan.plan_id)
    assert archived.status == "confirmed"
    assert archived.domain_root.id == root_id
    assert archived.items[0].result is ItemResult.CREATED
    assert archived.items[0].result_knowledge_id == result.created_ids[1]
    assert archived.items[2].result is ItemResult.SKIPPED
    # 重复 confirm 拒绝
    with pytest.raises(ValueError, match="重复 confirm"):
        comp.distill.confirm_plan(plan.plan_id)


def test_confirm_edit_merges_sources_and_updates(comp, tmp_path):
    """edit：title/summary/body/aspect 全量替换 + 同锚点溯源 turns 累积合并。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    kid = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="旧标题",
        summary="旧摘要", body="旧正文", aspect="flow",
        sources=[Source(SourceType.CONVERSATION_DISTILLED, anchor_id, [1])]))
    comp.truth.set_distill_cursor(anchor_id, 1)      # turn 1 已蒸过，本次增量只有 turn 2
    root = _mk_root(comp)
    plan, _ = comp.distill.submit_plan(_mk_plan(
        anchor_id,
        [_mk_item(anchor_id, action=PlanAction.EDIT, edit_id=kid,
                  title="新标题", aspect="boundary", source_turns=[2])],
        root))
    assert plan.expected_turns == [2]                # 游标后的增量全集
    _elaborate(comp, plan.plan_id)
    result = comp.distill.confirm_plan(plan.plan_id)
    assert result.edited_ids == [kid] and result.created_ids == []
    k = comp.search.get(kid)
    assert k.title == "新标题" and k.summary == "摘要0"
    assert k.aspect == "boundary"                        # 切面可改
    assert len(k.sources) == 1                           # 同锚点合并为一条
    assert k.sources[0].turns == [1, 2]                  # turns 并集（溯源累积）


def test_confirm_warning_requires_force(comp, tmp_path):
    """轮次覆盖缺口 = warning：无 force 拒收（不动游标），force 放行且警告留痕。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    root = _mk_root(comp)
    plan, _ = comp.distill.submit_plan(_mk_plan(
        anchor_id, [_mk_item(anchor_id, parent=root, source_turns=[1])], root))
    assert 2 in plan.expected_turns                      # 增量全集含 turn 2
    _elaborate(comp, plan.plan_id)
    with pytest.raises(PlanRejectedError) as ei:
        comp.distill.confirm_plan(plan.plan_id)
    assert ei.value.need_force
    assert any(i.rule == "turn_coverage" for i in ei.value.issues)
    assert comp.truth.get_distill_cursor(anchor_id) == -1   # 拒收不动游标
    assert [n.id for n in comp.index.get_all_nodes()] == [root]  # 未落新节点
    result = comp.distill.confirm_plan(plan.plan_id, force=True)
    assert len(result.created_ids) == 1
    assert any("turn_coverage" in w for w in result.warnings)
    assert comp.truth.get_distill_cursor(anchor_id) == 1    # 游标只推到覆盖处


def test_submit_plan_warns_duplicate_title(comp, tmp_path):
    """同 title 活跃节点已存在 → submit 警告（提示该 edit），不拒收。"""
    anchor_id = _anchor_for(comp, tmp_path)
    root = _mk_root(comp)
    comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="节点标题",
        summary="已有", parents=[root]))
    _, warnings = comp.distill.submit_plan(_mk_plan(
        anchor_id, [_mk_item(anchor_id, parent=root, title="节点标题")], root))
    assert any(i.rule == "duplicate_title" for i in warnings)


# ==================== DistillService：主题驱动盘点（--domain，阶段3） ====================


def _seed_domain(comp):
    """造测试领域树：根(国际机票) + flow/structure 两个子节点。"""
    root = _mk_root(comp, "国际机票")
    flow = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="创建需求单流程",
        aspect="flow", parents=[root]))
    struct = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="需求单实体",
        aspect="structure", parents=[root]))
    return root, flow, struct


def test_prepare_domain_report(comp):
    """盘点报告三供给：树全景（BFS 含根）+ 相关锚点（含游标）+ 机械空缺统计。"""
    root, flow, struct = _seed_domain(comp)
    aid = comp.truth.save_anchor(Anchor(
        id="", title="国际机票需求单联调", date="2026-09-20", content="需求单流程讨论"))
    comp.truth.save_anchor(Anchor(id="", title="无关会话", date="", content="聊别的"))
    dctx = comp.distill.prepare_domain("国际机票")
    assert dctx.root_id == root and dctx.root_title == "国际机票"
    assert [k.id for k in dctx.tree] == [root, flow, struct]   # BFS 序全景
    assert [a.id for a in dctx.anchors] == [aid]               # 只命中相关锚点
    assert "boundary 轴无节点" in dctx.gaps                    # 机械统计：空轴
    assert "constraint 轴无节点" in dctx.gaps
    assert "flow 轴仅 1 节点" in dctx.gaps                     # 机械统计：薄轴
    assert any("从未蒸馏" in g for g in dctx.gaps)             # 游标 -1 锚点


def test_prepare_domain_resolution_ladder(comp):
    """领域根解析：id 精确 / title 精确 / 子串唯一 → 命中；歧义/零命中 → 列候选。"""
    root, _flow, _struct = _seed_domain(comp)
    other = _mk_root(comp, "国际酒店")
    assert comp.distill.prepare_domain(root).root_id == root         # ① id 精确
    assert comp.distill.prepare_domain("国际酒店").root_id == other  # ② title 精确
    assert comp.distill.prepare_domain("机票").root_id == root       # ③ 子串唯一
    dctx = comp.distill.prepare_domain("国际")                       # 子串歧义
    assert dctx.root_id == "" and {n.id for n in dctx.candidates} == {root, other}
    dctx = comp.distill.prepare_domain("不存在xyz")                  # 零命中 → 列全部根
    assert dctx.root_id == "" and {n.id for n in dctx.candidates} == {root, other}
    dctx = comp.distill.prepare_domain("创建需求单流程")             # 子节点不是根
    assert dctx.root_id == "" and {n.id for n in dctx.candidates} == {root, other}
    comp.knowledge.deprecate(other)                                  # 废弃根退出解析
    assert comp.distill.prepare_domain("国际").root_id == root


def test_topic_plan_confirm_does_not_advance_cursor(comp, tmp_path):
    """topic 驱动不推游标（游标归会话驱动的增量边界）：items 带锚点轮次也不动。"""
    anchor_id = _anchor_for(comp, tmp_path)
    root = _mk_root(comp)
    plan, _ = comp.distill.submit_plan(DistillPlan(
        driver=DistillDriver.TOPIC,                      # 主题驱动：无计划级 anchor
        domain_root=DomainRoot(action="existing", id=root),
        items=[_mk_item(anchor_id, parent=root)]))
    assert plan.expected_turns == []                     # 非 session → 无轮次基线
    _elaborate(comp, plan.plan_id)
    result = comp.distill.confirm_plan(plan.plan_id)
    assert result.cursor_updated == ""                   # 未回写游标
    assert comp.truth.get_distill_cursor(anchor_id) == -1
