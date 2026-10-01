"""装配根（composition root）：唯一同时认识 application 端口与 infrastructure 实现的地方。

依赖规则：interfaces → application → domain ← infrastructure。
本模块是唯一的"交叉口"，其余任何模块不得跨层 import 实现。

两个装配入口：
- assemble_inmemory：单测 / 演示用（不碰盘、不起 SQLite）
- assemble：生产用（按 config 选 infrastructure 实现，落到真实数据目录）
"""
import os
from dataclasses import dataclass

from .application.distill_service import DistillService
from .application.import_service import ImportService
from .application.knowledge_service import KnowledgeService
from .application.ports import Embedder, IndexStore, TruthStore
from .application.rebuild_service import RebuildService
from .application.search_service import SearchService
from .infrastructure.config import Config
from .infrastructure.hash_embedder import HashEmbedder
from .infrastructure.markdown_truth import MarkdownTruthStore
from .infrastructure.memory_index import InMemoryIndexStore
from .infrastructure.memory_truth import InMemoryTruthStore
from .infrastructure.sqlite_index import SqliteIndexStore


@dataclass
class Components:
    """装配产物：三个端口 + 五个应用服务（+ 生产配置）。"""
    truth: TruthStore
    index: IndexStore
    embedder: Embedder
    knowledge: KnowledgeService
    search: SearchService
    distill: DistillService
    importer: ImportService
    rebuild: RebuildService
    config: Config | None = None


def _wire(truth: TruthStore, index: IndexStore, embedder: Embedder,
          semantic_fallback: str = "on_zero_hit",
          aspects: list[str] | None = None,
          view_config: dict[str, int] | None = None,
          config: Config | None = None) -> Components:
    """端口 → 服务的共享接线（生产与内存装配同一拓扑）。"""
    knowledge = KnowledgeService(truth, index, embedder)
    search = SearchService(truth, index, embedder, semantic_fallback)
    return Components(
        truth=truth, index=index, embedder=embedder,
        knowledge=knowledge, search=search,
        distill=DistillService(truth, index, knowledge, search=search,
                               aspects=aspects, view_config=view_config),
        importer=ImportService(truth),
        rebuild=RebuildService(truth, index, embedder),
        config=config)


def assemble_inmemory(embedder_dim: int = 1024,
                      semantic_fallback: str = "on_zero_hit",
                      aspects: list[str] | None = None,
                      view_config: dict[str, int] | None = None) -> Components:
    """内存装配：单测 / 演示用（不碰盘、不起 SQLite）。"""
    return _wire(InMemoryTruthStore(), InMemoryIndexStore(),
                 HashEmbedder(embedder_dim),
                 semantic_fallback=semantic_fallback, aspects=aspects,
                 view_config=view_config)


def assemble(config_path: str | None = None) -> Components:
    """生产装配：按 config 建目录、选实现、接服务。"""
    config = Config(config_path)
    for d in (config.data_dir, config.anchors_dir(), config.knowledge_dir(),
              config.temp_dir(), os.path.dirname(config.db_path())):
        os.makedirs(d, exist_ok=True)
    truth: TruthStore = MarkdownTruthStore(config.data_dir)
    if config.network_store == "sqlite":
        index: IndexStore = SqliteIndexStore(config.db_path(), config.embedding_dim)
    elif config.network_store == "memory":
        index = InMemoryIndexStore()
    else:
        raise ValueError(f"未知 network_store: {config.network_store!r}（可选: sqlite|memory）")
    return _wire(truth, index, HashEmbedder(config.embedding_dim),
                 semantic_fallback=config.semantic_fallback,
                 aspects=config.distill_aspects,
                 view_config=config.distill_view,
                 config=config)
