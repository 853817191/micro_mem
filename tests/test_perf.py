"""P1 性能用例（TC-P1~P11）。默认不跑，`pytest -m perf` 单独执行（消 T4）。"""
import glob
import json
import os
import tempfile
import time

import pytest

from micro_mem.api.distiller import Distiller
from micro_mem.api.importer import Importer
from micro_mem.api.reader import MemoryReader
from micro_mem.api.writer import MemoryWriter
from micro_mem.common.config import Config
from micro_mem.common.embedder import HashEmbedder
from micro_mem.common.md_parser import knowledge_from_meta, parse_file
from micro_mem.domain.types import Decision, DistillCandidate, Knowledge, KnowledgeType, Scope
from micro_mem.store.sqlite_store import SqliteNetworkStore

pytestmark = pytest.mark.perf


def _mk(writer, title, body=""):
    k = Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN, title=title,
                  summary=f"{title}的摘要", body=body or title)
    return writer.create_knowledge(k)


@pytest.fixture(scope="session")
def perf_env(tmp_path_factory):
    """独立装配的性能环境：造 200 条中量库，供各性能用例共享（session 级，只造一次）。"""
    base = tmp_path_factory.mktemp("perf")
    cfg_path = base / "config.yaml"
    cfg_path.write_text(f"data_dir: {base.as_posix()}\nembedding_dim: 1024\n", encoding="utf-8")
    cfg = Config(str(cfg_path))
    for d in (cfg.anchors_dir(), cfg.knowledge_dir(), cfg.temp_dir(),
              os.path.dirname(cfg.db_path())):
        os.makedirs(d, exist_ok=True)
    store = SqliteNetworkStore(cfg.db_path(), vec_dim=1024)
    embedder = HashEmbedder(1024)
    writer = MemoryWriter(cfg, store, embedder)
    reader = MemoryReader(cfg, store, embedder)
    distiller = Distiller(cfg, store, writer, reader)
    importer = Importer(cfg, store, writer)
    for i in range(200):
        _mk(writer, f"性能知识{i:03d}", f"性能正文{i:03d} 包含关键词perfword")
    return {"config": cfg, "store": store, "writer": writer, "reader": reader,
            "distiller": distiller, "importer": importer}


def _perf(fn, n, threshold_ms):
    """预热 10 次 → 测 n 次 → 断言平均耗时 ≤ 阈值。"""
    for _ in range(10):
        fn()
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    avg = sum(times) / len(times)
    p95 = times[int(len(times) * 0.95)]
    p99 = times[int(len(times) * 0.99)]
    assert avg <= threshold_ms, \
        f"avg={avg:.1f}ms p95={p95:.1f} p99={p99:.1f} 超过阈值 {threshold_ms}ms"


def _perf_rebuild(env):
    env["store"].clear_all()
    for path in sorted(glob.glob(os.path.join(env["config"].knowledge_dir(), "k-*.md"))):
        meta, body = parse_file(path)
        env["writer"].create_knowledge(knowledge_from_meta(meta, body), skip_md=True)


def _perf_import(env):
    with tempfile.TemporaryDirectory() as td:
        for i in range(10):
            with open(os.path.join(td, f"s{i}.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user",
                                    "message": {"role": "user",
                                                "content": [
                                                    {"type": "text", "text": "perf导入"}]}}) + "\n")
        env["importer"].import_history(td)


def _perf_confirm(env):
    cands = [DistillCandidate(type=KnowledgeType.METHOD, scope=Scope.DOMAIN,
                              title=f"候选{i}", summary="s",
                              decision=Decision.KEEP) for i in range(10)]
    env["distiller"].confirm_distill(cands)


def _perf_graph(env):
    _nodes = [{"id": n.id, "name": n.title, "type": n.type, "scope": n.scope}
              for n in env["store"].get_all_nodes()]
    _edges = [{"source": f, "target": t, "type": et}
              for f, t, et in env["store"].get_all_edges()]


def test_p1_save_anchor(perf_env):
    _perf(lambda: perf_env["writer"].save_anchor("p"), 100, 50)


def test_p2_create_knowledge(perf_env):
    _perf(lambda: _mk(perf_env["writer"], "性能新增"), 100, 50)


def test_p3_update_knowledge(perf_env):
    kid = perf_env["store"].get_all_nodes()[0].id
    _perf(lambda: perf_env["writer"].update_knowledge(kid, summary="x"), 100, 50)


def test_p4_search_keyword(perf_env):
    _perf(lambda: perf_env["reader"].search("性能"), 100, 100)


def test_p5_search_semantic(perf_env):
    _perf(lambda: perf_env["reader"].search("性能知识"), 100, 200)


def test_p6_traverse(perf_env):
    kid = perf_env["store"].get_all_nodes()[0].id
    _perf(lambda: perf_env["reader"].traverse(kid, depth=3), 100, 50)


def test_p7_get(perf_env):
    kid = perf_env["store"].get_all_nodes()[0].id
    _perf(lambda: perf_env["reader"].get(kid), 100, 10)


def test_p8_rebuild(perf_env):
    _perf(lambda: _perf_rebuild(perf_env), 3, 10000)


def test_p9_import_history(perf_env):
    _perf(lambda: _perf_import(perf_env), 3, 5000)


def test_p10_confirm_distill(perf_env):
    _perf(lambda: _perf_confirm(perf_env), 10, 500)


def test_p11_graph(perf_env):
    _perf(lambda: _perf_graph(perf_env), 10, 200)
