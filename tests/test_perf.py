"""P1 性能用例（TC-P1~P11，新栈版）：写/读/图/重建/导入/蒸馏落库全链路阈值。

默认不跑，`pytest -m perf` 单独执行。阈值沿用旧栈基线（200 条中量库）：
新栈索引写路径多了"全量替换"语义，理论更慢但应仍在同量级；
若阈值失效（机器差异），调阈值不改断言语义。
"""
import json
import os
import tempfile
import time

import pytest

from micro_mem.composition import assemble
from micro_mem.domain.models import (
    DistillPlan,
    DomainRoot,
    Knowledge,
    KnowledgeType,
    PlanAction,
    PlanItem,
    Scope,
)

pytestmark = pytest.mark.perf


@pytest.fixture(scope="session")
def perf_env(tmp_path_factory):
    """独立装配的性能环境：200 条中量库，各用例共享（session 级，只造一次）。"""
    base = tmp_path_factory.mktemp("perf")
    cfg_path = base / "config.yaml"
    cfg_path.write_text(f"data_dir: {base.as_posix()}\nembedding_dim: 1024\n",
                        encoding="utf-8")
    comp = assemble(str(cfg_path))
    for i in range(200):
        comp.knowledge.create(Knowledge(
            type=KnowledgeType.FACT, scope=Scope.DOMAIN,
            title=f"性能知识{i:03d}", summary=f"性能知识{i:03d}的摘要",
            body=f"性能正文{i:03d} 包含关键词perfword"))
    return comp


def _mk(comp, title, body=""):
    return comp.knowledge.create(Knowledge(
        type=KnowledgeType.FACT, scope=Scope.DOMAIN, title=title,
        summary=f"{title}的摘要", body=body or title))


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
        f"avg={avg:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms 超过阈值 {threshold_ms}ms"


def test_p1_save_anchor(perf_env):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as f:
        f.write(json.dumps({"message": {"role": "user", "content": "p"}},
                           ensure_ascii=False))
        path = f.name
    _perf(lambda: perf_env.distill.anchor(path), 100, 50)
    os.unlink(path)


def test_p2_create_knowledge(perf_env):
    _perf(lambda: _mk(perf_env, "性能新增"), 100, 50)


def test_p3_update_knowledge(perf_env):
    kid = perf_env.index.get_all_nodes()[0].id
    _perf(lambda: perf_env.knowledge.update(kid, summary="x"), 100, 50)


def test_p4_search_keyword(perf_env):
    _perf(lambda: perf_env.search.search("性能"), 100, 100)


def test_p5_search_semantic(perf_env):
    _perf(lambda: perf_env.search.search("性能知识"), 100, 200)


def test_p6_traverse(perf_env):
    kid = perf_env.index.get_all_nodes()[0].id
    _perf(lambda: perf_env.search.traverse(kid, depth=3), 100, 50)


def test_p7_get(perf_env):
    kid = perf_env.index.get_all_nodes()[0].id
    _perf(lambda: perf_env.search.get(kid), 100, 10)


def test_p8_rebuild(perf_env):
    _perf(lambda: perf_env.rebuild.rebuild(), 3, 10000)


def test_p9_import_history(perf_env):
    def _import():
        with tempfile.TemporaryDirectory() as td:
            for i in range(10):
                with open(os.path.join(td, f"s{i}.jsonl"), "w", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"type": "user",
                         "message": {"role": "user",
                                     "content": [{"type": "text", "text": "perf导入"}]}},
                        ensure_ascii=False) + "\n")
            perf_env.importer.import_history(td)
    _perf(_import, 3, 5000)


def test_p10_confirm_distill(perf_env):
    """蒸馏落库编排：submit（校验+回填）→ 填正文 → confirm（10 项计划）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                     encoding="utf-8") as f:
        f.write(json.dumps({"message": {"role": "user", "content": "p10"}},
                           ensure_ascii=False))
        path = f.name
    anchor_id = perf_env.distill.anchor(path)
    root = perf_env.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="p10领域根"))

    def _confirm():
        items = [PlanItem(action=PlanAction.CREATE, gist=f"候选{i}",
                          title=f"候选{i}", aspect="flow", parent=root,
                          source_anchor=anchor_id, source_turns=[1])
                 for i in range(10)]
        plan, _ = perf_env.distill.submit_plan(DistillPlan(
            anchor=anchor_id, domain_root=DomainRoot(action="existing", id=root),
            items=items))
        plan = perf_env.truth.get_plan(plan.plan_id)
        for it in plan.items:                      # 模拟 R3 填正文
            it.summary, it.body = "s", "b"
        perf_env.truth.save_plan(plan)
        # 重名 title 属预期（重复性能测量），force 放行警告
        perf_env.distill.confirm_plan(plan.plan_id, force=True)
    try:
        _perf(_confirm, 10, 800)   # 计划契约流程含档案落盘×3+全规则校验×2，阈值上调
    finally:
        os.unlink(path)


def test_p11_graph(perf_env):
    def _graph():
        _nodes = [{"id": n.id, "name": n.title, "type": n.type, "scope": n.scope}
                  for n in perf_env.index.get_all_nodes()]
        _edges = [{"source": f, "target": t, "type": et}
                  for f, t, et in perf_env.index.get_all_edges()]
    _perf(_graph, 10, 200)
