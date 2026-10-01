"""接口层：用户-facing 入口（CLI / HTTP）。

依赖规则：本层只依赖 application 服务与 domain 模型 + composition 装配根，
不直接 import infrastructure 实现。
"""
