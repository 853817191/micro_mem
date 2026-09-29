"""装配容器（Composition Root）：全项目唯一的组件装配点。

所有入口（cli/）都从这里取装配好的组件，不再各自内联装配。
换引擎、换向量化器只改这里一处。
"""
from dataclasses import dataclass

from .api.distiller import Distiller
from .api.importer import Importer
from .api.reader import MemoryReader
from .api.writer import MemoryWriter
from .common.config import Config
from .common.embedder import HashEmbedder
from .store.base import NetworkStore
from .store.sqlite_store import SqliteNetworkStore


@dataclass
class Components:
    """装配好的组件集合（命令函数统一接收）。"""
    config: Config
    store: NetworkStore
    writer: MemoryWriter
    reader: MemoryReader
    distiller: Distiller
    importer: Importer


def build_components() -> Components:
    """装配：加载配置 → 选引擎 → 向量化器 → 组装四接口。"""
    config = Config()
    if config.network_store == "sqlite":
        store = SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)
    else:
        raise NotImplementedError(f"引擎 {config.network_store} 未实现")
    embedder = HashEmbedder(dim=config.embedding_dim)
    writer = MemoryWriter(config, store, embedder=embedder)
    reader = MemoryReader(config, store, embedder=embedder)
    distiller = Distiller(config, store, writer, reader)
    importer = Importer(config, store, writer)
    return Components(config, store, writer, reader, distiller, importer)
