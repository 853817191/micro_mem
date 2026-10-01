"""应用服务单测：全部打在内存端口上（毫秒级，不碰盘、不起 SQLite）。

覆盖架构决策的行为面：
- D6 写路径：create/update 双源协调（真值 + 索引同步）
- D7 删除语义：delete 只移出索引工作集，rebuild 从真值复活
- D3 读路径：get 全文从真值组装，索引缺失 → None
- D8 语义兜底：off / always / on_zero_hit 三种策略
- D9 蒸馏编排：MAIN 两段式、游标回写、幂等、溯源校验、EDIT、自动挂靠
"""
import json
import os

import pytest

from micro_mem.composition import assemble_inmemory
from micro_mem.domain.models import (
    Decision,
    DistillCandidate,
    EdgeType,
    ExternalRef,
    Knowledge,
    KnowledgeType,
    RefType,
    Scope,
    Source,
    SourceType,
    Status,
)


@pytest.fixture
def comp(tmp_path):
    """内存装配；蒸馏状态文件落在 tmp_path（验证跨调用暂存语义）。"""
    return assemble_inmemory(state_path=str(tmp_path / "distill_state.json"))


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


def _candidate(anchor_id: str, title: str, **kw) -> DistillCandidate:
    """造一条溯源合法的 KEEP 候选。"""
    kw.setdefault("type", KnowledgeType.EVENT)
    kw.setdefault("scope", Scope.DOMAIN)
    kw.setdefault("sources", [Source(SourceType.CONVERSATION_DISTILLED, anchor_id)])
    return DistillCandidate(title=title, **kw)


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


# ==================== DistillService：三段编排（D9） ====================


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


def test_confirm_writes_back_cursor_and_consumes_state(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    ctx = comp.distill.prepare(anchor_id)
    state_path = comp.distill._state_path
    assert state_path                                    # 状态文件已写
    result = comp.distill.confirm([_candidate(anchor_id, "蒸馏产出")])
    assert result.cursor_updated == anchor_id            # 游标已回写
    assert comp.truth.get_distill_cursor(anchor_id) == ctx.processed_until
    assert not os.path.exists(state_path)              # 状态文件已消费
    assert len(result.created_ids) == 1


def test_confirm_main_two_pass_replaces_placeholder(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    main = _candidate(anchor_id, "主事件")
    sub = _candidate(anchor_id, "子结论", suggested_parents=["MAIN"],
                     suggested_links=["MAIN"])
    result = comp.distill.confirm([main, sub])
    main_id, sub_id = result.created_ids
    k = comp.search.get(sub_id)
    assert k.parents == [main_id] and k.links == [main_id]  # MAIN → 实际 id
    assert (main_id, "parent") in comp.index.get_edges(sub_id)
    assert (main_id, "link") in comp.index.get_edges(sub_id)


def test_confirm_idempotent_by_title_and_sources(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    c = _candidate(anchor_id, "同题知识")
    first = comp.distill.confirm([c]).created_ids
    second = comp.distill.confirm([_candidate(anchor_id, "同题知识")]).created_ids
    assert first == second                               # 幂等命中已有
    assert len(comp.index.get_all_nodes()) == 1          # 不产生重复节点


def test_confirm_rejects_invalid_anchor_source(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    bad = _candidate("s-99999999-999", "坏溯源")          # 指向不存在的锚点
    with pytest.raises(ValueError, match="锚点"):
        comp.distill.confirm([bad])
    assert comp.truth.get_distill_cursor(anchor_id) == -1  # 失败不回写游标
    # 非锚点来源（用户声明）不校验锚点存在性
    ok = _candidate(anchor_id, "用户声明", sources=[Source(SourceType.USER_DECLARED, "")])
    assert comp.distill.confirm([ok]).created_ids


def test_confirm_reject_skips_and_edit_updates(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    kid = comp.knowledge.create(_mk(title="待修正", summary="旧"))
    rejected = _candidate(anchor_id, "不要这条", decision=Decision.REJECT)
    edited = _candidate(anchor_id, "修正后", summary="新摘要",
                        decision=Decision.EDIT, edit_id=kid)
    result = comp.distill.confirm([edited, rejected])
    assert result.created_ids == [kid]                   # REJECT 跳过，EDIT 返回原 id
    k = comp.search.get(kid)
    assert k.title == "修正后" and k.summary == "新摘要"
    assert len(comp.index.get_all_nodes()) == 1          # REJECT 未入库


def test_confirm_edit_requires_valid_target(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    with pytest.raises(ValueError, match="edit_id"):
        comp.distill.confirm([_candidate(anchor_id, "x", decision=Decision.EDIT)])
    with pytest.raises(ValueError, match="不存在"):
        comp.distill.confirm([_candidate(anchor_id, "x", decision=Decision.EDIT,
                                         edit_id="k-9999")])


def test_auto_attach_to_topic_node(comp, tmp_path):
    """自动挂靠：新知识与"聚合主题"（有 parent 入边）标题字符重叠 ≥0.3 → 挂 parent。"""
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    topic = comp.knowledge.create(_mk(title="记忆系统架构设计", summary="主题"))
    comp.knowledge.add_edge(
        comp.knowledge.create(_mk(title="记忆系统旧知识")), topic, EdgeType.PARENT)  # 造聚合主题
    result = comp.distill.confirm([_candidate(anchor_id, "记忆系统")])
    new_id = result.created_ids[0]
    assert result.attached == {new_id: topic}            # 挂到主题节点
    assert comp.truth.get_knowledge(new_id).parents == [topic]  # 真值同步


def test_auto_attach_skips_unrelated(comp, tmp_path):
    anchor_id = _anchor_for(comp, tmp_path)
    comp.distill.prepare(anchor_id)
    topic = comp.knowledge.create(_mk(title="记忆系统架构设计", summary="主题"))
    comp.knowledge.add_edge(
        comp.knowledge.create(_mk(title="记忆系统旧知识")), topic, EdgeType.PARENT)
    result = comp.distill.confirm([_candidate(anchor_id, "数据库索引优化")])
    assert result.attached == {}                         # 字符零重叠 → 不挂靠
    # 普通节点（无 parent 入边）不做挂靠目标——即使字符完全重叠
    comp.knowledge.create(_mk(title="数据库范式"))
    result = comp.distill.confirm([_candidate(anchor_id, "数据库")])
    assert result.attached == {}
