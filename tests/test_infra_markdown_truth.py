"""MarkdownTruthStore 集成测试（tmp_path 真盘）。

红线：frontmatter 字段与顺序和旧版（api/writer.py 时代）字节级一致——
data/ 是真值档案，格式漂移即污染历史（阶段5 用真实 data/ 逐节点比对验收）。
"""
import json
import os
import re
from datetime import datetime

from micro_mem.domain.models import (
    Anchor,
    DistillPlan,
    DomainRoot,
    ExternalRef,
    ItemResult,
    Knowledge,
    KnowledgeType,
    PlanAction,
    PlanItem,
    RefType,
    Scope,
    Source,
    SourceType,
    Status,
)
from micro_mem.infrastructure.markdown_truth import MarkdownTruthStore


def mk_knowledge(title="测试知识", body="正文", **kw) -> Knowledge:
    return Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN,
                     title=title, summary=f"{title}的摘要", body=body, **kw)


# ---------------- 知识：红线格式 ----------------


def test_save_creates_file_with_legacy_byte_format(tmp_path):
    """红线：frontmatter 字段顺序/结构/转义与旧版一致。"""
    truth = MarkdownTruthStore(str(tmp_path))
    kid = truth.save_knowledge(mk_knowledge(
        "温度控制",
        sources=[Source(SourceType.CONVERSATION_DISTILLED, "anchors/s-1.md")],
        parents=["k-0000"], links=["k-0009"],
        external_refs=[ExternalRef(RefType.IDEV, "TCX-1")]))
    assert kid == "k-0001"
    text = (tmp_path / "knowledge" / "k-0001_温度控制.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[0] == "---"
    assert lines[1] == "id: k-0001"
    assert lines[2] == "type: fact"
    assert lines[3] == "scope: domain"
    assert lines[4] == "title: 温度控制"
    assert lines[5] == "summary: 温度控制的摘要"
    assert "- type: conversation_distilled" in text
    assert "ref: anchors/s-1.md" in text
    assert "parents:" in text and "- k-0000" in text
    assert "links:" in text and "- k-0009" in text
    assert "external_refs:" in text and "value: TCX-1" in text
    assert re.search(r"^status: draft$", text, re.M)
    # 时间戳样字符串 yaml 输出带单引号（防解析歧义）——与旧版同款行为
    assert re.search(r"^created: '\d{4}-\d{2}-\d{2} ", text, re.M)
    assert re.search(r"^updated: '\d{4}-\d{2}-\d{2} ", text, re.M)
    assert text.endswith("---\n\n正文"), "body 紧跟 frontmatter 后的空行"


def test_get_roundtrip_full_fields(tmp_path):
    """get 回读全字段：含旧版 knowledge_from_meta 丢失的 created/updated。"""
    truth = MarkdownTruthStore(str(tmp_path))
    kid = truth.save_knowledge(mk_knowledge(
        "回读", body="正文abc",
        sources=[Source(SourceType.USER_DECLARED, "anchors/s-x.md")]))
    k = truth.get_knowledge(kid)
    assert k is not None and k.id == kid
    assert k.body == "正文abc"
    assert k.created and k.updated, "created/updated 必须回读（旧版丢失点）"
    assert k.sources[0].type is SourceType.USER_DECLARED
    assert k.sources[0].ref == "anchors/s-x.md"
    assert k.status is Status.DRAFT
    assert truth.get_knowledge("k-9999") is None


def test_aspect_and_turns_roundtrip(tmp_path):
    """领域蒸馏 v2 字段：aspect 落盘（紧随 scope）+ Source.turns 轮次级溯源往返。"""
    truth = MarkdownTruthStore(str(tmp_path))
    kid = truth.save_knowledge(mk_knowledge(
        "流程节点", aspect="flow",
        sources=[Source(SourceType.CONVERSATION_DISTILLED, "s-20260930-001", [3, 4])]))
    text = (tmp_path / "knowledge" / f"{kid}_流程节点.md").read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[4] == "aspect: flow"      # aspect 紧随 scope（分类维度聚集）
    assert lines[5] == "title: 流程节点"
    assert re.search(r"^  turns:\s*$", text, re.M) and "- 3" in text and "- 4" in text
    k = truth.get_knowledge(kid)
    assert k is not None and k.aspect == "flow"
    assert k.sources[0].turns == [3, 4]


def test_no_aspect_means_no_aspect_line(tmp_path):
    """存量兼容红线：aspect 为空时 frontmatter 不出现该字段（旧文件重写字节不变）。"""
    truth = MarkdownTruthStore(str(tmp_path))
    kid = truth.save_knowledge(mk_knowledge("无切面"))
    text = (tmp_path / "knowledge" / f"{kid}_无切面.md").read_text(encoding="utf-8")
    assert "aspect" not in text
    assert "turns" not in text
    assert truth.get_knowledge(kid).aspect == ""


def test_knowledge_ref_and_rename(tmp_path):
    """标题变化 → 文件名变化 → 旧文件删除（防 rebuild 时同 id 冲突）。"""
    truth = MarkdownTruthStore(str(tmp_path))
    kid = truth.save_knowledge(mk_knowledge("旧标题"))
    old_path = tmp_path / "knowledge" / f"{kid}_旧标题.md"
    assert old_path.exists()
    assert truth.knowledge_ref(kid) == os.path.join("knowledge", f"{kid}_旧标题.md")
    k = truth.get_knowledge(kid)
    k.title = "新标题"
    truth.save_knowledge(k)
    assert not old_path.exists(), "改名后旧文件应删除"
    assert (tmp_path / "knowledge" / f"{kid}_新标题.md").exists()
    assert truth.knowledge_ref("k-9999") == ""


def test_id_sequence_and_list_delete(tmp_path):
    truth = MarkdownTruthStore(str(tmp_path))
    truth.save_knowledge(mk_knowledge("A"))
    truth.save_knowledge(mk_knowledge("B"))
    assert truth.save_knowledge(mk_knowledge("C")) == "k-0003"
    assert [k.title for k in truth.list_knowledge()] == ["A", "B", "C"]
    assert truth.delete_knowledge("k-0001") is True
    assert [k.title for k in truth.list_knowledge()] == ["B", "C"]
    assert truth.delete_knowledge("k-0001") is False


# ---------------- 锚点 ----------------


def test_anchor_save_format_and_readback(tmp_path):
    """锚点 frontmatter 定序行：id/title/date/游标/status/source；游标默认 -1。"""
    truth = MarkdownTruthStore(str(tmp_path))
    aid = truth.save_anchor(Anchor(id="", title="会话X", date="", content="对话正文",
                                   source="D:\\sessions\\a.jsonl"))
    today = datetime.now().strftime("%Y%m%d")
    assert aid == f"s-{today}-001"
    lines = (tmp_path / "anchors" / f"{aid}.md").read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---"
    assert lines[1] == f"id: {aid}"
    assert lines[2] == "title: 会话X"
    assert re.match(r"^date: \d{4}-\d{2}-\d{2}$", lines[3])
    assert lines[4] == "distilled_until: -1"
    assert lines[5] == "status: active"
    assert lines[6] == "source: D:\\sessions\\a.jsonl"
    assert lines[7] == "---"
    assert lines[8] == ""
    a = truth.get_anchor(aid)
    assert a is not None
    assert a.content == "对话正文"
    assert a.source == "D:\\sessions\\a.jsonl"
    assert a.distilled_until == -1
    assert a.status == "active"
    assert truth.get_anchor("s-20990101-999") is None


def test_anchor_cursor_pointwise_update(tmp_path):
    """游标定点改写：除游标行外其余字节不动（保真是锚点第一职责）。"""
    truth = MarkdownTruthStore(str(tmp_path))
    aid = truth.save_anchor(Anchor(id="", title="t", date="", content="正文"))
    before = (tmp_path / "anchors" / f"{aid}.md").read_text(encoding="utf-8")
    truth.set_distill_cursor(aid, 7)
    after = (tmp_path / "anchors" / f"{aid}.md").read_text(encoding="utf-8")
    assert truth.get_distill_cursor(aid) == 7
    assert after == before.replace("distilled_until: -1", "distilled_until: 7")
    assert truth.get_distill_cursor("s-20990101-999") == -1


def test_anchor_resync_keeps_frontmatter(tmp_path):
    truth = MarkdownTruthStore(str(tmp_path))
    aid = truth.save_anchor(Anchor(id="", title="t", date="", content="旧正文"))
    truth.set_distill_cursor(aid, 3)
    assert truth.resync_anchor(aid, "增长后的新正文") is True
    a = truth.get_anchor(aid)
    assert a.content == "增长后的新正文"
    assert a.distilled_until == 3, "resync 保留游标"
    assert truth.resync_anchor("s-20990101-999", "x") is False


def test_anchor_exists_and_list(tmp_path):
    truth = MarkdownTruthStore(str(tmp_path))
    aid = truth.save_anchor(Anchor(id="", title="一", date="", content="c1"))
    truth.save_anchor(Anchor(id="", title="二", date="", content="c2"))
    assert truth.anchor_exists(aid), "纯 id 形态"
    assert truth.anchor_exists(f"anchors/{aid}.md"), "引用路径形态"
    assert not truth.anchor_exists("anchors/s-20990101-999.md")
    assert [a.title for a in truth.list_anchors()] == ["一", "二"]


def test_anchor_search_substring(tmp_path):
    """锚点检索：title/正文子串匹配（逐文件读；锚点规模小，暂不上 FTS 表）。"""
    truth = MarkdownTruthStore(str(tmp_path))
    a1 = truth.save_anchor(Anchor(id="", title="国际机票联调", date="",
                                  content="需求单流程讨论"))
    a2 = truth.save_anchor(Anchor(id="", title="其他会话", date="",
                                  content="正文里提到机票"))
    truth.save_anchor(Anchor(id="", title="无关", date="", content="完全无关"))
    assert [a.id for a in truth.search_anchors("国际机票")] == [a1]     # title 命中
    assert [a.id for a in truth.search_anchors("需求单")] == [a1]       # 正文命中
    assert {a.id for a in truth.search_anchors("机票")} == {a1, a2}
    assert truth.search_anchors("不存在的词") == []
    assert truth.search_anchors("") == []
    assert [a.id for a in truth.search_anchors("机票", limit=1)] == [a1]  # limit 截断


# ---------------- 蒸馏计划档案 ----------------


def mk_plan(**kw) -> DistillPlan:
    kw.setdefault("anchor", "s-20260930-001")
    kw.setdefault("domain_root", DomainRoot(action="existing", id="k-0001"))
    kw.setdefault("items", [PlanItem(
        action=PlanAction.CREATE, gist="要点", title="节点",
        aspect="flow", parent="k-0001",
        source_anchor="s-20260930-001", source_turns=[1, 2])])
    return DistillPlan(**kw)


def test_plan_save_get_roundtrip(tmp_path):
    """计划档案：id 分配 plan-<当日>-<序号>、JSON 落盘 plans/、to_dict 对称回读。"""
    truth = MarkdownTruthStore(str(tmp_path))
    plan = mk_plan(expected_turns=[1, 2])
    pid = truth.save_plan(plan)
    today = datetime.now().strftime("%Y%m%d")
    assert pid == f"plan-{today}-001"
    path = tmp_path / "plans" / f"{pid}.json"
    assert path.exists(), "计划以单文件 JSON 归档（机器档案，非人读真值）"
    assert json.loads(path.read_text(encoding="utf-8")) == plan.to_dict()
    got = truth.get_plan(pid)
    assert got is not None and got.to_dict() == plan.to_dict(), "to_dict 对称往返"
    assert got.items[0].source_turns == [1, 2]
    assert truth.get_plan("plan-20990101-999") is None


def test_plan_overwrite_with_results_and_list(tmp_path):
    """单文件演进：confirm 后原地覆盖（status/result 回写），list_plans 全量有序。"""
    truth = MarkdownTruthStore(str(tmp_path))
    pid = truth.save_plan(mk_plan())
    plan = truth.get_plan(pid)
    plan.status = "confirmed"
    plan.items[0].result = ItemResult.CREATED
    plan.items[0].result_knowledge_id = "k-0002"
    assert truth.save_plan(plan) == pid, "已有 id 不再分配，原地覆盖"
    got = truth.get_plan(pid)
    assert got.status == "confirmed"
    assert got.items[0].result is ItemResult.CREATED
    assert got.items[0].result_knowledge_id == "k-0002"
    pid2 = truth.save_plan(mk_plan())
    assert [p.plan_id for p in truth.list_plans()] == [pid, pid2]
