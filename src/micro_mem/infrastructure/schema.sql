-- ============================================
-- 记忆/知识管理系统 · SqliteNetworkStore 完整表结构
-- 用法：sqlite3 memory.db < schema.sql
-- 幂等：全部 IF NOT EXISTS，可重复执行
-- ============================================

-- ① 节点表：每个节点 = 一条知识（真值在 md 文件，此处为可重建副本；无 body）
CREATE TABLE IF NOT EXISTS nodes (
  id      TEXT PRIMARY KEY,                                -- 知识唯一标识：k-0003
  file    TEXT NOT NULL,                                   -- 指向 md 真值：knowledge/k-0003_xxx.md
  title   TEXT NOT NULL,                                   -- 标题（候选列表一眼扫）
  summary TEXT,                                            -- 判断用摘要（确认"是不是这件事"，不读全文）
  type    TEXT NOT NULL CHECK (type IN ('event','model','fact','method')),
  scope   TEXT NOT NULL CHECK (scope IN ('universal','domain','personal')),
  status  TEXT NOT NULL DEFAULT 'draft'
          CHECK (status IN ('draft','evolving','settled','deprecated')),
  aspect  TEXT NOT NULL DEFAULT '',   -- 领域切面：flow|structure|boundary|constraint|…（值域规则层约定，可扩展）
  created TEXT,
  updated TEXT
);

-- ② 全文索引：关键词字面检索（FTS5 独立表：自存倒排+内容，rebuild 时从 md 读正文喂入）
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  title, body,
  tokenize = 'trigram'        -- 中文子串匹配：搜"需求"命中"需求单"
);

-- ③ 向量索引：模糊提示词语义检索（sqlite-vec，N = embedding 维度，如 1024）
-- 需要加载 sqlite-vec 扩展，第二批启用；无扩展时建表会失败，由 sqlite_store 捕获跳过
-- CREATE VIRTUAL TABLE IF NOT EXISTS nodes_vec USING vec0(embedding float[1024]);

-- ④ 边表：网的关系（挂靠/关联/溯源）
CREATE TABLE IF NOT EXISTS edges (
  from_id   TEXT NOT NULL REFERENCES nodes(id),
  to_id     TEXT NOT NULL REFERENCES nodes(id),
  edge_type TEXT NOT NULL CHECK (edge_type IN ('parent','link','trace')),
  PRIMARY KEY (from_id, to_id, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(to_id);

-- ⑤ 外部锚点：知识 ↔ 外部真实世界（需求单号/MR/UAT/URL）
CREATE TABLE IF NOT EXISTS external_refs (
  node_id   TEXT NOT NULL REFERENCES nodes(id),
  ref_type  TEXT NOT NULL CHECK (ref_type IN ('idev','mr','uat','url')),
  ref_value TEXT NOT NULL,
  PRIMARY KEY (node_id, ref_type, ref_value)
);
