"""配置加载与路径解析（D5 自包含，废弃"文件上溯四级"）。

定位优先级：显式 config_path > MEMORY_HOME 环境变量 > ./config.yaml > 包内默认值。
路径规则：data_dir 绝对路径直接用；相对路径按 config.yaml 所在目录解析
（无配置文件时按 MEMORY_HOME，否则按当前工作目录）。
"""
import copy
import os
from typing import Any

import yaml

from ..domain.models import SEMANTIC_FALLBACKS

# 包内默认配置（无 config.yaml 时兜底）
DEFAULT_CONFIG: dict[str, Any] = {
    "network_store": "sqlite",
    "data_dir": "./data",
    "embedding_dim": 1024,
    "search": {"semantic_fallback": "on_zero_hit"},
    # distill 节：aspects=切面值域；其余为 R1 视图预算参数
    # （与 application/distill_view.py 的 DEFAULTS 保持一致，改要同步）
    "distill": {
        "aspects": ["flow", "structure", "boundary", "constraint"],
        "context_budget_chars": 100000,   # 增量总字符预算：超了才触发压缩
        "assistant_chars": 300,           # 压缩时 assistant 段保留字符数
        "tool_input_chars": 100,          # 压缩时 tool_use 入参保留字符数
        "preretrieve_hits": 10,           # 预检索召回命中上限
        "subtree_max": 30,                # 预检索子树总量上限
    },
}


class Config:
    """项目配置：定位 config.yaml → 合并默认值 → 解析路径。"""

    def __init__(self, config_path: str | None = None):
        """加载配置。显式传入的 config_path 必须存在（静默忽略会藏配置错误）。"""
        if config_path is not None and not os.path.exists(config_path):
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        self._data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        memory_home = os.environ.get("MEMORY_HOME", "")
        self._base_dir = memory_home or os.getcwd()
        path = config_path or self._locate(memory_home)
        if path is not None:
            self._base_dir = os.path.dirname(os.path.abspath(path))
            self._load_file(path)
        self._resolve_paths()

    @staticmethod
    def _locate(memory_home: str) -> str | None:
        """定位配置文件：MEMORY_HOME/config.yaml > ./config.yaml。"""
        candidates = []
        if memory_home:
            candidates.append(os.path.join(memory_home, "config.yaml"))
        candidates.append(os.path.join(os.getcwd(), "config.yaml"))
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def _load_file(self, path: str) -> None:
        """读配置文件，覆盖默认值（缺失字段保持默认）。"""
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for key, value in loaded.items():
            # 嵌套节（如 search）做浅合并，避免覆盖整节丢默认项
            if isinstance(value, dict) and isinstance(self._data.get(key), dict):
                self._data[key].update(value)
            else:
                self._data[key] = value

    def _resolve_paths(self) -> None:
        """相对 data_dir 按配置文件所在目录解析（与工作目录解耦）。"""
        data_dir = self._data["data_dir"]
        if not os.path.isabs(data_dir):
            data_dir = os.path.join(self._base_dir, data_dir)
        self._data["data_dir"] = os.path.abspath(data_dir)

    # ---------------- 标量配置 ----------------

    @property
    def data_dir(self) -> str:
        """数据根目录（绝对路径）。"""
        return self._data["data_dir"]

    @property
    def network_store(self) -> str:
        """索引引擎类型：sqlite | memory（扩展位，如 neo4j）。"""
        return self._data["network_store"]

    @property
    def embedding_dim(self) -> int:
        """向量维度 N。"""
        return int(self._data["embedding_dim"])

    @property
    def semantic_fallback(self) -> str:
        """语义兜底策略（D8）：on_zero_hit | always | off。非法值显式报错。"""
        value = (self._data.get("search") or {}).get("semantic_fallback", "on_zero_hit")
        if value not in SEMANTIC_FALLBACKS:
            raise ValueError(
                f"search.semantic_fallback 非法值 {value!r}，可选: {SEMANTIC_FALLBACKS}")
        return value

    @property
    def distill_aspects(self) -> list[str]:
        """领域切面值域（蒸馏计划校验用；可扩展——加轴改配置不改代码）。"""
        return list((self._data.get("distill") or {}).get("aspects") or [])

    @property
    def distill_view(self) -> dict[str, int]:
        """R1 视图预算参数（键集合见 application/distill_view.py DEFAULTS）。"""
        d = (self._data.get("distill") or {})
        return {k: int(d[k]) for k in (
            "context_budget_chars", "assistant_chars", "tool_input_chars",
            "preretrieve_hits", "subtree_max") if k in d}

    # ---------------- 派生路径 ----------------

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
