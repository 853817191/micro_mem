"""TruthStore 的内存实现：单测基建（不碰盘），兼作路线图"内存引擎"的雏形。

与 Markdown 实现对齐的行为约定：
- 知识 id：k-<序号>（取现有最大序号 +1，四位补零）
- 锚点 id：s-<yyyymmdd>-<序号>（当日最大序号 +1，三位补零）
- 时间戳格式：%Y-%m-%d %H:%M:%S
- 存取均经副本：调用方改返回值不污染库内状态，反之亦然
"""
import copy
import re
from datetime import datetime

from ..application.ports import TruthStore
from ..domain.models import Anchor, Knowledge

_NOW = "%Y-%m-%d %H:%M:%S"


class InMemoryTruthStore(TruthStore):
    """内存真值库：dict 存知识/锚点，行为与文件实现对齐。"""

    def __init__(self) -> None:
        self._knowledge: dict[str, Knowledge] = {}
        self._anchors: dict[str, Anchor] = {}

    # ---------------- 知识 ----------------

    def save_knowledge(self, k: Knowledge) -> str:
        """id 为空分配新 id；created 首存写入，updated 每次刷新。"""
        if not k.id:
            k.id = self._next_knowledge_id()
        now = datetime.now().strftime(_NOW)
        if not k.created:
            k.created = now
        k.updated = now
        self._knowledge[k.id] = copy.deepcopy(k)
        return k.id

    def get_knowledge(self, id: str) -> Knowledge | None:
        k = self._knowledge.get(id)
        return copy.deepcopy(k) if k is not None else None

    def knowledge_ref(self, id: str) -> str:
        """内存实现无文件，返回稳定标识符。"""
        return f"memory://knowledge/{id}" if id in self._knowledge else ""

    def list_knowledge(self) -> list[Knowledge]:
        return [copy.deepcopy(k) for k in sorted(self._knowledge.values(), key=lambda x: x.id)]

    def delete_knowledge(self, id: str) -> bool:
        return self._knowledge.pop(id, None) is not None

    # ---------------- 锚点 ----------------

    def save_anchor(self, a: Anchor) -> str:
        """id 为空分配 s-<当日日期>-<序号>；date 为空填今天。"""
        if not a.id:
            date = datetime.now().strftime("%Y%m%d")
            a.id = self._next_anchor_id(date)
        if not a.date:
            a.date = datetime.now().strftime("%Y-%m-%d")
        self._anchors[a.id] = copy.deepcopy(a)
        return a.id

    def get_anchor(self, id: str) -> Anchor | None:
        a = self._anchors.get(id)
        return copy.deepcopy(a) if a is not None else None

    def list_anchors(self) -> list[Anchor]:
        return [copy.deepcopy(a) for a in sorted(self._anchors.values(), key=lambda x: x.id)]

    def anchor_exists(self, ref: str) -> bool:
        """兼容纯 id / 引用路径 / 任意前缀路径（取 basename 归一化）。"""
        anchor_id = ref.replace("\\", "/").split("/")[-1]
        if anchor_id.endswith(".md"):
            anchor_id = anchor_id[:-3]
        return anchor_id in self._anchors

    def resync_anchor(self, id: str, content: str) -> bool:
        """替换正文，保留游标等元数据。"""
        a = self._anchors.get(id)
        if a is None:
            return False
        a.content = content
        return True

    def get_distill_cursor(self, anchor_id: str) -> int:
        a = self._anchors.get(anchor_id)
        return a.distilled_until if a is not None else -1

    def set_distill_cursor(self, anchor_id: str, turn: int) -> None:
        a = self._anchors.get(anchor_id)
        if a is not None:
            a.distilled_until = int(turn)

    # ---------------- id 分配 ----------------

    def _next_knowledge_id(self) -> str:
        """k-<序号>：取现有最大序号 +1（四位补零），与文件版规则一致。"""
        max_seq = 0
        for kid in self._knowledge:
            m = re.match(r"k-(\d+)$", kid)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return f"k-{max_seq + 1:04d}"

    def _next_anchor_id(self, date: str) -> str:
        """s-<date>-<序号>：取当日最大序号 +1（三位补零），与文件版规则一致。"""
        max_seq = 0
        prefix = f"s-{date}-"
        for aid in self._anchors:
            if aid.startswith(prefix):
                m = re.match(rf"s-{date}-(\d+)$", aid)
                if m:
                    max_seq = max(max_seq, int(m.group(1)))
        return f"{prefix}{max_seq + 1:03d}"
