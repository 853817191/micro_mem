"""应用层：用例编排（唯一的业务逻辑所在）。

依赖规则：interfaces → application → domain ← infrastructure。
application 只依赖 domain 与自身定义的端口（ports.py），不认识任何实现。
"""
