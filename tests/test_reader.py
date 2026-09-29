"""P0 Reader 用例（TC-R1~R8）。"""
from micro_mem.domain.types import Knowledge, KnowledgeType, RefType, Scope


def test_r1_keyword_hit(reader, mk):
    mk("领导审批环节", "审批条件：金额大于10万")
    mk("供应商出票环节", "出票状态机")
    hits = reader.search("审批")
    assert any("领导审批" in h.title for h in hits), "关键词未命中"


def test_r2_chinese_2char(reader, mk):
    mk("领导审批环节", "审批条件：金额大于10万")
    hits = reader.search("审批")  # 2字词
    assert any("领导审批" in h.title for h in hits), "2字词未命中(LIKE兜底失败)"


def test_r3_search_filter(reader, mk):
    mk("领导审批环节", "审批条件：金额大于10万")
    hits = reader.search("审批", type=KnowledgeType.FACT, scope=Scope.DOMAIN)
    assert hits and all(h.type == KnowledgeType.FACT for h in hits), "过滤未生效"


def test_r4_search_no_hit(reader):
    # 语义路（向量）对任意查询都返回最近邻是正常行为；此处只要求不报错、返回列表
    hits = reader.search("不存在的词xyz")
    assert isinstance(hits, list), "无命中应返回列表不报错"


def test_r5_traverse_bidirectional(writer, reader, mk):
    parent = mk("父节点X")
    child = writer.create_knowledge(Knowledge(
        type=KnowledgeType.EVENT, scope=Scope.DOMAIN, title="子节点Y",
        summary="子节点Y摘要", parents=[parent]))
    # 从父 traverse 到子（入边，双向）
    assert any(t == child for t, e, d in reader.traverse(parent, depth=2)), "父→子 双向遍历失败"
    # 从子 traverse 到父（出边）
    assert any(t == parent for t, e, d in reader.traverse(child, depth=2)), "子→父 双向遍历失败"


def test_r6_traverse_multi_hop(writer, reader, mk):
    # 造链 A→B→C
    c = mk("链C")
    b = writer.create_knowledge(Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN,
                                          title="链B", summary="链B摘要", links=[c]))
    a = writer.create_knowledge(Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN,
                                          title="链A", summary="链A摘要", links=[b]))
    results = reader.traverse(a, depth=3)
    assert any(t == c for t, e, d in results), "多跳遍历未到3跳"


def test_r7_get_full(writer, reader, mk):
    kid = mk("详情测试", "详情正文内容")
    writer.add_external_ref(kid, RefType.MR, "mr-1")
    k = reader.get(kid)
    assert k.title == "详情测试" and k.body == "详情正文内容", "get 字段缺失"
    assert k.external_refs and k.external_refs[0].value == "mr-1", "外部锚点未回读"


def test_r8_get_invalid(reader):
    assert reader.get("k-999999") is None, "无效 id 应返回 None"
