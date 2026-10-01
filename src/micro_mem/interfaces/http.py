"""可视化 HTTP 服务：静态页面 + JSON API。

工厂化（D4）：make_handler(components) 返回 Handler 类——
不再有模块级装配副作用（import 不建库、不碰数据目录），
同一进程可起多个不同组件的服务实例（测试友好）。

web 静态资源在包内（interfaces/web/），经 importlib.resources 读取——
pip 安装后可读，不依赖源码目录结构。

API：
    GET  /api/stats                总览统计
    GET  /api/graph                全量图数据（nodes + edges）
    GET  /api/node/{id}            单条知识详情（全文从真值组装）
    GET  /api/anchor/{id}          锚点对话正文（溯源）
    GET  /api/anchors              锚点列表
    GET  /api/search?q=xxx         检索
    GET  /api/traverse/{id}?depth=2  图遍历
    POST /api/link/add             {from_id, to_id, edge_type}   建边（幂等）
    POST /api/link/delete          {from_id, to_id, edge_type}   删边（精确→反向兜底）
    POST /api/node/delete          {id, cascade}  删节点（D7：移出索引，真值保留）
"""
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler
from importlib.resources import files

from ..composition import Components
from ..domain.models import EdgeType

# 页面可建/删的边类型（trace 由系统维护，页面不暴露）
_VALID_EDGE_TYPES = {EdgeType.PARENT.value, EdgeType.LINK.value}

_CONTENT_TYPES = {
    ".html": "text/html", ".js": "application/javascript",
    ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
    ".json": "application/json",
}


def _read_web_asset(rel: str) -> tuple[bytes, str] | None:
    """读包内 web 静态资源；不存在或路径越界返回 None。"""
    if ".." in rel:
        return None
    import os
    res = files("micro_mem.interfaces").joinpath("web")
    for part in rel.split("/"):
        if part:
            res = res.joinpath(part)
    try:
        if not res.is_file():
            return None
        return res.read_bytes(), _CONTENT_TYPES.get(
            os.path.splitext(rel)[1], "application/octet-stream")
    except OSError:
        return None


def make_handler(components: Components) -> type[BaseHTTPRequestHandler]:
    """装配组件 → 请求处理类（闭包持有组件，无模块级状态）。"""
    ctx = components

    class Handler(BaseHTTPRequestHandler):
        """请求处理：API 走 JSON，其他走静态文件。"""

        # ================= 分发 =================

        def do_GET(self):
            """按路径分发：/api/* 走 JSON API，其余走静态文件。"""
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self._handle_api(parsed.path, urllib.parse.parse_qs(parsed.query))
            else:
                self._serve_static(parsed.path)

        def do_POST(self):
            """写接口：/api/* 走 JSON API（建边/删边/删节点），其余 405。"""
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._send_json({"error": "method not allowed"})
                return
            try:
                body = self._read_json_body()
                if parsed.path == "/api/link/add":
                    data = self._api_link_add(body)
                elif parsed.path == "/api/link/delete":
                    data = self._api_link_delete(body)
                elif parsed.path == "/api/node/delete":
                    data = self._api_node_delete(body)
                else:
                    data = {"error": "not found"}
            except Exception as e:  # 写接口异常兜底，返回错误信息（不落库）
                data = {"error": str(e)}
            self._send_json(data)

        def _read_json_body(self) -> dict:
            """读取并解析 JSON 请求体（限长 1MB，防误用）。"""
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                raise ValueError("请求体为空")
            if length > 1_000_000:
                raise ValueError("请求体过大（>1MB）")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def log_message(self, fmt, *args):
            """精简日志（抑制默认每请求输出）。"""
            if sys.stderr is not None:  # pythonw 无 console
                sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")

        # ================= 读 API =================

        def _handle_api(self, path: str, query: dict) -> None:
            """路由 API 到对应实现。"""
            try:
                data: object
                if path == "/api/stats":
                    data = self._api_stats()
                elif path == "/api/graph":
                    data = self._api_graph()
                elif path == "/api/anchors":
                    data = [{"id": a.id, "file": f"{a.id}.md"}
                            for a in ctx.truth.list_anchors()]
                elif path.startswith("/api/node/"):
                    data = self._api_node(path.split("/")[-1])
                elif path.startswith("/api/anchor/"):
                    data = self._api_anchor(path.split("/")[-1])
                elif path.startswith("/api/traverse/"):
                    data = self._api_traverse(path.split("/")[-1],
                                              int(query.get("depth", ["2"])[0]))
                elif path == "/api/search":
                    data = [{"id": h.id, "title": h.title, "summary": h.summary,
                             "type": h.type.value, "scope": h.scope.value}
                            for h in ctx.search.search(query.get("q", [""])[0], limit=20)]
                else:
                    data = {"error": "not found"}
            except Exception as e:  # API 异常兜底，返回错误信息
                data = {"error": str(e)}
            self._send_json(data)

        def _api_stats(self) -> dict:
            """总览统计：知识数、类型分布、边数、锚点数。"""
            nodes = ctx.index.get_all_nodes()
            type_dist: dict[str, int] = {}
            for n in nodes:
                type_dist[n.type] = type_dist.get(n.type, 0) + 1
            return {"total": len(nodes), "type_dist": type_dist,
                    "edge_count": len(ctx.index.get_all_edges()),
                    "anchor_count": len(ctx.truth.list_anchors())}

        def _api_graph(self) -> dict:
            """全量图数据：nodes + edges（前端 ECharts graph 用）。"""
            return {
                "nodes": [{"id": n.id, "name": n.title, "type": n.type,
                           "scope": n.scope, "summary": n.summary, "file": n.file}
                          for n in ctx.index.get_all_nodes()],
                "edges": [{"source": f, "target": t, "type": et}
                          for f, t, et in ctx.index.get_all_edges()],
            }

        def _api_node(self, node_id: str) -> dict:
            """单条知识详情（body/关联/外部锚点——全文从真值组装，D3）。"""
            k = ctx.search.get(node_id)
            if k is None:
                return {"error": f"节点不存在: {node_id}"}
            return {
                "id": k.id, "type": k.type.value, "scope": k.scope.value,
                "title": k.title, "summary": k.summary, "body": k.body,
                "status": k.status.value, "created": k.created, "updated": k.updated,
                "parents": k.parents, "links": k.links,
                "external_refs": [{"type": r.type.value, "value": r.value}
                                  for r in k.external_refs],
                "sources": [{"type": s.type.value, "ref": s.ref} for s in k.sources],
            }

        def _api_anchor(self, anchor_id: str) -> dict:
            """锚点对话正文（溯源，从真值读）。"""
            a = ctx.truth.get_anchor(anchor_id)
            if a is None:
                return {"error": f"锚点不存在: {anchor_id}"}
            return {"id": anchor_id, "content": a.content}

        def _api_traverse(self, node_id: str, depth: int) -> dict:
            """图遍历：从节点出发 N 跳，返回关联节点信息。"""
            start = ctx.search.get(node_id)
            if start is None:
                return {"error": f"节点不存在: {node_id}"}
            related = []
            for to_id, etype, d in ctx.search.traverse(node_id, depth=depth):
                k = ctx.search.get(to_id)
                if k:
                    related.append({"id": k.id, "title": k.title,
                                    "type": k.type.value, "scope": k.scope.value,
                                    "summary": k.summary, "edge": etype, "depth": d})
            return {"start": {"id": start.id, "title": start.title}, "related": related}

        # ================= 写 API =================

        def _check_link_args(self, body: dict) -> tuple[str, str, str]:
            """校验建/删边的公共参数，返回 (from_id, to_id, edge_type)。"""
            from_id = str(body.get("from_id") or "").strip()
            to_id = str(body.get("to_id") or "").strip()
            edge_type = str(body.get("edge_type") or "").strip()
            if not from_id or not to_id:
                raise ValueError("from_id / to_id 必填")
            if edge_type not in _VALID_EDGE_TYPES:
                raise ValueError(
                    f"edge_type 必须 ∈ {{{', '.join(sorted(_VALID_EDGE_TYPES))}}}")
            if from_id == to_id:
                raise ValueError("from_id 与 to_id 不能相同")
            return from_id, to_id, edge_type

        def _api_link_add(self, body: dict) -> dict:
            """建边（幂等；parent/link 同步真值）。"""
            from_id, to_id, edge_type = self._check_link_args(body)
            if (ctx.index.get_node(from_id) is None
                    or ctx.index.get_node(to_id) is None):
                raise ValueError("from_id 或 to_id 节点不存在")
            ctx.knowledge.add_edge(from_id, to_id, EdgeType(edge_type))
            return {"ok": True,
                    "edge": {"from_id": from_id, "to_id": to_id, "edge_type": edge_type}}

        def _api_link_delete(self, body: dict) -> dict:
            """删边：先精确方向，未命中再试反向；都没有返回 deleted=False。"""
            from_id, to_id, edge_type = self._check_link_args(body)
            for f, t in ((from_id, to_id), (to_id, from_id)):  # 精确 → 反向兜底
                if t in [t2 for t2, _ in ctx.index.get_edges(f, [edge_type])]:
                    ctx.knowledge.remove_edge(f, t, EdgeType(edge_type))
                    return {"ok": True, "deleted": True,
                            "edge": {"from_id": f, "to_id": t, "edge_type": edge_type}}
            return {"ok": True, "deleted": False, "msg": "未找到该方向的关联边"}

        def _direct_related(self, node_id: str) -> list[str]:
            """直接关联节点（1 跳）：出边目标 + 入边来源，去重排序。"""
            related = {t for t, _ in ctx.index.get_edges(node_id)}
            related |= {f for f, t, _ in ctx.index.get_all_edges() if t == node_id}
            return sorted(related)

        def _api_node_delete(self, body: dict) -> dict:
            """删节点（D7：移出索引工作集，真值保留）。cascade=True 连带 1 跳关联。"""
            node_id = str(body.get("id") or "").strip()
            cascade = bool(body.get("cascade", False))
            if not node_id:
                raise ValueError("id 必填")
            if ctx.index.get_node(node_id) is None:
                raise ValueError(f"节点不存在: {node_id}")
            to_delete = [node_id] + (self._direct_related(node_id) if cascade else [])
            delete_set = set(to_delete)
            edge_count = sum(1 for f, t, _ in ctx.index.get_all_edges()
                             if f in delete_set or t in delete_set)
            ref_count = sum(len(ctx.index.get_external_refs(nid)) for nid in to_delete)
            for nid in to_delete:
                ctx.knowledge.delete(nid)
            return {"ok": True, "deleted": to_delete, "cascade": cascade,
                    "removed_edges": edge_count, "removed_refs": ref_count}

        # ================= 静态文件 =================

        def _serve_static(self, path: str) -> None:
            """提供包内 web/ 静态资源。"""
            if path == "/":
                path = "/index.html"
            asset = _read_web_asset(path.lstrip("/"))
            if asset is None:
                self.send_error(404)
                return
            body, ctype = asset
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data) -> None:
            """发送 JSON 响应（UTF-8）。"""
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
