"""自动挂靠：落库后把新知识挂靠到相关主题节点（同类知识聚合）。

从 CLI 抽出，供蒸馏 confirm 流程复用，独立可测。
"""
from ..domain.types import EdgeType


def auto_attach_all(writer, reader, ids):
    """落库后自动挂靠：对每条新知识，用标题检索知识库，挂靠到"聚合主题"节点。

    返回 {新知识id: 父节点id}。
    """
    idset = set(ids)  # 同批新知识，不作为父主题候选（避免同批互挂）
    result = {}
    for kid in ids:
        k = reader.get(kid)
        if not k or not k.title:
            continue
        parent = auto_attach(writer, reader, kid, k.title, exclude=idset)
        if parent:
            result[kid] = parent
    return result


def auto_attach(writer, reader, kid, title, exclude=None):
    """单条自动挂靠：只挂到"聚合主题"节点（有 parent 入边）且标题相关。

    相关性门槛：标题字符重叠 ≥ 0.3——避免挂到不相关的聚合主题（如碰巧有入边的节点）。
    """
    exclude = exclude or set()
    try:
        hits = reader.search(title, limit=10)
    except Exception:
        return None
    q_chars = {c for c in title if c.strip()}
    if not q_chars:
        return None
    for h in hits:
        if h.id == kid or h.id in exclude:
            continue
        if writer.store.has_parent_edge(h.id):
            overlap = len(q_chars & set(h.title)) / len(q_chars)
            if overlap >= 0.3:
                writer.add_edge(kid, h.id, EdgeType.PARENT)
                return h.id
    return None
