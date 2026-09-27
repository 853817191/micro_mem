"""配置加载：读取 config.yaml，提供全局配置。"""
import os

import yaml

# 缺省配置（config.yaml 缺失时兜底）
DEFAULT_CONFIG = {
    "network_store": "sqlite",
    "data_dir": "./data",
    "embedding_model": "bge-m3",
    "embedding_dim": 1024,
}

# 项目根目录：src/config.py → 上溯两级
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    """项目配置，从 config.yaml 加载，缺省字段用默认值。"""

    def __init__(self, config_path: str = "config.yaml"):
        """加载配置：读 config.yaml 并与默认值合并；路径相对项目根解析。"""
        self._data = dict(DEFAULT_CONFIG)
        self._load_file(config_path)
        self._resolve_paths()

    def _load_file(self, config_path: str) -> None:
        """读配置文件，存在则覆盖默认值（缺失字段保持默认）。"""
        if not os.path.exists(config_path):
            return
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        self._data.update(loaded)

    def _resolve_paths(self) -> None:
        """解析路径：相对路径基于项目根 BASE，而非当前工作目录。

        避免从其他目录运行命令时读到空库/错库（data_dir: ./data 曾按工作目录解析）。
        """
        data_dir = self._data["data_dir"]
        if not os.path.isabs(data_dir):
            data_dir = os.path.join(BASE, data_dir)
        self._data["data_dir"] = os.path.abspath(data_dir)

    @property
    def data_dir(self) -> str:
        """数据根目录。"""
        return self._data["data_dir"]

    @property
    def network_store(self) -> str:
        """引擎类型：sqlite | neo4j | memory。"""
        return self._data["network_store"]

    @property
    def embedding_dim(self) -> int:
        """向量维度 N。"""
        return int(self._data["embedding_dim"])

    def anchors_dir(self) -> str:
        """锚点目录：对话记录（保真）。"""
        return os.path.join(self.data_dir, "anchors")

    def knowledge_dir(self) -> str:
        """知识目录：md 真值。"""
        return os.path.join(self.data_dir, "knowledge")

    def temp_dir(self) -> str:
        """临时目录：过程产物（可清理）。"""
        return os.path.join(self.data_dir, "temp")

    def db_path(self) -> str:
        """SQLite 索引文件路径。"""
        return os.path.join(self.data_dir, "db", "memory.db")
