"""Embedder 端口的 Hash 实现：字符 bigram 哈希向量。

无外部依赖、确定性（md5），用于验证向量管道。
局限：基于字符重合而非语义。后续可换真实语义模型（sentence-transformers / API）。
"""
import hashlib
import math

from ..application.ports import Embedder


class HashEmbedder(Embedder):
    """字符 bigram 哈希到固定维度 + L2 归一化（同一文本向量稳定）。"""

    def __init__(self, dim: int = 1024):
        """维度默认 1024（与默认配置 embedding_dim 一致）。"""
        self._dim = dim

    @property
    def dim(self) -> int:
        """向量维度。"""
        return self._dim

    def embed(self, text: str) -> list[float]:
        """文本 → 向量。"""
        vec = [0.0] * self._dim
        for i in range(len(text) - 1):
            gram = text[i:i + 2].encode("utf-8")
            idx = int(hashlib.md5(gram).hexdigest(), 16) % self._dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
