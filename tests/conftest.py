"""pytest 公共 fixtures：真实 Config + 临时 config.yaml（消 T5）、UTF-8 重配置（消 T3）、
装配各组件。"""
import os
import sys
from pathlib import Path

import pytest

# 确保 src-layout 下 micro_mem 可导入（不依赖 pip install -e）
SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Windows GBK 控制台下 ✓/中文 会崩（消 T3），与 main/distill 一致
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from micro_mem.api.distiller import Distiller  # noqa: E402
from micro_mem.api.importer import Importer  # noqa: E402
from micro_mem.api.reader import MemoryReader  # noqa: E402
from micro_mem.api.writer import MemoryWriter  # noqa: E402
from micro_mem.common.config import Config  # noqa: E402
from micro_mem.common.embedder import HashEmbedder  # noqa: E402
from micro_mem.domain.types import Knowledge, KnowledgeType, Scope  # noqa: E402
from micro_mem.store.sqlite_store import SqliteNetworkStore  # noqa: E402


@pytest.fixture
def config(tmp_path):
    """真实 Config：写临时 config.yaml，data_dir 指向本测试临时目录（消 T5）。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"data_dir: {tmp_path.as_posix()}\nembedding_dim: 1024\n",
        encoding="utf-8",
    )
    cfg = Config(str(cfg_path))
    for d in (cfg.anchors_dir(), cfg.knowledge_dir(), cfg.temp_dir(),
              os.path.dirname(cfg.db_path())):
        os.makedirs(d, exist_ok=True)
    return cfg


@pytest.fixture
def store(config):
    """SQLite 引擎（真实 Config 派生路径）。"""
    return SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)


@pytest.fixture
def embedder(config):
    """向量化器（哈希嵌入，测试用）。"""
    return HashEmbedder(config.embedding_dim)


@pytest.fixture
def writer(config, store, embedder):
    return MemoryWriter(config, store, embedder)


@pytest.fixture
def reader(config, store, embedder):
    return MemoryReader(config, store, embedder)


@pytest.fixture
def distiller(config, store, writer, reader):
    return Distiller(config, store, writer, reader)


@pytest.fixture
def importer(config, store, writer):
    return Importer(config, store, writer)


@pytest.fixture
def mk(writer):
    """造一条知识并返回 id（原 run_tests.py 的 _mk 平移）。"""
    def _mk(title, body="", **kw):
        k = Knowledge(type=KnowledgeType.FACT, scope=Scope.DOMAIN, title=title,
                      summary=f"{title}的摘要", body=body or title, **kw)
        return writer.create_knowledge(k)
    return _mk
