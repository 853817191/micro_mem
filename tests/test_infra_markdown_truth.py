"""MarkdownTruthStore 集成测试（tmp_path 真盘）。

红线：frontmatter 字段与顺序和旧版（api/writer.py 时代）字节级一致——
data/ 是真值档案，格式漂移即污染历史（阶段5 用真实 data/ 逐节点比对验收）。
"""
import os
import re
from datetime import datetime

from micro_mem.domain.models import (
    Anchor,
    ExternalRef,
    Knowledge,
    KnowledgeType,
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
    """锚点 frontmatter 定序行与旧版一致；游标默认 -1。"""
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
    assert lines[5] == "source: D:\\sessions\\a.jsonl"
    assert lines[6] == "---"
    assert lines[7] == ""
    a = truth.get_anchor(aid)
    assert a is not None
    assert a.content == "对话正文"
    assert a.source == "D:\\sessions\\a.jsonl"
    assert a.distilled_until == -1
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
