"""P0 一致性用例（TC-D1~D3）。"""
import glob
import os

from micro_mem.common.md_parser import knowledge_from_meta, parse_file


def test_d1_create_consistency(writer, reader, config, mk):
    kid = mk("一致性知识", "一致性正文")
    # md frontmatter 里 title 应与索引一致
    path = os.path.join(config.knowledge_dir(), f"{kid}_一致性知识.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "title: 一致性知识" in content, "md frontmatter 与索引不一致"


def test_d2_rebuild(writer, reader, store, config, mk):
    # 建 3 条，清空索引，rebuild 恢复
    ids = [mk(f"重建{i}") for i in range(3)]
    store.clear_all()
    # rebuild：扫描 knowledge 目录重建
    for path in sorted(glob.glob(os.path.join(config.knowledge_dir(), "k-*.md"))):
        meta, body = parse_file(path)
        writer.create_knowledge(knowledge_from_meta(meta, body))
    assert any(h.id in ids for h in reader.search("重建")), "rebuild 后检索丢失"


def test_d3_update_consistency(writer, reader, config, mk):
    kid = mk("更新一致", "更新前正文")
    writer.update_knowledge(kid, title="更新后标题")
    k = reader.get(kid)
    path = os.path.join(config.knowledge_dir(), f"{kid}_更新后标题.md")
    assert os.path.exists(path), "update 后 md 文件未重命名/未同步"
    assert k.title == "更新后标题", "索引未同步"
