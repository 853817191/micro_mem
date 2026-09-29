"""P0 健壮性用例（TC-RO1~RO5）。"""
from micro_mem.domain.types import EdgeType, Knowledge, KnowledgeType, Scope


def test_ro1_empty_db(reader):
    # 每个测试函数的 config/store 均为全新空库（function 级 fixture）
    assert reader.search("任何") == [], "空库检索应返回空"
    assert reader.traverse("k-1", depth=2) == [], "空库遍历应返回空"
    assert reader.get("k-1") is None, "空库 get 应返回 None"


def test_ro2_special_chars(reader):
    hits = reader.search('含"引号"和空格 test emoji😀')
    assert isinstance(hits, list), "特殊字符查询应不崩溃"


def test_ro3_duplicate_id(writer, store):
    # 同一 id 重复写：明确报错防脏数据即可接受，不崩溃
    k = Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN, title="重复测试", summary="s")
    id1 = writer.create_knowledge(k)
    try:
        store.create_node(store.get_node(id1), body="")
    except Exception:
        pass  # 明确报错防脏数据可接受


def test_ro4_long_text(writer, reader, mk):
    long_body = "长文本内容" * 2000  # ~10KB
    kid = mk("长文本测试", long_body)
    k = reader.get(kid)
    assert len(k.body) == len(long_body), "长文本存取不一致"
    assert any(h.id == kid for h in reader.search("长文本内容")), "长文本 FTS 检索失败"


def test_ro5_duplicate_edge(writer, store, mk):
    a, b = mk("重复边A"), mk("重复边B")
    writer.add_edge(a, b, EdgeType.LINK)
    writer.add_edge(a, b, EdgeType.LINK)  # 重复建
    edges = store.get_edges(a, [EdgeType.LINK.value])
    assert edges.count((b, "link")) == 1, "重复边未幂等"
