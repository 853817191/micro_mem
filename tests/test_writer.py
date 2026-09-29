"""P0 Writer 用例（TC-W1~W8）。"""
import os

from micro_mem.api.reader import MemoryReader
from micro_mem.domain.types import EdgeType, RefType


def test_w1_create_knowledge_basic(writer, store, config, mk):
    kid = mk("测试知识1", "测试正文内容abc")
    assert os.path.exists(
        os.path.join(config.knowledge_dir(), f"{kid}_测试知识1.md")), "md 文件未生成"
    assert store.get_node(kid) is not None, "索引未写入"


def test_w2_id_autoincrement(mk):
    ids = [mk(f"序列{i}") for i in range(3)]
    assert len(set(ids)) == 3, "id 未递增唯一"


def test_w3_save_anchor(writer, config):
    aid = writer.save_anchor("测试对话内容")
    assert os.path.exists(os.path.join(config.anchors_dir(), f"{aid}.md")), "锚点文件未生成"
    aid2 = writer.save_anchor("另一段")
    assert aid != aid2, "锚点 id 重复"


def test_w4_update_metadata(writer, reader, mk):
    kid = mk("元数据测试")
    writer.update_knowledge(kid, title="元数据改后", status="settled")
    k = reader.get(kid)
    assert k.title == "元数据改后", "title 未更新"
    assert k.status.value == "settled", "status 未更新"
    assert any(h.id == kid for h in reader.search("元数据改后")), "新 title 检索不到"


def test_w5_update_body_fts(writer, mk, config, store):
    # 用字面路验证（无 embedder）：语义路对任意查询返回"最近邻"是向量检索正常行为，
    # 会干扰"旧词应消失"断言
    r_plain = MemoryReader(config, store)
    kid = mk("FTS同步测试", "旧内容词oldword")
    writer.update_knowledge(kid, body="新内容词newword")
    assert any(h.id == kid for h in r_plain.search("newword")), "新词搜不到"
    assert not any(h.id == kid for h in r_plain.search("oldword")), "旧词仍可搜到(FTS 未同步)"


def test_w6_deprecate(writer, reader, mk):
    kid = mk("废弃测试")
    writer.deprecate_knowledge(kid)
    k = reader.get(kid)
    assert k is not None, "废弃不应删除数据"
    assert k.status.value == "deprecated", "status 未变 deprecated"


def test_w7_add_remove_edge(writer, store, mk):
    a, b = mk("边A"), mk("边B")
    writer.add_edge(a, b, EdgeType.LINK)
    assert (b, "link") in store.get_edges(a), "边未建立"
    writer.remove_edge(a, b, EdgeType.LINK)
    assert (b, "link") not in store.get_edges(a), "边未删除"


def test_w8_add_remove_external_ref(writer, store, mk):
    kid = mk("外部锚点测试")
    writer.add_external_ref(kid, RefType.IDEV, "idev-123")
    assert ("idev", "idev-123") in store.get_external_refs(kid), "外部锚点未加"
    writer.remove_external_ref(kid, RefType.IDEV, "idev-123")
    assert ("idev", "idev-123") not in store.get_external_refs(kid), "外部锚点未删"
