"""infrastructure/config.py 集成测试：定位优先级与路径解析（D5）。"""
import os

import pytest

from micro_mem.infrastructure.config import Config


def test_explicit_path_relative_data_dir_against_config_dir(tmp_path, monkeypatch):
    """显式 config_path：相对 data_dir 按 config 所在目录解析（与 cwd 无关）。"""
    cfg_dir = tmp_path / "conf"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("data_dir: ./mydata\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)   # cwd 与 config 不同目录
    c = Config(str(cfg_dir / "config.yaml"))
    assert c.data_dir == os.path.abspath(cfg_dir / "mydata")


def test_memory_home_locates_config(tmp_path, monkeypatch):
    """MEMORY_HOME 指向目录下的 config.yaml 优先于 cwd。"""
    home = tmp_path / "memhome"
    home.mkdir()
    (home / "config.yaml").write_text("data_dir: ./d\nembedding_dim: 256\n", encoding="utf-8")
    monkeypatch.setenv("MEMORY_HOME", str(home))
    monkeypatch.chdir(tmp_path)   # cwd 无 config.yaml
    c = Config()
    assert c.embedding_dim == 256
    assert c.data_dir == os.path.abspath(home / "d")


def test_cwd_config_when_no_memory_home(tmp_path, monkeypatch):
    """无 MEMORY_HOME：落回 ./config.yaml。"""
    monkeypatch.delenv("MEMORY_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("embedding_dim: 512\n", encoding="utf-8")
    assert Config().embedding_dim == 512


def test_defaults_when_no_config_anywhere(tmp_path, monkeypatch):
    """全无配置：包内默认值；data_dir 按 cwd 解析；绝对化。"""
    monkeypatch.delenv("MEMORY_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    c = Config()
    assert c.data_dir == os.path.abspath(tmp_path / "data")
    assert c.network_store == "sqlite"
    assert c.embedding_dim == 1024
    assert c.semantic_fallback == "on_zero_hit"   # D8 默认值


def test_semantic_fallback_configurable_and_validated(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("search:\n  semantic_fallback: always\n", encoding="utf-8")
    assert Config(str(cfg)).semantic_fallback == "always"
    cfg.write_text("search:\n  semantic_fallback: bogus\n", encoding="utf-8")
    with pytest.raises(ValueError, match="semantic_fallback"):
        _ = Config(str(cfg)).semantic_fallback


def test_distill_templates_default_and_overridable(tmp_path, monkeypatch):
    """轴模板：默认内置 domain 四轴；用户 config.yaml 可新增模式/覆盖轴（浅合并）。"""
    monkeypatch.delenv("MEMORY_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    c = Config()
    assert list(c.distill_templates["domain"]) == [
        "flow", "structure", "boundary", "constraint"]
    assert c.distill_templates["event"] == {}
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "distill:\n  templates:\n    mechanism:\n      原理: 怎么实现——算法、数据结构\n",
        encoding="utf-8")
    c2 = Config(str(cfg))
    assert c2.distill_templates["mechanism"] == {"原理": "怎么实现——算法、数据结构"}
    assert "flow" in c2.distill_templates["domain"]   # 浅合并：默认模板未被整节覆盖


def test_explicit_missing_config_raises(tmp_path):
    """显式路径不存在必须显式失败（静默用默认值会藏配置错误）。"""
    with pytest.raises(FileNotFoundError):
        Config(str(tmp_path / "nope.yaml"))


def test_derived_paths(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"data_dir: {tmp_path.as_posix()}\n", encoding="utf-8")
    c = Config(str(cfg))
    assert c.anchors_dir() == os.path.join(c.data_dir, "anchors")
    assert c.knowledge_dir() == os.path.join(c.data_dir, "knowledge")
    assert c.db_path() == os.path.join(c.data_dir, "db", "memory.db")
