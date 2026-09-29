"""P1 蒸馏/导入用例（TC-DI1~DI2）。"""
import json
import os

from micro_mem.domain.types import Decision, DistillCandidate, KnowledgeType, Scope


def test_di1_confirm_distill(distiller, reader):
    cands = [
        DistillCandidate(type=KnowledgeType.METHOD, scope=Scope.DOMAIN,
                         title="蒸馏候选keep", summary="keep摘要", decision=Decision.KEEP),
        DistillCandidate(type=KnowledgeType.FACT, scope=Scope.DOMAIN,
                         title="蒸馏候选reject", summary="reject", decision=Decision.REJECT),
    ]
    ids = distiller.confirm_distill(cands)
    assert len(ids) == 1, "reject 应被跳过"
    assert any(h.id == ids[0] for h in reader.search("蒸馏候选keep")), "keep 候选未落库"


def test_di2_import_history(importer, config, tmp_path):
    before = len(os.listdir(config.anchors_dir()))
    sess = tmp_path / "sessions"
    sess.mkdir()
    (sess / "s-test.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user",
                                                 "content": [
                                                     {"type": "text", "text": "导入测试对话"},
                                                 ]}}) + "\n",
        encoding="utf-8")
    result = importer.import_history(str(sess))
    assert result.anchors_created == 1, "锚点未生成"
    after = len(os.listdir(config.anchors_dir()))
    assert after == before + 1, "锚点文件未增加（应 +1）"
