"""统一 CLI（interfaces/cli.py）单测：注入内存组件，验证输出格式与命令行为。

输出格式是被 CI 守卫的红线（rebuild/search/traverse 有 grep 回归守卫），
改动 cli.py 后必须全绿。
"""
import json
import re

import pytest

from micro_mem.composition import assemble_inmemory
from micro_mem.domain.models import (
    Anchor,
    EdgeType,
    Knowledge,
    KnowledgeType,
    Scope,
)
from micro_mem.interfaces.cli import main


@pytest.fixture
def comp():
    # 语义兜底行为在 test_services.py 已覆盖；CLI 层用确定性字面检索
    return assemble_inmemory(semantic_fallback="off")


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


# ================= 蒸馏（计划契约流程） =================

def test_anchor_distill_plan_confirm_flow(tmp_path, capsys):
    """全流程：anchor → distill → plan（归档）→ R3 填正文 → confirm（落库+游标）。"""
    comp = assemble_inmemory(aspects=["flow", "structure", "boundary", "constraint"])
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
    assert "【轴模板】" in out                                    # 轴先行：供给视图带筛子
    assert "── flow ──" in out and "── constraint ──" in out
    assert f"source.anchor 填 {anchor_id}" in out
    assert "第2问" in out                                    # 增量轮次（预算内全量）
    assert "【相关已有子树（预检索）】" in out
    assert "无相关已有节点" in out                           # 空库零命中
    assert f"data/anchors/{anchor_id}.md" in out            # 下钻指引
    # plan（提交 AI 手写的 draft；轴先行：四轴全建，空轴=底座随 delta 轮验证）
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({
        "driver": "session", "mode": "domain",
        "domain_root": {"action": "create", "title": "测试领域",
                        "summary": "根摘要", "body": "根正文"},
        "anchor": anchor_id,
        "items": [
            {"action": "create", "aspect": "flow", "parent": "$ROOT",
             "title": "流程", "gist": "流程轴节点",
             "source": {"anchor": anchor_id, "turns": [1]}},
            {"action": "create", "aspect": "flow", "parent": "$AXIS:flow",
             "title": "第一轮流程", "gist": "第1问的流程知识",
             "source": {"anchor": anchor_id, "turns": [1]}},
            {"action": "skip", "gist": "第2问是闲聊，无领域知识",
             "source": {"anchor": anchor_id, "turns": [2]}},
            {"action": "create", "aspect": "structure", "parent": "$ROOT",
             "title": "结构", "gist": "结构轴节点（空轴底座）",
             "source": {"anchor": anchor_id, "turns": [1, 2]}},
            {"action": "create", "aspect": "boundary", "parent": "$ROOT",
             "title": "边界", "gist": "边界轴节点（空轴底座）",
             "source": {"anchor": anchor_id, "turns": [1, 2]}},
            {"action": "create", "aspect": "constraint", "parent": "$ROOT",
             "title": "约束", "gist": "约束轴节点（空轴底座）",
             "source": {"anchor": anchor_id, "turns": [1, 2]}},
        ]}, ensure_ascii=False), encoding="utf-8")
    main(["plan", str(plan_file)], components=comp)
    out = capsys.readouterr().out
    m = re.search(r"计划已归档: (plan-[\d-]+)", out)
    assert m, out
    plan_id = m.group(1)
    assert "增量轮次全集: [1, 2]" in out
    # R3：AI 在归档文件上原地填充正文（单文件演进；轴节点与叶子都填）
    plan = comp.truth.get_plan(plan_id)
    plan.items[0].summary = "流程轴摘要"
    plan.items[0].body = "流程轴正文"
    plan.items[1].summary = "流程摘要"
    plan.items[1].body = "流程正文"
    for i in (3, 4, 5):                       # 空轴底座也填占位正文
        plan.items[i].summary = f"{plan.items[i].title}轴摘要"
        plan.items[i].body = f"{plan.items[i].title}轴正文"
    comp.truth.save_plan(plan)
    # confirm（$ROOT/$AXIS 三段式 + 游标回写 + 计划回写）
    main(["confirm", plan_id], components=comp)
    out = capsys.readouterr().out
    assert "落库完成" in out and "新建 6 条" in out and "跳过 1 条" in out
    assert f"已更新锚点蒸馏游标: {anchor_id}.distilled_until = 2" in out
    root = comp.search.get("k-0001")
    assert root is not None and root.title == "测试领域" and root.aspect == ""
    axis_node = comp.search.get("k-0002")
    assert axis_node is not None
    assert axis_node.parents == ["k-0001"]                   # $ROOT → 实际 id
    assert axis_node.aspect == "flow"
    assert comp.search.get("k-0003").aspect == "structure"   # 空轴底座落库
    assert comp.search.get("k-0004").aspect == "boundary"
    assert comp.search.get("k-0005").aspect == "constraint"
    leaf = comp.search.get("k-0006")
    assert leaf is not None
    assert leaf.parents == ["k-0002"]                        # $AXIS:flow → 轴节点 id
    assert leaf.aspect == "flow" and leaf.sources[0].turns == [1]
    archived = comp.truth.get_plan(plan_id)
    assert archived.status == "confirmed"
    assert archived.items[0].result_knowledge_id == "k-0002"
    assert archived.items[1].result_knowledge_id == "k-0006"  # 叶子最后落
    # prepare 之后：增量为空
    main(["distill", anchor_id], components=comp)
    assert "本轮增量 turn 2..2" in capsys.readouterr().out


def test_plan_rejected_on_bad_coordinates(tmp_path, capsys):
    """坏坐标计划：mem plan 拒收（列全部问题），零归档、游标不动。"""
    comp = assemble_inmemory()
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    bad = tmp_path / "bad_plan.json"
    bad.write_text(json.dumps({
        "anchor": "s-99999999-999",
        "domain_root": {"action": "existing", "id": "k-9999"},
        "items": [{"action": "create", "title": "坏坐标",
                   "source": {"anchor": "s-99999999-999"}}],
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["plan", str(bad)], components=comp)
    out = capsys.readouterr().out
    assert "校验未通过" in out and "锚点不存在" in out
    assert comp.truth.list_plans() == []
    assert comp.truth.get_distill_cursor(anchor_id) == -1


# ================= 计划 JSON 结构校验（白名单：未知字段/缺必填/枚举非法） =================

def _submit(comp, tmp_path, capsys, plan_dict) -> str:
    """写计划文件并提交，返回捕获的输出（SystemExit 由调用方断言）。"""
    f = tmp_path / "p.json"
    f.write_text(json.dumps(plan_dict, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["plan", str(f)], components=comp)
    return capsys.readouterr().out


def test_plan_schema_rejects_unknown_item_field(comp, tmp_path, capsys):
    """字段拼错（aspects）不再被静默忽略：精确到 items[i] 报错。"""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    out = _submit(comp, tmp_path, capsys, {
        "domain_root": {"action": "create", "title": "领域"},
        "anchor": anchor_id,
        "items": [{"action": "create", "aspects": "flow",   # 拼错：应为 aspect
                   "parent": "$ROOT", "title": "流程",
                   "source": {"anchor": anchor_id, "turns": [1]}}]})
    assert "结构校验未通过" in out
    assert "items[0] 未知字段: aspects" in out
    assert comp.truth.list_plans() == []                   # 未提交、未归档


def test_plan_schema_rejects_system_field_in_draft(comp, tmp_path, capsys):
    """系统管理字段（plan_id/status…）混入手写 draft → 显式拒收。"""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    out = _submit(comp, tmp_path, capsys, {
        "mode": "domain", "anchor": anchor_id,
        "plan_id": "plan-20990101-001",                     # 从归档文件抄来的
        "domain_root": {"action": "create", "title": "领域"},
        "items": [{"action": "skip", "gist": "闲聊",
                   "source": {"anchor": anchor_id, "turns": [1]}}]})
    assert "系统管理字段不允许出现在 draft: plan_id" in out


def test_plan_schema_rejects_missing_action_and_bad_mode(comp, tmp_path, capsys):
    """缺 action / mode 枚举非法：一次性报全。"""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    out = _submit(comp, tmp_path, capsys, {
        "mode": "bogus", "anchor": anchor_id,
        "domain_root": {"action": "create", "title": "领域"},
        "items": [{"title": "无 action", "aspect": "flow", "parent": "$ROOT",
                   "source": {"anchor": anchor_id, "turns": [1]}}]})
    assert "mode 非法" in out and "'bogus'" in out
    assert "items[0] 缺 action" in out


def test_plan_schema_rejects_missing_source(comp, tmp_path, capsys):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"message": {"role": "user", "content": "问"}},
                            ensure_ascii=False), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    out = _submit(comp, tmp_path, capsys, {
        "anchor": anchor_id,
        "domain_root": {"action": "create", "title": "领域"},
        "items": [{"action": "create", "aspect": "flow", "parent": "$ROOT",
                   "title": "流程"}]})                       # 整个 source 缺失
    assert "items[0] 缺 source" in out
    assert comp.truth.list_plans() == []


# ================= R2 审查视图（mem review：计划树 + 依据段，只读） =================

def test_review_renders_tree_and_evidence(comp, tmp_path, capsys):
    """审查视图：计划树（根→轴→叶子）+ gist + 依据段 + 跳过区 + 机械统计。"""
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(
        json.dumps({"message": {"role": "user", "content": f"第{i}轮内容" }},
                   ensure_ascii=False) for i in range(1, 3)), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({
        "driver": "session", "mode": "domain",
        "domain_root": {"action": "create", "title": "测试领域"},
        "anchor": anchor_id,
        "items": [
            {"action": "create", "aspect": "flow", "parent": "$ROOT",
             "title": "流程", "gist": "建流程轴",
             "source": {"anchor": anchor_id, "turns": [1]}},
            {"action": "create", "aspect": "flow", "parent": "$AXIS:flow",
             "title": "第一轮流程", "gist": "第1轮流程知识",
             "source": {"anchor": anchor_id, "turns": [1]}},
            {"action": "create", "aspect": "structure", "parent": "$ROOT",
             "title": "结构", "gist": "结构轴（空轴底座：已逐轮检查）",
             "source": {"anchor": anchor_id, "turns": [1, 2]}},
            {"action": "skip", "gist": "第2轮是闲聊",
             "source": {"anchor": anchor_id, "turns": [2]}},
        ]}, ensure_ascii=False), encoding="utf-8")
    main(["review", str(plan_file)], components=comp)
    out = capsys.readouterr().out
    assert "【计划审查】driver=session mode=domain" in out
    assert f"锚点 {anchor_id}" in out
    assert "领域根: 测试领域（create 新领域）" in out
    assert "── 计划树 ──" in out
    assert "[create] 流程 axis=flow" in out                  # 轴层
    assert "[create] 第一轮流程 axis=flow" in out            # 叶子层（归组在 flow 下）
    assert "gist: 建流程轴" in out
    assert "依据:" in out and "turn 1: ## user 第1轮内容" in out  # 原文摘录
    assert "── 跳过（1 条）──" in out and "第2轮是闲聊" in out
    assert "覆盖: 2/2 轮（turn 1..2 全覆盖）" in out
    assert "轴建齐: 2/4（缺 ['boundary', 'constraint']）" in out
    assert "动作: create 3 / skip 1" in out
    assert comp.truth.list_plans() == []                   # 只读：不归档


def test_review_schema_error_blocks_render(comp, tmp_path, capsys):
    """结构不合法的计划：review 同样挡在白名单，不渲染。"""
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({
        "anchor": "s-1", "domain_root": {"action": "create", "title": "x"},
        "items": [{"action": "create", "aspects": "flow",
                   "source": {"anchor": "s-1"}}],
    }, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["review", str(f)], components=comp)
    out = capsys.readouterr().out
    assert "结构校验未通过" in out and "未知字段: aspects" in out


def test_review_missing_coverage_shown(comp, tmp_path, capsys):
    """漏轮次在机械统计里显形（R2 一眼看到覆盖缺口）。"""
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(
        json.dumps({"message": {"role": "user", "content": f"第{i}轮" }},
                   ensure_ascii=False) for i in range(1, 3)), encoding="utf-8")
    main(["anchor", str(p)], components=comp)
    anchor_id = re.search(r"锚点已保存: (s-[\d-]+)",
                          capsys.readouterr().out).group(1)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({
        "anchor": anchor_id,
        "domain_root": {"action": "create", "title": "测试领域"},
        "items": [{"action": "create", "aspect": "flow", "parent": "$ROOT",
                   "title": "流程", "gist": "g",
                   "source": {"anchor": anchor_id, "turns": [1]}}],  # 漏了 turn 2
    }, ensure_ascii=False), encoding="utf-8")
    main(["review", str(plan_file)], components=comp)
    out = capsys.readouterr().out
    assert "覆盖: 1/2 轮（缺 [2]（未覆盖））" in out
    assert "轴建齐: 1/4" in out


def test_anchor_missing_path(comp, capsys):
    with pytest.raises(SystemExit):
        main(["anchor", "nonexistent.jsonl"], components=comp)
    assert "未找到素材" in capsys.readouterr().out


def test_anchor_preview_gate(comp, tmp_path, capsys):
    """确认闸门：--preview 只渲染素材确认视图（含将生成标题），不落库。"""
    md = tmp_path / "doc.md"
    md.write_text("# 概述\n甲\n\n## 流程\n乙", encoding="utf-8")
    main(["anchor", str(md), "--preview"], components=comp)
    out = capsys.readouterr().out
    assert "【素材确认】共 2 轮" in out
    assert "将生成标题: doc.md" in out              # title 进闸门（缺口①）
    assert "── turn 1 ──" in out and "── turn 2 ──" in out
    assert "去掉 --preview" in out
    assert comp.truth.list_anchors() == []            # 未落库


def test_anchor_duplicate_warns(comp, tmp_path, capsys):
    """同素材重复 anchor：提示但不拒收（新快照语义，缺口③）。"""
    md = tmp_path / "doc.md"
    md.write_text("# t\nx", encoding="utf-8")
    main(["anchor", str(md)], components=comp)
    capsys.readouterr()
    main(["anchor", str(md)], components=comp)
    out = capsys.readouterr().out
    assert "同素材已有锚点" in out
    assert len(comp.truth.list_anchors()) == 2        # 两份快照并存


def test_anchor_deprecate_blocks_plan_reference(comp, tmp_path, capsys):
    """锚点作废：真值保留；蒸馏计划引用被 checker 拒收（缺口②）。"""
    md = tmp_path / "doc.md"
    md.write_text("# t\nx", encoding="utf-8")
    main(["anchor", str(md)], components=comp)
    anchor_id = comp.truth.list_anchors()[0].id
    main(["anchor", "--deprecate", anchor_id], components=comp)
    assert "已作废" in capsys.readouterr().out
    assert comp.truth.anchor_exists(anchor_id)                 # 物理存在
    assert not comp.truth.anchor_active(anchor_id)             # 引用不合法
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({
        "driver": "session", "mode": "domain",
        "domain_root": {"action": "create", "title": "测试领域"},
        "anchor": anchor_id,
        "items": [{"action": "create", "aspect": "flow", "parent": "$ROOT",
                   "title": "流程", "gist": "g",
                   "source": {"anchor": anchor_id, "turns": [1]}}]},
        ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["plan", str(plan)], components=comp)
    assert "已作废" in capsys.readouterr().out


def test_anchor_text_channel(comp, capsys):
    """--text 通道：对话里直接贴的描述 → 单轮锚点。"""
    main(["anchor", "--text", "用户口述的需求单流程"], components=comp)
    out = capsys.readouterr().out
    assert "锚点已保存: s-" in out
    anchors = comp.truth.list_anchors()
    assert len(anchors) == 1 and anchors[0].source == "inline-text"
    assert "用户口述的需求单流程" in anchors[0].content


# ================= 主题驱动盘点（distill --domain，阶段3） =================

def test_distill_domain_report(comp, capsys):
    """distill --domain：盘点报告（全景分组 + 相关锚点 + 空缺提示 + 下一步指引）。"""
    root = comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="国际机票", summary="机票领域"))
    comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="创建需求单流程",
        summary="主流程", aspect="flow", parents=[root]))
    comp.truth.save_anchor(Anchor(id="", title="国际机票联调", date="2026-09-20",
                                  content="需求单讨论"))
    main(["distill", "--domain", "机票"], components=comp)   # 子串唯一命中
    out = capsys.readouterr().out
    assert f"领域: 国际机票 ({root})" in out
    assert "【领域树全景】（按轴分组）" in out
    assert "（领域根）" in out and "── flow ──" in out
    assert "[k-0002] 创建需求单流程" in out and f"parent: {root}" in out
    assert "【相关锚点】" in out and "国际机票联调" in out and "未蒸馏" in out
    assert "【空缺提示】（机械统计）" in out
    assert "driver=topic" in out


def test_distill_domain_not_found_lists_candidates(comp, capsys):
    """零命中：列全部领域根候选，退出码非零。"""
    comp.knowledge.create(Knowledge(
        type=KnowledgeType.MODEL, scope=Scope.DOMAIN, title="国际机票"))
    with pytest.raises(SystemExit):
        main(["distill", "--domain", "不存在xyz"], components=comp)
    out = capsys.readouterr().out
    assert "未定位到领域根" in out and "国际机票" in out       # 候选列出


def test_distill_domain_mutex_with_anchor_id(comp, capsys):
    """--domain 与位置参数 anchor_id 互斥。"""
    with pytest.raises(SystemExit):
        main(["distill", "s-20260930-001", "--domain", "x"], components=comp)
    assert "互斥" in capsys.readouterr().out
