"""记忆系统可视化 HTTP 服务：静态页面 + API。

用法：
    python src/server.py [--port 8000]
前端：
    http://localhost:8000/  （web/index.html）

API：
    GET  /api/stats            总览统计
    GET  /api/graph            全量图数据（nodes + edges）
    GET  /api/node/{id}        单条知识详情
    GET  /api/search?q=xxx     检索
    GET  /api/traverse/{id}?depth=2  图遍历
    GET  /api/anchors          锚点列表
    写接口（页面暂未接入，供维护/脚本调用）：
    POST /api/link/add         {from_id, to_id, edge_type}         建边（幂等）
    POST /api/link/delete      {from_id, to_id, edge_type}         删边（精确→反向兜底）
    POST /api/node/delete      {id, cascade}                       删节点（cascade: 连带直接关联节点）
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

# 支持 pythonw 后台运行（无 console）：stdout/stderr 可能为 None，重定向到 devnull 避免崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 项目根加入 path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.api.reader import MemoryReader          # noqa: E402
from src.api.writer import MemoryWriter          # noqa: E402
from src.config import Config                    # noqa: E402
from src.embedder import HashEmbedder            # noqa: E402
from src.md_parser import parse_frontmatter      # noqa: E402
from src.store.sqlite_store import SqliteNetworkStore  # noqa: E402
from src.types import EdgeType                   # noqa: E402

WEB_DIR = os.path.join(ROOT, "web")

# 装配组件（单例，服务生命周期内复用）
config = Config()
store = SqliteNetworkStore(config.db_path(), vec_dim=config.embedding_dim)
embedder = HashEmbedder(dim=config.embedding_dim)
writer = MemoryWriter(config, store, embedder=embedder)
reader = MemoryReader(config, store, embedder=embedder)


class Handler(BaseHTTPRequestHandler):
    """请求处理：API 走 JSON，其他走静态文件。"""

    # ================= 分发 =================

    def do_GET(self):
        """按路径分发：/api/* 走 JSON API，其余走静态文件。"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            self._handle_api(path, query)
        else:
            self._serve_static(path)

    def do_POST(self):
        """写接口：/api/* 走 JSON API（建边/删边/删节点），其余 405。"""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_write_api(path)
        else:
            self._send_json({"error": "method not allowed"})

    def _read_json_body(self) -> dict:
        """读取并解析 JSON 请求体（限长 1MB，防误用）。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            raise ValueError("请求体为空")
        if length > 1_000_000:
            raise ValueError("请求体过大（>1MB）")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _handle_write_api(self, path: str) -> None:
        """路由写接口：解析 JSON body → 分发 → 统一 JSON 响应。"""
        try:
            body = self._read_json_body()
            if path == "/api/link/add":
                data = self._api_link_add(body)
            elif path == "/api/link/delete":
                data = self._api_link_delete(body)
            elif path == "/api/node/delete":
                data = self._api_node_delete(body)
            else:
                data = {"error": "not found"}
        except Exception as e:  # 写接口异常兜底，返回错误信息（不落库）
            data = {"error": str(e)}
        self._send_json(data)

    def log_message(self, fmt, *args):
        """精简日志（抑制默认每请求输出）。"""
        sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")

    # ================= API =================

    def _handle_api(self, path: str, query: dict) -> None:
        """路由 API 到对应实现。"""
        try:
            if path == "/api/stats":
                data = self._api_stats()
            elif path == "/api/graph":
                data = self._api_graph()
            elif path == "/api/anchors":
                data = self._api_anchors()
            elif path.startswith("/api/node/"):
                data = self._api_node(path.split("/")[-1])
            elif path.startswith("/api/anchor/"):
                data = self._api_anchor(path.split("/")[-1])
            elif path.startswith("/api/traverse/"):
                node_id = path.split("/")[-1]
                depth = int(query.get("depth", ["2"])[0])
                data = self._api_traverse(node_id, depth)
            elif path == "/api/search":
                data = self._api_search(query.get("q", [""])[0])
            else:
                data = {"error": "not found"}
        except Exception as e:  # API 异常兜底，返回错误信息
            data = {"error": str(e)}
        self._send_json(data)

    def _api_stats(self) -> dict:
        """总览统计：知识数、类型分布、边数、锚点数。"""
        nodes = store.get_all_nodes()
        type_dist = {}
        for n in nodes:
            type_dist[n.type] = type_dist.get(n.type, 0) + 1
        return {
            "total": len(nodes),
            "type_dist": type_dist,
            "edge_count": len(store.get_all_edges()),
            "anchor_count": len([f for f in os.listdir(config.anchors_dir()) if f.endswith(".md")]),
        }

    def _api_graph(self) -> dict:
        """全量图数据：nodes + edges（前端 ECharts graph 用）。"""
        nodes = [{"id": n.id, "name": n.title, "type": n.type,
                  "scope": n.scope, "summary": n.summary, "file": n.file}
                 for n in store.get_all_nodes()]
        edges = [{"source": f, "target": t, "type": et}
                 for f, t, et in store.get_all_edges()]
        return {"nodes": nodes, "edges": edges}

    def _api_node(self, node_id: str) -> dict:
        """单条知识详情（含 body/关联/外部锚点）。"""
        k = reader.get(node_id)
        if k is None:
            return {"error": f"节点不存在: {node_id}"}
        return {
            "id": k.id, "type": k.type.value, "scope": k.scope.value,
            "title": k.title, "summary": k.summary, "body": k.body,
            "status": k.status.value, "created": k.created, "updated": k.updated,
            "parents": k.parents, "links": k.links,
            "external_refs": [{"type": r.type.value, "value": r.value} for r in k.external_refs],
            "sources": [{"type": s.type.value, "ref": s.ref} for s in k.sources],
        }

    def _api_anchor(self, anchor_id: str) -> dict:
        """锚点对话正文（溯源；解析 frontmatter，只返回对话内容）。"""
        path = os.path.join(config.anchors_dir(), f"{anchor_id}.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _, body = parse_frontmatter(f.read())
            return {"id": anchor_id, "content": body}
        return {"error": f"锚点不存在: {anchor_id}"}

    def _api_traverse(self, node_id: str, depth: int) -> dict:
        """图遍历：从节点出发 N 跳，返回关联节点信息。"""
        start = reader.get(node_id)
        if start is None:
            return {"error": f"节点不存在: {node_id}"}
        related = []
        for to_id, etype, d in reader.traverse(node_id, depth=depth):
            k = reader.get(to_id)
            if k:
                related.append({"id": k.id, "title": k.title,
                                "type": k.type.value, "scope": k.scope.value,
                                "summary": k.summary, "edge": etype, "depth": d})
        return {"start": {"id": start.id, "title": start.title}, "related": related}

    def _api_search(self, q: str) -> dict:
        """检索：双路召回返回候选。"""
        hits = reader.search(q, limit=20)
        return [{"id": h.id, "title": h.title, "summary": h.summary,
                 "type": h.type.value, "scope": h.scope.value} for h in hits]

    # ================= 写接口 =================

    _VALID_EDGE_TYPES = {et.value for et in EdgeType}  # {"parent", "link"}

    def _check_link_args(self, body: dict) -> tuple[str, str, str]:
        """校验建/删边的公共参数，返回 (from_id, to_id, edge_type)。"""
        from_id = str(body.get("from_id") or "").strip()
        to_id = str(body.get("to_id") or "").strip()
        edge_type = str(body.get("edge_type") or "").strip()
        if not from_id or not to_id:
            raise ValueError("from_id / to_id 必填")
        if edge_type not in self._VALID_EDGE_TYPES:
            raise ValueError(f"edge_type 必须 ∈ {{{', '.join(sorted(self._VALID_EDGE_TYPES))}}}")
        if from_id == to_id:
            raise ValueError("from_id 与 to_id 不能相同")
        return from_id, to_id, edge_type

    def _api_link_add(self, body: dict) -> dict:
        """建边（幂等）。"""
        from_id, to_id, edge_type = self._check_link_args(body)
        if store.get_node(from_id) is None or store.get_node(to_id) is None:
            raise ValueError("from_id 或 to_id 节点不存在")
        writer.add_edge(from_id, to_id, EdgeType(edge_type))
        return {"ok": True, "edge": {"from_id": from_id, "to_id": to_id, "edge_type": edge_type}}

    def _api_link_delete(self, body: dict) -> dict:
        """删边：先精确方向，未命中再试反向；都没有返回 deleted=False。"""
        from_id, to_id, edge_type = self._check_link_args(body)
        # 精确方向：from_id → to_id
        if to_id in [t for t, _ in store.get_edges(from_id, [edge_type])]:
            writer.remove_edge(from_id, to_id, EdgeType(edge_type))
            return {"ok": True, "deleted": True, "edge": {"from_id": from_id, "to_id": to_id, "edge_type": edge_type}}
        # 反向兜底：to_id → from_id
        if from_id in [t for t, _ in store.get_edges(to_id, [edge_type])]:
            writer.remove_edge(to_id, from_id, EdgeType(edge_type))
            return {"ok": True, "deleted": True, "edge": {"from_id": to_id, "to_id": from_id, "edge_type": edge_type}}
        return {"ok": True, "deleted": False, "msg": "未找到该方向的关联边"}

    def _direct_related(self, node_id: str) -> list[str]:
        """直接关联节点（1 跳）：出边目标 + 入边来源，去重排序。"""
        related = set()
        for to_id, _ in store.get_edges(node_id, None):
            related.add(to_id)
        for from_id, to_id, _ in store.get_all_edges():
            if to_id == node_id:
                related.add(from_id)
        return sorted(related)

    def _api_node_delete(self, body: dict) -> dict:
        """删节点。cascade=True 时连带直接关联节点（1 跳）一起删。"""
        node_id = str(body.get("id") or "").strip()
        cascade = bool(body.get("cascade", False))
        if not node_id:
            raise ValueError("id 必填")
        if store.get_node(node_id) is None:
            raise ValueError(f"节点不存在: {node_id}")
        to_delete = [node_id] + (self._direct_related(node_id) if cascade else [])
        delete_set = set(to_delete)
        # 受影响计数（删除前，唯一边去重）
        edge_count = sum(1 for f, t, _ in store.get_all_edges()
                         if f in delete_set or t in delete_set)
        ref_count = sum(len(store.get_external_refs(nid)) for nid in to_delete)
        for nid in to_delete:
            writer.delete_knowledge(nid)
        return {"ok": True, "deleted": to_delete, "cascade": cascade,
                "removed_edges": edge_count, "removed_refs": ref_count}

    def _api_anchors(self) -> dict:
        """锚点列表（溯源）。"""
        anchors = []
        for f in sorted(os.listdir(config.anchors_dir())):
            if f.endswith(".md"):
                anchors.append({"id": f[:-3], "file": f})
        return anchors

    # ================= 静态文件 =================

    def _serve_static(self, path: str) -> None:
        """提供 web/ 下的静态文件。"""
        if path == "/":
            path = "/index.html"
        file_path = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not file_path.startswith(WEB_DIR) or not os.path.exists(file_path) or os.path.isdir(file_path):
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html", ".js": "application/javascript",
            ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
            ".json": "application/json",
        }.get(os.path.splitext(file_path)[1], "application/octet-stream")
        with open(file_path, "rb") as f:
            body = f.read()
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


def main() -> None:
    """启动服务（单线程 HTTPServer：本地可视化，避免 SQLite 跨线程问题）。"""
    port = 8000
    if len(sys.argv) > 1:
        if sys.argv[1] == "--port" and len(sys.argv) > 2:
            port = int(sys.argv[2])
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"记忆系统可视化: http://localhost:{port}/")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
