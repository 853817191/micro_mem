"""存量导入服务：扫描历史 JSONL 会话 → 生成锚点。

编排归本服务；Claude 会话格式解析归 infrastructure/claude_jsonl.py（反腐层）。
存量与增量共用同一管道：存量 = 批量扫描生成锚点（后续蒸馏由 DistillService 处理）。
"""
import os
from dataclasses import dataclass, field

from ..application.ports import TruthStore
from ..domain.models import Anchor
from ..infrastructure import claude_jsonl


@dataclass
class ImportResult:
    """存量导入结果统计。"""
    anchors_created: int = 0          # 生成的锚点数
    knowledge_created: int = 0        # 生成的知识数（锚点导入阶段恒为 0）
    errors: list[str] = field(default_factory=list)


class ImportService:
    """存量导入：扫描 Claude JSONL 会话目录 → 逐会话生成保真锚点。"""

    def __init__(self, truth: TruthStore):
        """依赖注入：真值端口（锚点不进索引，无需 IndexStore）。"""
        self._truth = truth

    def import_history(self, source_dir: str, mode: str = "backfill") -> ImportResult:
        """扫描 source_dir 下的 .jsonl 会话，每个生成一个锚点。

        单文件失败不影响整体导入（记入 errors）。mode 预留（backfill/incremental 当前同处理）。
        """
        result = ImportResult()
        for path in claude_jsonl.scan_jsonl(source_dir):
            try:
                text = claude_jsonl.parse_session(path)
                if text:
                    self._truth.save_anchor(Anchor(
                        id="", title=os.path.basename(path), date="",
                        content=text, source=os.path.abspath(path)))
                    result.anchors_created += 1
            except Exception as e:
                result.errors.append(f"{os.path.basename(path)}: {e}")
        return result
