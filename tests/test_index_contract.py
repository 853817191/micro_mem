"""IndexStore 端口契约测试：同一行为套件跑全部实现（防实现漂移，风险表对策）。

契约约束"找得到/找不到/状态变化"的行为面；排序细节不约束（归各实现：
SQLite 按 FTS rank，内存版按 id 字典序）。检索词统一用 ≥3 字符，
避开 FTS5 trigram 的短词边界（短词召回由 LIKE 兜底，属实现细节）。
"""
import pytest

from micro_mem.application.ports import IndexStore
from micro_mem.domain.models import NodeRecord
from micro_mem.infrastructure.memory_index import InMemoryIndexStore
from micro_mem.infrastructure.sqlite_index import SqliteIndexStore


@pytest.fixture(params=["memory", "sqlite"])
def index(request, tmp_path) -> IndexStore:
    """参数化：同一套件跑两种实现。"""
    if request.param == "memory":
        return InMemoryIndexStore()
    return SqliteIndexStore(str(tmp_path / "test.db"))


def mk_node(kid: str, title: str, **kw) -> NodeRecord:
    kw.setdefault("type", "fact")
    kw.setdefault("scope", "domain")
    return NodeRecord(id=kid, file=f"knowledge/{kid}_{title}.md", title=title,
                      summary=f"{title}的摘要", **kw)


def test_upsert_create_then_overwrite(index):
    """upsert 合一语义：再写覆盖且节点数不增。"""
    index.upsert_node(mk_node("k-0001", "原标题"), body="旧正文")
    index.upsert_node(mk_node("k-0001", "新标题"), body="新正文")
    node = index.get_node("k-0001")
    assert node is not None and node.title == "新标题"
    assert len(index.get_all_nodes()) == 1


def test_upsert_empty_body_then_fill_is_searchable(index):
    """B2 语义根除：空 body 创建 → upsert 补 body → 可检索（两实现都必须过）。"""
    index.upsert_node(mk_node("k-0001", "空body"), body="")
    index.upsert_node(mk_node("k-0001", "空body"), body="独有词uniqueterm")
    assert [n.id for n in index.search_keyword("uniqueterm")] == ["k-0001"]


def test_search_keyword_hit_miss_and_filters(index):
    index.upsert_node(mk_node("k-0001", "温度控制"), body="发酵工艺要点")
    index.upsert_node(mk_node("k-0002", "无关条目", type="method"), body="无关正文")
    assert {n.id for n in index.search_keyword("发酵工艺")} == {"k-0001"}, "body 词可召回"
    assert index.search_keyword("不存在的关键词") == []
    assert index.search_keyword("发酵工艺", type_filter="method") == []
    assert {n.id for n in index.search_keyword("发酵工艺", type_filter="fact")} == {"k-0001"}
    assert index.search_keyword("发酵工艺", scope_filter="personal") == []


def test_delete_node_cascades(index):
    index.upsert_node(mk_node("k-0001", "甲"))
    index.upsert_node(mk_node("k-0002", "乙"))
    index.add_edge("k-0001", "k-0002", "link")
    index.add_external_ref("k-0001", "idev", "TCX-1")
    index.delete_node("k-0001")
    assert index.get_node("k-0001") is None
    assert index.get_edges("k-0002") == [], "入边应随删除清理"
    assert index.get_external_refs("k-0001") == []
    index.delete_node("k-0001")   # 幂等：删不存在不报错


def test_edges_idempotent_and_filtered(index):
    index.upsert_node(mk_node("k-0001", "甲"))
    index.upsert_node(mk_node("k-0002", "乙"))
    index.add_edge("k-0001", "k-0002", "parent")
    index.add_edge("k-0001", "k-0002", "parent")   # 重复建幂等
    index.add_edge("k-0001", "k-0002", "link")
    assert sorted(index.get_edges("k-0001")) == [("k-0002", "link"), ("k-0002", "parent")]
    assert index.get_edges("k-0001", ["parent"]) == [("k-0002", "parent")]
    assert index.has_parent_edge("k-0002") is True
    assert index.has_parent_edge("k-0001") is False
    index.remove_edge("k-0001", "k-0002", "parent")
    assert index.get_edges("k-0001", ["parent"]) == []
    index.remove_edge("k-0001", "k-0002", "parent")   # 删不存在静默


def test_traverse_bidirectional_depth(index):
    for kid in ("k-0001", "k-0002", "k-0003"):
        index.upsert_node(mk_node(kid, kid))
    index.add_edge("k-0001", "k-0002", "parent")
    index.add_edge("k-0002", "k-0003", "link")
    # 双向：从中间出发两个方向都可达
    assert dict((nid, d) for nid, _, d in index.traverse("k-0002", depth=1)) == \
        {"k-0001": 1, "k-0003": 1}
    # 深度限制与跳数标记
    assert dict((nid, d) for nid, _, d in index.traverse("k-0001", depth=2)) == \
        {"k-0002": 1, "k-0003": 2}
    assert [nid for nid, _, _ in index.traverse("k-0001", depth=1)] == ["k-0002"]
    # 边类型过滤
    assert index.traverse("k-0001", depth=2, edge_types=["trace"]) == []
    # 起点不计入结果
    assert all(nid != "k-0001" for nid, _, _ in index.traverse("k-0001", depth=2))


def test_external_refs_roundtrip(index):
    index.upsert_node(mk_node("k-0001", "甲"))
    index.add_external_ref("k-0001", "idev", "TCX-1")
    index.add_external_ref("k-0001", "idev", "TCX-1")   # 幂等
    index.add_external_ref("k-0001", "url", "https://x")
    assert sorted(index.get_external_refs("k-0001")) == [("idev", "TCX-1"), ("url", "https://x")]
    index.remove_external_ref("k-0001", "idev", "TCX-1")
    assert index.get_external_refs("k-0001") == [("url", "https://x")]


def test_clear_all(index):
    index.upsert_node(mk_node("k-0001", "甲"), body="正文")
    index.add_edge("k-0001", "k-0001", "link")
    index.add_external_ref("k-0001", "idev", "TCX-1")
    index.clear_all()
    assert index.get_all_nodes() == []
    assert index.get_all_edges() == []
    assert index.get_external_refs("k-0001") == []
    assert index.search_keyword("正文") == []
