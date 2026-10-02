"""轴模板：蒸馏模式 → {轴名: 筛子定义}。

轴先行（用户拍板）：R1 不从内容里抽轴，而是拿模板逐轴去原文找对应内容，
找不到的轴也建节点（空轴=底座，前期数据不全、后续流程补充）。

模板分层：
- 默认模板（本文件）：domain 四轴为权威定义；event 留白待定。
- 用户配置：config.yaml 的 distill.templates 可新增模式 / 覆盖轴（浅合并语义，
  只覆盖写出的键，不动默认模式）。
- 兜底（用户拍板）：模式无模板或模板为空 → 用默认模板（domain）。
筛子定义是 R1 找内容的指引，随供给视图带给 R1。
"""

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "domain": {
        "flow": "怎么流转——步骤、分支条件、接口编排",
        "structure": "长什么样——实体/表/字段、状态机、枚举",
        "boundary": "和谁交互——外部调用、入口、契约、上下游",
        "constraint": "必须遵守什么——规则、校验、坑、违反后果",
    },
    "event": {},  # 事件蒸馏轴待定（用户拍板：后讨论；未配前走兜底=domain 模板）
}


def resolve_template(templates: dict[str, dict[str, str]], mode: str) -> dict[str, str]:
    """按模式取轴模板；无配置或空 → 兜底内置默认模板（domain，权威定义见本文件）。

    用户配置的 mode 命中（非空）→ 原样采用（整模式覆盖，轴级重写归配置组织方式）；
    未命中/留白 → 一律回落到内置 domain 默认——默认模板是底座，配置只是扩展/覆盖。
    """
    hit = templates.get(mode) or {}
    if hit:
        return hit
    return dict(DEFAULT_TEMPLATES["domain"])
