"""SQLite 实现（默认引擎）：五张表读写、FTS 更新、rowid 对齐、递归 CTE 图遍历。

一致性约定：
- nodes 与 nodes_fts 用同一个 rowid 对齐（create 时取 lastrowid 回填）
- FTS5 不支持 UPDATE，改 title/body 用 delete + insert
- 所有写操作在事务内原子完成
"""
import json
import os
import sqlite3

from .base import NetworkStore, NodeRecord

# 项目根目录（schema.sql 所在处）：src/micro_mem/store/sqlite_store.py → 上溯四级
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SCHEMA_PATH = os.path.join(ROOT, "schema.sql")

# 三类边类型常量
EDGE_TYPES = ("parent", "link", "trace")
REF_TYPES = ("idev", "mr", "uat", "url")

# 语义检索距离过滤系数：排除 distance > 最近距离 * DIST_RATIO 的近邻（语义不相关）
DIST_RATIO = 1.15


class SqliteNetworkStore(NetworkStore):
    """SQLite 实现（默认引擎）。"""

    def __init__(self, db_path: str, vec_dim: int = 1024):
        """连接数据库；首次连接自动执行 schema.sql 建表（幂等）；向量表按 vec_dim 建。"""
        self.db_path = db_path
        self._vec_dim = vec_dim
        self._vec_available = False
        self._conn = self._connect()
        self._ensure_schema()

    # ---------------- 连接与建表 ----------------

    def _connect(self) -> sqlite3.Connection:
        """建立连接：确保父目录存在，用 Row 工厂；尝试加载 sqlite-vec 扩展（向量检索）。"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self._vec_available = self._try_load_vec(conn)
        return conn

    def _try_load_vec(self, conn: sqlite3.Connection) -> bool:
        """尝试加载 sqlite-vec 扩展（Python 3.11+ 需先 enable_load_extension）。"""
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
            return True
        except Exception:
            return False

    def _ensure_schema(self) -> None:
        """首次连接执行 schema.sql（IF NOT EXISTS，幂等可重复）；向量可用时建 nodes_vec。"""
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            self._conn.executescript(f.read())
        if self._vec_available:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS nodes_vec "
                f"USING vec0(embedding float[{self._vec_dim}])")
        self._conn.commit()

    # ---------------- 构建 / 维护 ----------------

    def create_node(self, node: NodeRecord, body: str = "") -> None:
        """新增节点；body 非空则同步 FTS 索引（独立表存内容+倒排）。"""
        cur = self._conn.execute(
            "INSERT INTO nodes(id, file, title, summary, type, scope, status, created, updated) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (node.id, node.file, node.title, node.summary,
             node.type, node.scope, node.status, node.created, node.updated))
        rowid = cur.lastrowid
        if body:
            assert rowid is not None
            self._fts_insert(rowid, node.title, body)
        self._conn.commit()

    def update_node(self, node: NodeRecord, new_body: str = "") -> None:
        """更新节点；title/body 变化时同步 FTS（FTS5 不支持 UPDATE，用 delete+insert）。"""
        old = self.get_node(node.id)
        if old is None:
            raise KeyError(f"节点不存在: {node.id}")

        old_title = old.title
        old_rowid = old.rowid
        assert old_rowid is not None
        old_body = self._get_fts_body(old_rowid)
        new_body = new_body or old_body     # 未传新 body = body 不变

        with self._conn:
            # 更新 nodes 元数据
            self._conn.execute(
                "UPDATE nodes SET file=?, title=?, summary=?, type=?, scope=?, status=?, updated=? "
                "WHERE id=?",
                (node.file, node.title, node.summary, node.type,
                 node.scope, node.status, node.updated, node.id))
            # 标题或正文变化 → 重建 FTS 索引
            # 用 FTS5 原生 UPDATE（自动维护倒排），避免 DELETE+INSERT 在 trigram 下残留旧词
            if old_title != node.title or old_body != new_body:
                self._conn.execute(
                    "UPDATE nodes_fts SET title=?, body=? WHERE rowid=?",
                    (node.title, new_body, old_rowid))

    def delete_node(self, id: str) -> None:
        """删除节点：连带删除 FTS / edges / external_refs / 向量（保持一致性）。"""
        rec = self.get_node(id)
        if rec is None:
            return
        with self._conn:
            self._conn.execute("DELETE FROM edges WHERE from_id=? OR to_id=?", (id, id))
            self._conn.execute("DELETE FROM external_refs WHERE node_id=?", (id,))
            self._conn.execute("DELETE FROM nodes_fts WHERE rowid=?", (rec.rowid,))
            if self._vec_available:
                self._conn.execute("DELETE FROM nodes_vec WHERE rowid=?", (rec.rowid,))
            self._conn.execute("DELETE FROM nodes WHERE id=?", (id,))

    def get_node(self, id: str) -> NodeRecord | None:
        """按 id 取节点（含 rowid）。"""
        row = self._conn.execute(
            "SELECT rowid, * FROM nodes WHERE id=?", (id,)).fetchone()
        if row is None:
            return None
        return self._row_to_node(row)

    def get_fts_body(self, id: str) -> str:
        """取节点的 FTS 正文（索引副本，读 body 用）。"""
        rec = self.get_node(id)
        if rec is None:
            return ""
        assert rec.rowid is not None
        return self._get_fts_body(rec.rowid)

    # ---------------- 检索 ----------------

    def search_keyword(self, query: str, limit: int = 10,
                       type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """关键词检索：FTS5 字面 + LIKE 兜底（覆盖 2 字中文词），合并去重。"""
        results: dict[str, NodeRecord] = {}
        # FTS5 路：trigram 子串匹配（3 字以上可靠）
        for n in self._search_fts(query, limit, type_filter, scope_filter):
            results[n.id] = n
        # LIKE 兜底：title/summary 包含查询词（2 字词）
        if len(results) < limit:
            for n in self._search_like(query, limit, type_filter, scope_filter):
                results.setdefault(n.id, n)
        return list(results.values())[:limit]

    def _search_fts(self, query: str, limit: int, type_filter: str,
                    scope_filter: str) -> list[NodeRecord]:
        """FTS5 路：倒排命中 rowid（按 rank 排序）→ JOIN nodes。

        查询词用双引号包裹为短语，避免 FTS5 特殊字符（@、引号、冒号等）造成语法错误。
        """
        sql = ("SELECT n.rowid, n.* FROM nodes n "
               "JOIN (SELECT rowid, rank FROM nodes_fts "
               "      WHERE nodes_fts MATCH '\"' || ? || '\"') f ON n.rowid = f.rowid")
        params: list = [query]
        if type_filter:
            sql += " AND n.type=?"
            params.append(type_filter)
        if scope_filter:
            sql += " AND n.scope=?"
            params.append(scope_filter)
        sql += " ORDER BY f.rank LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    def _search_like(self, query: str, limit: int, type_filter: str,
                     scope_filter: str) -> list[NodeRecord]:
        """LIKE 兜底：title/summary/body 包含查询词（覆盖 <3 字中文词）。

        body 全文只在 FTS 索引副本（nodes_fts）里、nodes 表无 body 列，
        故 body 单独从 nodes_fts JOIN nodes 查，补齐 2 字正文词的召回。
        """
        pattern = f"%{query}%"
        # 1) nodes 表：title / summary（summary 不在 FTS，只能在此查）
        sql = "SELECT rowid, * FROM nodes WHERE (title LIKE ? OR summary LIKE ?)"
        params: list = [pattern, pattern]
        if type_filter:
            sql += " AND type=?"
            params.append(type_filter)
        if scope_filter:
            sql += " AND scope=?"
            params.append(scope_filter)
        sql += " LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        # 2) nodes_fts 表：body（补正文里的 2 字词；JOIN nodes 取元数据 + type/scope 过滤）
        if len(rows) < limit:
            bsql = ("SELECT n.rowid, n.* FROM nodes n "
                    "JOIN nodes_fts f ON f.rowid = n.rowid WHERE f.body LIKE ?")
            bparams: list = [pattern]
            if type_filter:
                bsql += " AND n.type=?"
                bparams.append(type_filter)
            if scope_filter:
                bsql += " AND n.scope=?"
                bparams.append(scope_filter)
            bsql += " LIMIT ?"
            bparams.append(limit - len(rows))
            rows += self._conn.execute(bsql, bparams).fetchall()
        return [self._row_to_node(r) for r in rows]

    def traverse(self, start_id: str, depth: int = 2,
                 edge_types: list[str] | None = None) -> list[tuple[str, str, int]]:
        """图遍历：递归 CTE 从 start_id 沿边双向扩展 N 跳（出边 + 入边）。

        双向保证"从父流程下钻到环节"和"从环节上溯到父流程"都可达。
        返回 (节点id, 边类型, 跳数)。
        """
        edge_types = edge_types or list(EDGE_TYPES)
        placeholders = ",".join("?" * len(edge_types))
        sql = f"""
        WITH RECURSIVE reach(node_id, edge_type, d) AS (
            SELECT ?, '', 0
            UNION ALL
            SELECT e.to_id, e.edge_type, r.d + 1
            FROM reach r JOIN edges e ON e.from_id = r.node_id
            WHERE r.d < ? AND e.edge_type IN ({placeholders})
            UNION ALL
            SELECT e.from_id, e.edge_type, r.d + 1
            FROM reach r JOIN edges e ON e.to_id = r.node_id
            WHERE r.d < ? AND e.edge_type IN ({placeholders})
        )
        SELECT node_id, edge_type, d FROM reach WHERE d > 0 AND node_id != ? ORDER BY d
        """
        params = ([start_id, depth] + edge_types
                  + [depth] + edge_types + [start_id])
        rows = self._conn.execute(sql, params).fetchall()
        return [(r["node_id"], r["edge_type"], r["d"]) for r in rows]

    # ---------------- 向量检索 ----------------

    def save_vector(self, node_id: str, embedding: list) -> None:
        """存向量到 nodes_vec（按 node_id 定位 rowid 关联 nodes；向量用 JSON 数组）。"""
        if not self._vec_available:
            return
        rec = self.get_node(node_id)
        if rec is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO nodes_vec(rowid, embedding) VALUES(?, ?)",
            (rec.rowid, json.dumps(embedding)))
        self._conn.commit()

    def search_semantic(self, query_vec: list, limit: int = 10,
                        type_filter: str = "", scope_filter: str = "") -> list[NodeRecord]:
        """语义检索：向量最近邻 → 距离过滤 → JOIN nodes 取数据。

        距离过滤：排除与最近距离差距过大的近邻（distance > 最近距离 * DIST_RATIO），
        避免把语义不相关的"最近邻"混入（向量检索无相关性阈值，需此兜底）。
        """
        if not self._vec_available:
            return []
        # 第一步：取最近邻带 distance
        rows = self._conn.execute(
            "SELECT rowid, distance FROM nodes_vec "
            "WHERE embedding MATCH ? AND k = ?",
            (json.dumps(query_vec), limit)).fetchall()
        if not rows:
            return []
        # 距离过滤：只保留与最近距离接近的候选
        min_d = rows[0]["distance"]
        keep = [r["rowid"] for r in rows
                if r["distance"] <= min_d * DIST_RATIO + 1e-9]
        if not keep:
            return []
        # 第二步：按保留的 rowid 取 nodes（可叠加 type/scope 过滤）
        placeholders = ",".join("?" * len(keep))
        sql = f"SELECT n.rowid, n.* FROM nodes n WHERE n.rowid IN ({placeholders})"
        params: list = list(keep)
        if type_filter:
            sql += " AND n.type=?"
            params.append(type_filter)
        if scope_filter:
            sql += " AND n.scope=?"
            params.append(scope_filter)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    # ---------------- 边 ----------------

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """建边（parent/link/trace），重复建幂等（OR IGNORE）。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO edges(from_id, to_id, edge_type) VALUES(?,?,?)",
            (from_id, to_id, edge_type))
        self._conn.commit()

    def remove_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """删边。"""
        self._conn.execute(
            "DELETE FROM edges WHERE from_id=? AND to_id=? AND edge_type=?",
            (from_id, to_id, edge_type))
        self._conn.commit()

    def get_edges(self, node_id: str, edge_types: list[str] | None = None) -> list[tuple[str, str]]:
        """取节点所有出边（to_id, edge_type），可过滤边类型。"""
        if edge_types:
            placeholders = ",".join("?" * len(edge_types))
            rows = self._conn.execute(
                "SELECT to_id, edge_type FROM edges "
                f"WHERE from_id=? AND edge_type IN ({placeholders})",
                [node_id] + edge_types).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT to_id, edge_type FROM edges WHERE from_id=?", (node_id,)).fetchall()
        return [(r["to_id"], r["edge_type"]) for r in rows]

    def get_all_nodes(self) -> list[NodeRecord]:
        """取全部节点（可视化/统计用）。"""
        rows = self._conn.execute(
            "SELECT rowid, * FROM nodes ORDER BY id").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_all_edges(self) -> list[tuple[str, str, str]]:
        """取全部边（可视化用），返回 (from_id, to_id, edge_type)。"""
        rows = self._conn.execute(
            "SELECT from_id, to_id, edge_type FROM edges ORDER BY edge_type").fetchall()
        return [(r["from_id"], r["to_id"], r["edge_type"]) for r in rows]

    def has_parent_edge(self, node_id: str) -> bool:
        """判断节点是否有 parent 入边（其他节点挂靠它 = 聚合主题）。"""
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE to_id=? AND edge_type='parent'",
            (node_id,)).fetchone()
        return row["c"] > 0

    # ---------------- 外部锚点 ----------------

    def add_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """挂外部锚点（幂等）。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO external_refs(node_id, ref_type, ref_value) VALUES(?,?,?)",
            (node_id, ref_type, ref_value))
        self._conn.commit()

    def remove_external_ref(self, node_id: str, ref_type: str, ref_value: str) -> None:
        """移除外部锚点。"""
        self._conn.execute(
            "DELETE FROM external_refs WHERE node_id=? AND ref_type=? AND ref_value=?",
            (node_id, ref_type, ref_value))
        self._conn.commit()

    def get_external_refs(self, node_id: str) -> list[tuple[str, str]]:
        """取节点所有外部锚点（ref_type, ref_value）。"""
        rows = self._conn.execute(
            "SELECT ref_type, ref_value FROM external_refs WHERE node_id=?", (node_id,)).fetchall()
        return [(r["ref_type"], r["ref_value"]) for r in rows]

    # ---------------- 重建 ----------------

    def clear_all(self) -> None:
        """清空所有表（rebuild 用：从真值文件重建；含向量表）。"""
        with self._conn:
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM external_refs")
            self._conn.execute("DELETE FROM nodes_fts")
            self._conn.execute("DELETE FROM nodes")
            if self._vec_available:
                self._conn.execute("DELETE FROM nodes_vec")

    # ---------------- 私有工具方法 ----------------

    def _row_to_node(self, row: sqlite3.Row) -> NodeRecord:
        """把 nodes 行转成 NodeRecord（含 rowid）。"""
        return NodeRecord(
            rowid=row["rowid"], id=row["id"], file=row["file"],
            title=row["title"], summary=row["summary"],
            type=row["type"], scope=row["scope"], status=row["status"],
            created=row["created"], updated=row["updated"])

    def _get_fts_body(self, rowid: int) -> str:
        """从 FTS 表取该 rowid 的旧正文（独立表存了内容副本）。"""
        row = self._conn.execute(
            "SELECT body FROM nodes_fts WHERE rowid=?", (rowid,)).fetchone()
        return row["body"] if row else ""

    def _fts_insert(self, rowid: int, title: str, body: str) -> None:
        """FTS5 插入新索引。"""
        self._conn.execute(
            "INSERT INTO nodes_fts(rowid, title, body) VALUES(?,?,?)",
            (rowid, title, body))
