"""轴模板（domain/axis_templates.py）单测：默认模板形状 + resolve_template 兜底语义。"""
from micro_mem.domain.axis_templates import DEFAULT_TEMPLATES, resolve_template


def test_default_templates_domain_four_axes():
    """domain 默认模板：四轴齐全，每轴带筛子定义。"""
    domain = DEFAULT_TEMPLATES["domain"]
    assert set(domain) == {"flow", "structure", "boundary", "constraint"}
    assert all(isinstance(v, str) and v for v in domain.values())


def test_default_templates_event_empty():
    """event 默认模板留白（待定）→ resolve 时走兜底。"""
    assert DEFAULT_TEMPLATES["event"] == {}


def test_resolve_hits_configured_mode():
    """配置了该模式的模板 → 原样返回（用户覆盖生效）。"""
    templates = {"domain": {"flow": "x", "structure": "y"}}
    assert resolve_template(templates, "domain") == {"flow": "x", "structure": "y"}


def test_resolve_empty_mode_template_falls_back_to_domain():
    """模式模板为空（event 留白场景）→ 兜底 domain 默认模板。"""
    out = resolve_template(DEFAULT_TEMPLATES, "event")
    assert out == DEFAULT_TEMPLATES["domain"]


def test_resolve_unknown_mode_falls_back_to_domain():
    """未配置的模式 → 兜底 domain 默认模板（预留扩展口子的默认值语义）。"""
    out = resolve_template(DEFAULT_TEMPLATES, "mechanism")
    assert out == DEFAULT_TEMPLATES["domain"]


def test_resolve_empty_templates_falls_back_to_builtin():
    """全空配置 → 兜底内置 domain 默认（默认模板是底座，配置只是扩展/覆盖）。"""
    assert resolve_template({}, "domain") == DEFAULT_TEMPLATES["domain"]
