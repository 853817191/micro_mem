"""阶段1 骨架验证：InMemory 端口实现 + 装配根。

只测行为契约（id 递增、找得到/找不到、游标回写、遍历跳数、副本隔离），
不测排序细节（归各实现）。这些用例同时是阶段3 服务层单测的地基。
"""
from datetime import datetime

from micro_mem.application.ports import Embedder, IndexStore, TruthStore
from micro_mem.composition import assemble_inmemory
from micro_mem.domain.models import Anchor, Knowledge, KnowledgeType, NodeRecord, Scope

# ---------------- 工厂 ----------------


def mk_knowledge(title: str, body: str = "", **kw) -> Knowledge:
    return Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN,
                     title=title, summary=f"{title}的摘要", body=body, **kw)


def mk_node(kid: str, title: str, **kw) -> NodeRecord:
    kw.setdefault("type", "fact")
    kw.setdefault("scope", "domain")
    return NodeRecord(id=kid, file=f"knowledge/{kid}_{title}.md", title=title,
                      summary=f"{title}的摘要", **kw)


# ---------------- TruthStore ----------------


def test_truth_save_assigns_incremental_ids():
    c = assemble_inmemory()
    k1 = mk_knowledge("第一条")
    k2 = mk_knowledge("第二条")
    assert c.truth.save_knowledge(k1) == "k-0001"
    assert c.truth.save_knowledge(k2) == "k-0002"
    assert c.truth.get_knowledge("k-0001").title == "第一条"


def test_truth_upsert_keeps_created_and_refreshes_updated():
    c = assemble_inmemory()
    kid = c.truth.save_knowledge(mk_knowledge("原文"))
    first = c.truth.get_knowledge(kid)
    k2 = mk_knowledge("改后", id=kid, created=first.created)
    c.truth.save_knowledge(k2)
    got = c.truth.get_knowledge(kid)
    assert got.title == "改后", "按已有 id 保存应为覆盖"
    assert got.created == first.created, "created 不应被覆盖"
    assert len(c.truth.list_knowledge()) == 1, "upsert 不应产生第二条"


def test_truth_get_returns_full_body_and_copy_isolated():
    c = assemble_inmemory()
    kid = c.truth.save_knowledge(mk_knowledge("副本", body="正文内容"))
    got = c.truth.get_knowledge(kid)
    assert got.body == "正文内容", "get 必须含全量 body（D3）"
    got.title = "改返回值"
    assert c.truth.get_knowledge(kid).title == "副本", "返回值必须是副本，改它不污染库"


def test_truth_delete_knowledge():
    c = assemble_inmemory()
    kid = c.truth.save_knowledge(mk_knowledge("待删"))
    assert c.truth.delete_knowledge(kid) is True
    assert c.truth.get_knowledge(kid) is None
    assert c.truth.delete_knowledge(kid) is False, "重复删除应返回 False"


def test_truth_anchor_lifecycle_and_cursor():
    c = assemble_inmemory()
    aid = c.truth.save_anchor(Anchor(id="", title="会话", date="", content="原文"))
    today = datetime.now().strftime("%Y%m%d")
    assert aid == f"s-{today}-001"
    assert c.truth.get_distill_cursor(aid) == -1, "新锚点游标应为 -1（未蒸馏）"
    c.truth.set_distill_cursor(aid, 5)
    assert c.truth.get_distill_cursor(aid) == 5
    # resync：换正文、保游标
    assert c.truth.resync_anchor(aid, "增长后的全文") is True
    a = c.truth.get_anchor(aid)
    assert a.content == "增长后的全文" and a.distilled_until == 5
    assert c.truth.resync_anchor("s-20990101-999", "x") is False


def test_truth_anchor_exists_accepts_both_ref_forms():
    c = assemble_inmemory()
    aid = c.truth.save_anchor(Anchor(id="", title="t", date="", content="c"))
    assert c.truth.anchor_exists(aid), "纯 id 形态"
    assert c.truth.anchor_exists(f"anchors/{aid}.md"), "引用路径形态"
    assert not c.truth.anchor_exists("anchors/s-20990101-999.md")


# ---------------- IndexStore ----------------


def test_index_upsert_is_full_replace():
    """B2 语义根除验证：upsert 合一后，"空 body 再补 body"只是普通二次写入。"""
    c = assemble_inmemory()
    c.index.upsert_node(mk_node("k-0001", "空body"), body="")
    c.index.upsert_node(mk_node("k-0001", "空body"), body="独有词uniqueterm")
    hits = c.index.search_keyword("uniqueterm")
    assert [h.id for h in hits] == ["k-0001"], "补写的 body 必须可检索"
    assert len(c.index.get_all_nodes()) == 1, "upsert 不产生重复节点"


def test_index_search_keyword_filters_and_finds_body():
    c = assemble_inmemory()
    c.index.upsert_node(mk_node("k-0001", "温度控制"), body="发酵工艺要点")
    c.index.upsert_node(mk_node("k-0002", "无关", type="method"), body="发酵")
    assert [h.id for h in c.index.search_keyword("发酵", type_filter="fact")] == ["k-0001"]
    assert {h.id for h in c.index.search_keyword("发酵")} == {"k-0001", "k-0002"}
    assert c.index.search_keyword("不存在的词") == []


def test_index_delete_node_cascades():
    c = assemble_inmemory()
    c.index.upsert_node(mk_node("k-0001", "A"))
    c.index.upsert_node(mk_node("k-0002", "B"))
    c.index.add_edge("k-0001", "k-0002", "link")
    c.index.add_external_ref("k-0001", "idev", "TCX-1")
    c.index.save_vector("k-0001", c.embedder.embed("A的摘要"))
    c.index.delete_node("k-0001")
    assert c.index.get_node("k-0001") is None
    assert c.index.get_all_edges() == [], "边应连带删除"
    assert c.index.get_external_refs("k-0001") == [], "外部锚点应连带删除"
    assert c.index.search_semantic(c.embedder.embed("A的摘要")) == [], "向量应连带删除"


def test_index_traverse_bidirectional_with_depth():
    c = assemble_inmemory()
    for kid in ("k-0001", "k-0002", "k-0003"):
        c.index.upsert_node(mk_node(kid, kid))
    c.index.add_edge("k-0001", "k-0002", "parent")
    c.index.add_edge("k-0002", "k-0003", "link")
    # 从中间节点出发：双向各一跳
    reached = dict((nid, d) for nid, _, d in c.index.traverse("k-0002", depth=1))
    assert reached == {"k-0001": 1, "k-0003": 1}
    # 从端点出发：depth=1 只到中间，depth=2 到对面
    assert [nid for nid, _, _ in c.index.traverse("k-0001", depth=1)] == ["k-0002"]
    assert dict((nid, d) for nid, _, d in c.index.traverse("k-0001", depth=2)) == \
        {"k-0002": 1, "k-0003": 2}
    # 边类型过滤
    assert c.index.traverse("k-0001", depth=2, edge_types=["link"]) == []


def test_index_semantic_nearest_neighbor():
    c = assemble_inmemory()
    c.index.upsert_node(mk_node("k-0001", "发酵"))
    c.index.upsert_node(mk_node("k-0002", "烹饪"))
    c.index.save_vector("k-0001", c.embedder.embed("发酵温度控制"))
    c.index.save_vector("k-0002", c.embedder.embed("量子力学基本原理"))
    hits = c.index.search_semantic(c.embedder.embed("发酵温度控制"))
    assert hits[0].id == "k-0001", "同文本向量距离应为 0，最近邻是自己"


# ---------------- 装配根 ----------------


def test_assemble_inmemory_satisfies_ports():
    c = assemble_inmemory()
    assert isinstance(c.truth, TruthStore)
    assert isinstance(c.index, IndexStore)
    assert isinstance(c.embedder, Embedder)
    assert c.embedder.dim == 1024
