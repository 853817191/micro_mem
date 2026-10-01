"""SqliteIndexStore 特有的集成测试（契约之外的实现细节面）。

通用行为契约见 test_index_contract.py（双实现同套件）。
"""
import sqlite3

from micro_mem.domain.models import NodeRecord
from micro_mem.infrastructure.sqlite_index import SqliteIndexStore, _load_schema


def mk_node(kid: str, title: str, **kw) -> NodeRecord:
    kw.setdefault("type", "fact")
    kw.setdefault("scope", "domain")
    return NodeRecord(id=kid, file=f"knowledge/{kid}_{title}.md", title=title,
                      summary=f"{title}的摘要", **kw)


def test_schema_loads_from_package_resource():
    """schema.sql 入包后可读（pip install 不自损的关键）。"""
    schema = _load_schema()
    assert "CREATE TABLE IF NOT EXISTS nodes" in schema
    assert "nodes_fts USING fts5" in schema


def test_reopen_existing_db_is_idempotent(tmp_path):
    """同一 db 二次初始化不炸（schema 幂等）；数据保留。"""
    db = str(tmp_path / "sub" / "memory.db")
    store = SqliteIndexStore(db)
    store.upsert_node(mk_node("k-0001", "持久化"), body="正文")
    store2 = SqliteIndexStore(db)
    node = store2.get_node("k-0001")
    assert node is not None and node.title == "持久化"


def test_db_parent_dir_auto_created(tmp_path):
    """db 路径的父目录不存在时自动创建。"""
    db = str(tmp_path / "a" / "b" / "memory.db")
    SqliteIndexStore(db)
    assert (tmp_path / "a" / "b" / "memory.db").exists()


def test_fts_body_has_no_read_exit(tmp_path):
    """D3：FTS body 是纯索引耗材——实现不提供 body 读出口。"""
    store = SqliteIndexStore(str(tmp_path / "m.db"))
    assert not hasattr(store, "get_fts_body"), "body 读出口已从端口移除（D3）"
    # 但索引耗材确实写入（检索可用）
    store.upsert_node(mk_node("k-0001", "标题"), body="索引耗材词consumable")
    assert [n.id for n in store.search_keyword("consumable")] == ["k-0001"]


def test_semantic_search_with_distance_filter(tmp_path):
    """语义路：同文本向量距离 0 命中；无向量能力时静默返回空（由 sqlite-vec 决定）。"""
    from micro_mem.infrastructure.hash_embedder import HashEmbedder
    store = SqliteIndexStore(str(tmp_path / "m.db"))
    store.upsert_node(mk_node("k-0001", "发酵"))
    emb = HashEmbedder(1024)
    store.save_vector("k-0001", emb.embed("发酵温度控制"))
    hits = store.search_semantic(emb.embed("发酵温度控制"))
    if store._vec_available:
        assert hits and hits[0].id == "k-0001", "同文本最近邻是自己"
    else:
        assert hits == [], "无 vec 扩展时语义检索静默为空"


def test_rowid_not_exposed(tmp_path):
    """rowid 收回实现内部：NodeRecord 无该字段，端口返回值不含物理键。"""
    store = SqliteIndexStore(str(tmp_path / "m.db"))
    store.upsert_node(mk_node("k-0001", "甲"))
    node = store.get_node("k-0001")
    assert node is not None
    assert not hasattr(node, "rowid") or "rowid" not in node.__dataclass_fields__
    # 直接查库验证 rowid 对齐仍在内部生效（FTS 与 nodes 同 rowid）
    conn = sqlite3.connect(store.db_path)
    n_rowid = conn.execute("SELECT rowid FROM nodes WHERE id='k-0001'").fetchone()[0]
    f_rowid = conn.execute("SELECT rowid FROM nodes_fts").fetchone()[0]
    assert n_rowid == f_rowid, "nodes 与 nodes_fts 仍按 rowid 对齐"
