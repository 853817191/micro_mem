"""P2 可视化 API 逻辑（TC-E1~E6，等价 server API 数据链路，不依赖 HTTP 层）。"""
import os

from micro_mem.domain.types import Knowledge, KnowledgeType, Scope


def test_e1_stats(mk, store):
    mk("API测试知识A", "API正文内容")
    nodes = store.get_all_nodes()
    type_dist = {}
    for n in nodes:
        type_dist[n.type] = type_dist.get(n.type, 0) + 1
    assert nodes and sum(type_dist.values()) == len(nodes), "stats 节点数与类型分布不一致"


def test_e2_graph(mk, store):
    mk("API测试知识A", "API正文内容")
    nodes = store.get_all_nodes()
    edges = store.get_all_edges()
    assert all(n.id and n.title for n in nodes), "graph 节点缺 id/title"
    assert all(len(e) == 3 for e in edges), "graph 边结构错误"


def test_e3_node_detail(mk, reader):
    kid = mk("详情API", "详情正文内容")
    k = reader.get(kid)
    data = {"id": k.id, "title": k.title, "summary": k.summary,
            "body": k.body, "type": k.type.value}
    assert data["title"] == "详情API" and data["body"] == "详情正文内容", "node 详情数据错误"


def test_e4_search(mk, reader):
    mk("API测试知识A", "API正文内容")
    mk("API测试知识B", "B正文")
    hits = reader.search("API测试知识")
    assert any(h.title == "API测试知识A" for h in hits), "API search 未命中"


def test_e5_traverse(writer, reader, mk):
    a = mk("traverseAPI_A")
    b = writer.create_knowledge(Knowledge(
        type=KnowledgeType.FACT, scope=Scope.DOMAIN, title="traverseAPI_B",
        summary="b摘要", parents=[a]))
    assert any(t == b for t, e, d in reader.traverse(a, depth=2)), "API traverse 未到子节点"


def test_e6_anchors(writer, config, mk):
    mk("API测试知识A", "API正文内容")
    writer.save_anchor("API锚点测试内容")
    anchors = [f for f in os.listdir(config.anchors_dir()) if f.endswith(".md")]
    assert len(anchors) >= 1, "API anchors 列表为空"
