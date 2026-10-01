"""统一 CLI（interfaces/cli.py）单测：注入内存组件，验证输出格式与命令行为。

输出格式是被 CI 守卫的红线（rebuild/search/traverse 有 grep 回归守卫），
改动 cli.py 后必须全绿。
"""
import json
import re

import pytest

from micro_mem.composition import assemble_inmemory
from micro_mem.domain.models import EdgeType, Knowledge, KnowledgeType, Scope
from micro_mem.interfaces.cli import main


@pytest.fixture
def comp():
    # 语义兜底行为在 test_services.py 已覆盖；CLI 层用确定性字面检索
    return assemble_inmemory(semantic_fallback="off", state_path="")


def _mk(title="CLI 测试知识", summary="摘要", body="正文", **kw) -> Knowledge:
    kw.setdefault("type", KnowledgeType.FACT)
    kw.setdefault("scope", Scope.DOMAIN)
    return Knowledge(title=title, summary=summary, body=body, **kw)


# ================= search / get / traverse（CI 守卫格式） =================

def test_search_output_format(comp, capsys):
    comp.knowledge.create(_mk(title="发酵程度看状态不看时间", summary="看状态"))
    main(["search", "发酵"], components=comp)
    out = capsys.readouterr().out
    # CI 守卫 grep "^\[k-"：格式必须保持 [id] title (type/scope) score=
    assert re.search(r"^\[k-0001\] 发酵程度看状态不看时间  \(fact/domain\) score=\d", out)
    assert "\n    看状态\n" in out                               # summary 缩进行


def test_search_multi_and_no_hit(comp, capsys):
    comp.knowledge.create(_mk(title="alpha beta"))
    main(["search", "alpha beta", "--multi", "--limit", "5"], components=comp)
    assert "[k-0001]" in capsys.readouterr().out
    main(["search", "不存在的词xyz"], components=comp)
    assert "无命中" in capsys.readouterr().out


def test_get_output_format(comp, capsys):
    kid = comp.knowledge.create(_mk())
    main(["get", kid], components=comp)
    out = capsys.readouterr().out
    for prefix in ("id:", "title:", "summary:", "parents:", "external_refs:",
                   "sources:", "body:"):
        assert prefix in out
    main(["get", "k-9999"], components=comp)
    assert "未找到" in capsys.readouterr().out


def test_traverse_output_format(comp, capsys):
    a = comp.knowledge.create(_mk(title="甲"))
    b = comp.knowledge.create(_mk(title="乙"))
    comp.knowledge.add_edge(a, b, EdgeType.PARENT)
    main(["traverse", a, "--depth", "2"], components=comp)
    out = capsys.readouterr().out
    # CI 守卫 grep "^  k-0001 -\[parent\]→"：格式必须保持
    assert f"  {a} -[parent]→ {b}  (跳数 1)" in out
    main(["traverse", "k-9999"], components=comp)
    assert "无关联节点" in capsys.readouterr().out


# ================= 写命令 =================

def test_create(comp, capsys):
    main(["create", "--type", "fact", "--scope", "domain",
          "--title", "新收录", "--summary", "S", "--body", "B"], components=comp)
    assert "已收录: k-0001" in capsys.readouterr().out
    assert comp.search.get("k-0001").title == "新收录"


def test_deprecate_and_delete(comp, capsys):
    kid = comp.knowledge.create(_mk())
    main(["deprecate", kid], components=comp)
    assert "已废弃" in capsys.readouterr().out
    main(["delete", kid], components=comp)
    out = capsys.readouterr().out
    assert "已移出索引工作集" in out
    assert comp.truth.get_knowledge(kid) is not None      # 真值保留（D7）
    assert comp.search.get(kid) is None
    main(["delete", "k-9999"], components=comp)
    assert "未找到" in capsys.readouterr().out
    main(["deprecate", "k-9999"], components=comp)
    assert "未找到" in capsys.readouterr().out


def test_rebuild_output_format(comp, capsys):
    comp.knowledge.create(_mk())
    main(["rebuild"], components=comp)
    # CI 守卫 grep "rebuild 完成：[1-9][0-9]* 条知识重建"
    assert "rebuild 完成：1 条知识重建" in capsys.readouterr().out


def test_import(comp, tmp_path, capsys):
    (tmp_path / "s.jsonl").write_text(
        json.dumps({"message": {"role": "user", "content": "历史会话"}},
                   ensure_ascii=False), encoding="utf-8")
    main(["import", "--dir", str(tmp_path)], components=comp)
    out = capsys.readouterr().out
    assert "存量导入完成：锚点 1 个，知识 0 个" in out


# ================= 蒸馏三段 =================

def test_anchor_and_distill_and_confirm(tmp_path, capsys):
    # 该流程要测游标回写，装配必须带真实状态文件路径
    comp = assemble_inmemory(state_path=str(tmp_path / "distill_state.json"))
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(
        json.dumps({"message": {"role": "user", "content": f"第{i}问"}},
                   ensure_ascii=False) for i in range(1, 3)), encoding="utf-8")
    # anchor
    main(["anchor", str(p), "测试会话"], components=comp)
    out = capsys.readouterr().out
    m = re.search(r"锚点已保存: (s-[\d-]+)", out)
    assert m, out
    anchor_id = m.group(1)
    # distill（prepare）
    main(["distill", anchor_id], components=comp)
    out = capsys.readouterr().out
    assert f"锚点: {anchor_id} | 已蒸馏到 turn -1" in out
    assert f"sources.ref 填 {anchor_id}" in out
    assert "第2问" in out                                    # 增量轮次
    # confirm（with MAIN 两段式 + 游标回写）
    cand_file = tmp_path / "candidates.json"
    cand_file.write_text(json.dumps([
        {"type": "event", "scope": "domain", "title": "主事件",
         "summary": "S", "body": "B", "decision": "keep",
         "sources": [{"type": "conversation_distilled", "ref": anchor_id}]},
        {"type": "fact", "scope": "domain", "title": "子结论",
         "decision": "keep", "suggested_parents": ["MAIN"],
         "sources": [{"type": "conversation_distilled", "ref": anchor_id}]},
    ], ensure_ascii=False), encoding="utf-8")
    main(["confirm", str(cand_file)], components=comp)
    out = capsys.readouterr().out
    assert "落库 2 条" in out
    assert f"已更新锚点蒸馏游标: {anchor_id}.distilled_until = 2" in out
    assert "自动挂靠" in out
    sub = comp.search.get("k-0002")
    assert sub is not None and sub.parents == ["k-0001"]   # MAIN → 实际 id
    # prepare 之后：增量为空
    main(["distill", anchor_id], components=comp)
    assert "本轮增量 turn 2..2" in capsys.readouterr().out


def test_confirm_failure_keeps_cursor(tmp_path, capsys):
    comp = assemble_inmemory(state_path=str(tmp_path / "state.json"))
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    main(["distill", anchor_id], components=comp)
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([
        {"type": "fact", "scope": "domain", "title": "坏溯源",
         "sources": [{"type": "conversation_distilled", "ref": "s-99999999-999"}]}
    ], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["confirm", str(bad)], components=comp)
    out = capsys.readouterr().out
    assert "落库失败" in out and "未回写蒸馏游标" in out
    assert comp.truth.get_distill_cursor(anchor_id) == -1


def test_anchor_missing_path(comp, capsys):
    with pytest.raises(SystemExit):
        main(["anchor", "nonexistent.jsonl"], components=comp)
    assert "未找到 jsonl 会话文件" in capsys.readouterr().out
