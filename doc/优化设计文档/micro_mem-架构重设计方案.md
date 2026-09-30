# micro_mem 架构重设计方案（定稿）

> 状态：**已定稿**（经讨论对齐）｜ 日期：2026-09-30 ｜ 版本：v1.0
>
> 本文档是 micro_mem 的**首次完整架构设计**。它不以第一轮工程化整改为前提、不受其框架限制；
> 第一轮的骨架（src-layout / pytest / pyproject / 分层意识）作为既有资产被吸收，但全部结构决策以本文档为准。
>
> 对齐过程：第二轮全量代码审查（~3100 行通读 + 实证复现）→ 问题诊断 → 多轮讨论拍板 → 定稿。

---

## 一、背景：为什么是重设计，不是修补

### 1.1 第二轮审查的关键发现（实证）

| # | 发现 | 证据 | 处置 |
|---|---|---|---|
| B1 | `delete_knowledge` 只删索引不删 md 真值，`rebuild` 后复活 | writer.py:344；临时目录复现确认 | **不修**（用户拍板）：delete 语义 = 移出索引工作集，真值档案保留；契约文档化（见 D7） |
| B2 | 空 body 创建 → update 补 body → body 从索引与真值**双双丢失** | sqlite_store.py:103 FTS UPDATE 命中 0 行静默跳过 → `_rewrite_md_file` 读回空 body 覆盖真值；临时目录复现确认 | **修**（FTS 改 upsert；新架构中该 bug 从结构上灭绝，见 D3） |
| — | `_read_sources` 在 writer/reader 逐字重复两份；序列化三个实现 | writer.py:322 ≡ reader.py:125 | 重构消解 |
| — | `Distiller._find_existing` 全表扫描 + 每节点回读 md（N+1） | distiller.py:72-86 | 重构消解（索引预筛） |
| — | server.py import 即连真实库，不可测；test_api.py 测的是复制副本 | server.py:44 模块级装配 | 重构消解（工厂模式） |
| — | 双入口命令形态不一致：包装器 `mem anchor` vs pip `mem-distill anchor` | mem.cmd vs pyproject.toml | 重构消解（单命令树） |
| — | 项目根靠"文件上溯四级"推导，非 editable 安装即坏；schema.sql 不入包 | config.py:16 / sqlite_store.py:15 | 重构消解（资源入包） |
| — | 文档大面积滞后：README 仍写 3×3 类型、双路召回、`mem anchor`；TEST_CASES.md 引用已删除的 run_tests.py | 多出处 | 本轮完成后统一重写 |
| — | 仓库卫生：`candidates.json`（含真实业务信息）与 `.playwright-mcp/` 被提交；cache 目录未 gitignore | git ls-files | 阶段 0 清理 |

### 1.2 三个架构缺口（根因诊断）

第一轮的目录搬家是对的，但只做了"卫生"没做"架构"。系统最重要的设计决策在代码里没有对应实体：

1. **真值没有"家"**。第一原则说"真值在 Markdown"，但没有任何组件对 Markdown 真值负责：文件 IO 散落在 writer（写知识/写锚点）、reader（读 sources）、distiller（读锚点）、server（读锚点），序列化标准两套（yaml.safe_dump vs 字符串拼接+正则）。B2 bug 与 split-brain 讨论的根因都在此。
2. **用例编排泡在入口层**。`cmd_confirm` 的主事件两段式、MAIN 占位、游标回写、自动挂靠是蒸馏用例的核心编排，住在 CLI 函数里；后果是测试只能复制逻辑测副本（test_api.py / test_d2）。
3. **序列化三分天下**。`to_dict` / `_write_md_file` 手搓字典 / `knowledge_from_meta` 三处实现，改一个字段动三处。

### 1.3 用户拍板（本次重构的约束输入）

| # | 拍板 | 内容 |
|---|---|---|
| 1 | 路线 | **完全重构**，假设没有上轮优化，按首次架构设计来做 |
| 2 | TruthStore | **抽象接口**（用户有真值不局限于 md 的扩展计划） |
| 3 | 旧入口点 | `mem-distill`/`mem-server`/`mem-build-db`/`mem-import-one` 删除，统一 `mem` 单命令树 |
| 4 | 仓库卫生 | 清理，含 git 历史（filter-repo 清 candidates.json / .playwright-mcp） |
| 5 | 领域骨架校验 | 维持**纯文档约定**（skill 层），不进代码 |
| 6 | 语义兜底策略 | 提为配置项 |
| 7 | README | 本轮后置，优化完成后统一重写（面向开源） |
| 8 | 自动挂靠 | 保留现状启发式并入 DistillService；**"知识节点如何正确建立关联关系"另立专题详细设计**（见第十一章） |

---

## 二、设计驱动力（架构要回应的"力"）

1. **双源是系统最独特的决策**：真值（人读）与索引（机读）分离、索引可重建 → 两个显式存储端口，而非一个 store 包打天下。
2. **真值未来可能不是 Markdown** → 真值存取必须端口化。
3. **"判断归模型"** → 消费方是 AI/CLI/HTTP，入口要极薄极稳定，业务逻辑必须可脱离入口测试。
4. **蒸馏是核心增值流程**（候选→审核→落库的工作流）→ 应用层的头号公民。
5. **个人项目、~2000 行** → 为"变化"设计，不为"规模"设计；抽象宁缺毋滥，但端口一个不能少。

---

## 三、目标架构

### 3.1 分层与目录

```
依赖规则：interfaces → application → domain ← infrastructure
          application 定义端口，infrastructure 实现端口（依赖倒置）
```

```
src/micro_mem/
├── domain/                       # 领域层：纯类型 + 领域规则，零依赖
│   └── models.py                 #   Knowledge/Anchor/Source/… 全部 dataclass
│                                 #   序列化对称（to_dict/from_dict），不知 YAML 为何物
├── application/                  # 应用层：用例编排（唯一的业务逻辑所在）
│   ├── ports.py                  # ★ 三个端口（ABC）：TruthStore / IndexStore / Embedder
│   ├── knowledge_service.py      #   收录/更新/废弃/删除（双源协调的唯一责任点）
│   ├── search_service.py         #   检索/多词融合/遍历/详情组装
│   ├── distill_service.py        #   蒸馏三段：anchor → 增量准备 → confirm 编排
│   │                             #   （MAIN 两段式、游标回写、自动挂靠，全收进来）
│   ├── import_service.py         #   存量/单条导入
│   └── rebuild_service.py        #   索引重建（从 CLI 收上来）
├── infrastructure/               # 出适配器：端口的实现
│   ├── config.py                 #   配置加载与路径解析（重写，见 D5）
│   ├── markdown_truth.py         #   TruthStore 的 Markdown 实现：所有文件 IO 唯一收口
│   ├── sqlite_index.py           #   IndexStore 的 SQLite 实现
│   ├── schema.sql                #   ★ 入包（importlib.resources 读，pip install 不自损）
│   ├── hash_embedder.py          #   Embedder 的 Hash 实现
│   ├── memory_truth.py           #   ★ InMemory 实现（测试基建 + 路线图"内存引擎"一举两得）
│   ├── memory_index.py           #
│   └── claude_jsonl.py           #   Claude 会话格式 → 领域对话（反腐层，原 importer 私货）
├── interfaces/                   # 入适配器：零业务，解析参数 → 调服务 → 格式化输出
│   ├── cli.py                    #   ★ 单 mem 命令树（12 子命令收编，见 D4）
│   ├── http.py                   #   可视化服务：make_handler(components) 工厂，import 无副作用
│   └── web/                      #   静态页面入包
├── __main__.py                   # python -m micro_mem 支持
└── composition.py                # 装配根：唯一认识所有实现的地方；可注入 config 路径
```

**旧概念的归宿**：Writer/Reader/Distiller/Importer 四接口的边界（按"读写/导入"切）不符合用例的真实形状（蒸馏横跨读写）。新服务按**用例**切。"记忆/蒸馏/收录/关联"四操作作为领域语言保留在文档。新旧映射见第九章。

### 3.2 端口契约（application/ports.py）

```python
class TruthStore(ABC):
    """真值存取端口：知识/锚点的唯一权威来源。"""

    # 知识
    def save_knowledge(self, k: Knowledge) -> str: ...         # 分配 id，返回 id
    def get_knowledge(self, id: str) -> Knowledge | None: ...  # 含 body + sources 全文
    def list_knowledge(self) -> list[Knowledge]: ...           # rebuild 用
    def delete_knowledge(self, id: str) -> bool: ...           # 物理删真值（谨慎暴露）

    # 锚点
    def save_anchor(self, a: Anchor) -> str: ...
    def get_anchor(self, id: str) -> Anchor | None: ...
    def list_anchors(self) -> list[Anchor]: ...
    def anchor_exists(self, ref: str) -> bool: ...             # 溯源校验用
    def resync_anchor(self, id: str, content: str) -> bool: ...
    def get_distill_cursor(self, anchor_id: str) -> int: ...   # -1 = 未蒸馏
    def set_distill_cursor(self, anchor_id: str, turn: int) -> None: ...

class IndexStore(ABC):
    """索引端口（原 NetworkStore 清理版）：rowid 收回实现内部，upsert 语义。"""

    def upsert_node(self, node: NodeRecord, body: str) -> None: ...  # create/update 合一，B2 根除
    def delete_node(self, id: str) -> None: ...
    def get_node(self, id: str) -> NodeRecord | None: ...            # NodeRecord 不再携带 rowid
    def search_keyword(self, query, limit=10, type_filter="", scope_filter="") -> list[NodeRecord]: ...
    def search_semantic(self, query_vec, limit=10, type_filter="", scope_filter="") -> list[NodeRecord]: ...
    def traverse(self, start_id, depth=2, edge_types=None) -> list[tuple[str, str, int]]: ...
    def add_edge / remove_edge / get_edges / get_all_edges: ...
    def has_parent_edge(self, node_id: str) -> bool: ...
    def add_external_ref / remove_external_ref / get_external_refs: ...
    def get_all_nodes(self) -> list[NodeRecord]: ...
    def save_vector(self, node_id: str, embedding: list[float]) -> None: ...
    def clear_all(self) -> None: ...

class Embedder(ABC):
    """文本向量化端口。"""
    @property
    def dim(self) -> int: ...
    def embed(self, text: str) -> list[float]: ...
```

---

## 四、关键架构决策（D1–D10）

**D1 · 端口在 application 层定义。** 用例声明"我需要什么样的存储"，基础设施去实现（依赖倒置）。红利：application 层测试注入内存实现，不碰盘、不起 SQLite。

**D2 · 序列化收口到实现侧。** `Knowledge` 是纯 dataclass，**不知道自己怎么变成 YAML frontmatter**——那是 `MarkdownTruthStore` 的实现细节。序列化三分天下的局面从结构上消失；未来 TruthStore 换实现，领域对象一行不动。

**D3 · 读 body 改走真值（翻转第一轮"决策 3"）。** 有了 TruthStore 端口后，"body 从 FTS 索引副本读"是多余的暧昧，且是 B2 数据丢失的直接温床。新规则：**search 给轻量候选（索引），get 从 truth 组装全文**。get 是低频操作（模型判中才下钻），一次文件读完全可接受；FTS 表的 body 列沦为纯索引耗材（供检索/LIKE 兜底），任何调用方不把它当数据源。双源 split-brain 从结构上灭绝。

**D4 · 单命令树。** `mem` 收编 12 个子命令：

```
mem search [--multi] [--limit N]     mem get <id>
mem create ...                       mem deprecate <id>
mem delete <id>                      mem traverse <id> [--depth N]
mem rebuild                          mem import --dir <目录> | --file <jsonl>
mem anchor [jsonl]                   mem distill [anchor_id]
mem confirm <candidates.json>        mem serve [--port 8000]
```

pip 入口点只留 `mem` 一个；`mem-distill` 等四个旧入口点删除（未发布、无兼容负担）；包装器（clients/cli/mem.cmd|sh）退化为一行转发 `PYTHONPATH=<root>/src python -m micro_mem %*`——两条路命令形态真正一致，5 个 sys.path hack 全删。

**D5 · 配置与路径自包含。** 废弃所有"文件上溯四级"：

- config 定位：`MEMORY_HOME` 环境变量 > `./config.yaml` > 包内默认值
- `data_dir`：绝对路径直接用；相对路径按 **config.yaml 所在目录**解析
- schema.sql / web/ 入包（`package-data` + `importlib.resources`）
- 清理死配置 `embedding_model`（或届时接上真实用途）

**D6 · 一致性责任一句话。** *"写路径：先 truth 后 index，只在 KnowledgeService 一处协调；失败由 rebuild 兜底（系统既有容错设计，文档化）。读路径：index 出候选，truth 出全文，只在 SearchService 一处组装。"*

**D7 · delete 语义（用户拍板，契约化）。** `delete` = 移出索引工作集，**真值档案保留**；`rebuild` 会按真值全量恢复（想彻底消失：`deprecate` 或手动删 md 文件）。写入 IndexStore/TruthStore 契约注释，防止后人当 bug 修。

**D8 · 语义兜底策略配置化。** `config.yaml` 新增：

```yaml
search:
  semantic_fallback: on_zero_hit   # on_zero_hit（默认，HashEmbedder 时代）| always（真语义模型时代）| off
```

**D9 · 蒸馏编排收编 + CLI 私货清剿。** `cmd_confirm` 的两段式落库、MAIN 占位、游标回写、自动挂靠全部移入 `DistillService.confirm()`；候选 JSON 解析统一走 `DistillCandidate.from_dict`（MAIN 占位替换作为前置一步）；删除硬编码 `search("记忆系统")` 验证输出；EDIT 链路补测试。

**D10 · 测试三层。** domain 纯单测 / application 注入内存端口（毫秒级）/ infrastructure 用 tmp_path 集成测试 / composition 装配后 e2e 冒烟。补齐：EDIT 链路、B2 回归、删除语义。CI 接入 ruff + mypy 门（当前 writer.py:196 E501 失守为鉴）。

**明确不做**：不引 DI 框架、不引 pydantic、不写第二真值/索引实现（内存版只为测试）、不接真语义模型、不动五篇设计 HTML（历史档案）。

---

## 五、调用关系与数据流转（架构讲解）

### 5.1 静态角色：图书馆类比

| 组件 | 图书馆角色 | 职责一句话 |
|---|---|---|
| `TruthStore`（端口） | 书库的规则 | "真本怎么存取"的契约 |
| `MarkdownTruthStore` | 书库管理员 | 唯一碰 md 文件的人：读写、id 分配、frontmatter 序列化 |
| `IndexStore`（端口） | 卡片柜的规则 | "索引怎么查"的契约 |
| `SqliteIndexStore` | 卡片柜管理员 | 五张表、FTS、向量、图遍历；不管真本 |
| `Embedder`（端口） | 主题标引员 | 文本 → 向量 |
| `KnowledgeService` | 采编部 | 新书入库：先上书库、再建卡片（双源协调唯一责任点） |
| `SearchService` | 阅览部馆员 | 查卡片给候选；读者选中后去书库取真本组装 |
| `DistillService` | 编辑部 | 蒸馏三段编排：建档 → 增量 → 审核落库 |
| `ImportService` | 旧档数字化 | 历史 jsonl → 锚点 |
| `RebuildService` | 盘点部 | 卡片柜丢了？照书库全量重建 |
| `composition.py` | 开馆准备 | 唯一认识所有具体实现的房间 |

### 5.2 调用关系：依赖只朝一个方向

```
┌─────────────────────────────────────────────────────────────┐
│ interfaces/   cli.py · http.py                               │
│   只做三件事：解析参数 → 调服务 → 格式化输出。不含任何业务判断      │
└──────────────────────┬──────────────────────────────────────┘
                       │ 调用（构造时注入的服务）
┌──────────────────────▼──────────────────────────────────────┐
│ application/   Knowledge · Search · Distill · Import · Rebuild│
│   服务只 import ports.py，不知道 SQLite/Markdown 的存在         │
└──────────────┬───────────────────────────────┬──────────────┘
               │ 依赖端口（ABC）                 │ 使用值对象
┌──────────────▼──────────────┐   ┌────────────▼─────────────┐
│ application/ports.py         │   │ domain/models.py          │
│ TruthStore·IndexStore·Embedder│   │ Knowledge·Anchor·枚举      │
└──────────────▲──────────────┘   └──────────────────────────┘
               │ 实现（依赖倒置：外层实现内层的契约）
┌──────────────┴──────────────────────────────────────────────┐
│ infrastructure/  MarkdownTruthStore · SqliteIndexStore ·     │
│                  HashEmbedder · InMemory*Store · Config      │
└──────────────────────────────────────────────────────────────┘

composition.py：唯一"越层"的地方——它 import 所有实现，装配成服务，
               递给 interfaces。（Composition Root：装配动作只能发生在最外层）
```

**关键纪律**：服务的构造函数签名是端口类型——`KnowledgeService(truth: TruthStore, index: IndexStore, embedder: Embedder)`。全项目只有 `composition.py` 知道"TruthStore 实际上是 Markdown"。

### 5.3 链路 1 · 收录（写）

```
cli.cmd_create
  │  ① 参数 → Knowledge 对象（domain 值对象，此时无 id）
  ▼
KnowledgeService.create(k)
  │  ② 领域校验（title 非空、type/scope 合法）
  │  ③ truth.save_knowledge(k) ──────► MarkdownTruthStore
  │                                    · 扫目录分配 id（k-0036）
  │                                    · dataclass → frontmatter 序列化（唯一序列化点）
  │                                    · 写 data/knowledge/k-0036_xxx.md
  │  ④ index.upsert_node(record, body) ► SqliteIndexStore（upsert 语义——B2 根除）
  │  ⑤ embedder.embed(k.summary) → index.save_vector(id, vec)
  │  ⑥ parents/links/refs → index.add_edge / add_external_ref
  ▼
cli 打印"已收录 k-0036"
```

数据形态：CLI 字符串 → `Knowledge`（内存对象）→ 分叉为【md 真值文件】+【SQLite 索引副本】。**分叉只发生在 KnowledgeService 一处**——"一致性责任收敛"的物理含义。

### 5.4 链路 2 · 检索与下钻（读）

```
mem search "审批"
  cli → SearchService.search("审批")
          ① index.search_keyword()     FTS5 + LIKE 兜底（纯索引内的事）
          ② 零命中且 semantic_fallback≠off:
             embedder.embed() → index.search_semantic()
          ③ RRF 融合 → list[SearchHit]（轻量：id/title/summary/score）
  cli 打印候选 ──► 模型看 summary 判断（判断归模型，系统不越界）

mem get k-0003        ← 模型选中后下钻
  cli → SearchService.get("k-0003")
          ① index.get_node()          元数据
          ② truth.get_knowledge()     ★ body + sources 从真值读（D3）
          ③ index.get_edges()         关系（parents/links）
          ④ index.get_external_refs() 外部锚点
          组装 → 完整 Knowledge
```

### 5.5 链路 3 · 蒸馏（最复杂的编排，全部收进服务）

```
mem anchor [jsonl]
  cli → DistillService.create_anchor()
          ① claude_jsonl.parse_session()   （反腐层：Claude 格式 → 领域对话文本）
          ② truth.save_anchor()            保真落盘 + 游标置 -1

mem distill
  cli → DistillService.prepare_delta()
          ① truth.get_anchor() → 读 source 定位 jsonl
          ② 重新解析 + truth 重同步锚点全文（保真）
          ③ truth.get_distill_cursor() 算增量轮次
          ④ 写 data/temp/distill_state.json
          ⑤ 返回增量文本 → cli 打印给 AI
  ──── AI 读增量、产出 candidates.json（系统外，判断归模型）────

mem confirm candidates.json
  cli → 解析 JSON（DistillCandidate.from_dict + MAIN 占位前置替换）
      → DistillService.confirm(candidates)
          对每条候选：
            REJECT → 跳过
            EDIT   → KnowledgeService.update(edit_id, ...)（保留 id/sources）
            KEEP   → 幂等去重（index.search_keyword(title) 预筛，不再全表扫）
                   → 溯源校验（truth.anchor_exists(ref)）
                   → KnowledgeService.create()
          ⑥ truth.set_distill_cursor()    回写游标
          ⑦ 自动挂靠（现状启发式，TODO：关联关系专题重设计，见第十一章）
```

CLI 在整条链路里只干 IO（读文件、打印）。编排逻辑一行都不在入口层。

### 5.6 链路 4 · rebuild（架构自证）

```
RebuildService.rebuild()
  index.clear_all()
  for k in truth.list_knowledge():      ← 唯一数据源是真值
      index.upsert_node(...)            ← 真值 → 索引的单向投影
```

这条链路的存在就是"索引可丢"的证明，也是验证新 TruthStore 实现正确性的手段：换一个实现，rebuild 出的索引应逐字节一致。

---

## 六、扩展性：四个扩展场景，各自动哪里

| 扩展场景 | 新写 | 改 | **不动** |
|---|---|---|---|
| 真值换实现（md→Notion/DB，用户扩展计划） | `NotionTruthStore`（实现端口方法） | composition 1 行 | 全部服务、全部入口、domain |
| 索引换引擎（→Neo4j） | `Neo4jIndexStore` | composition 1 行分支 | 同上 |
| 换真语义模型 | `SentenceTransformerEmbedder` | config + composition 1 行 | 同上；`semantic_fallback` 切 `always` 即恢复双路融合 |
| 加入口（MCP server / IDE 插件） | `interfaces/mcp.py` | 无 | 业务逻辑零复制——直接复用五个服务 |

### 6.1 扩展场景演练（四个真实问题的回答）

以"对系统不熟悉的用户"视角的四个问题验证扩展轴。共同答案：**每个"可能会变"都在设计时被识别为变化轴，各有一道专门的墙。**

**演练 1 · 新增接入入口（MCP / IDE 插件 / 新命令）**

答案：interfaces/ 加一个文件。以 MCP server 为例：

```
新增  interfaces/mcp.py          # 唯一的实质性工作：协议适配
改    pyproject.toml             # +1 行入口点（如果是独立命令）
不动  application/ domain/ infrastructure/   ← 零改动
```

```python
from micro_mem.composition import build_components

def main():
    svc = build_components()           # 拿到装配好的五个服务
    server = McpServer("micro_mem")
    server.tool("search", svc.search.search)      # 直接挂服务方法
    server.tool("get", svc.search.get)
    server.tool("create", svc.knowledge.create)
    server.run()
```

为什么能这么薄：业务逻辑在五个服务里完整且无入口耦合（这正是把 `cmd_confirm` 编排从 CLI 收进 DistillService 的意义）。入口只负责"协议翻译"。

诚实边界：若新入口需要**现有用例之外的能力**（如"新知识推送订阅"），那不是入口问题，是用例问题——见演练 4。

**演练 2 · 真值保存方式变了（md → Notion / DB / Git）**

改动收敛为"一个新类 + 一行装配"：

```
新增  infrastructure/notion_truth.py   # class NotionTruthStore(TruthStore)
改    composition.py                   # 1 行：truth = NotionTruthStore(...)
不动  五个服务、cli、http、domain       ← 零改动
```

真正的工作量不在"写一个类"，而在**新介质的语义映射**。TruthStore 端口方法背后各有一个设计决策，新实现必须回答：

| 端口方法 | Markdown 实现的答案 | 换成 Notion 要回答的 |
|---|---|---|
| `save_knowledge()` 返回 id | 扫目录取最大序号 | id 从哪来？Notion page id 还是自建序号？ |
| `save_anchor()` | 原文直存，一字不改 | 分块存储还"保真"吗？富文本转换会丢信息吗？ |
| `get_distill_cursor()` | 锚点 frontmatter | 游标存 Notion 属性还是别处？ |
| `list_knowledge()` | 扫目录 | API 分页拉全量（性能/限流） |

端口把这些决策与系统其余部分隔开——服务层只问"给我 id 为 X 的知识"，不关心答案来自文件还是 API。

存量数据迁移是任何架构都逃不掉的成本，端口化让它变得机械（两实例对拷）：

```python
for k in old_truth.list_knowledge():
    new_truth.save_knowledge(k)
for a in old_truth.list_anchors():
    new_truth.save_anchor(a)
```

**演练 3 · 索引存储换了（SQLite → Neo4j / ES）**

```
新增  infrastructure/neo4j_index.py    # class Neo4jIndexStore(IndexStore)，~15 个方法
改    config.yaml                      # index_store: neo4j
改    composition.py                   # 1 个 if 分支
不动  五个服务、cli、http、domain、truth ← 零改动
```

量级估计：参照 `SqliteIndexStore` 约 388 行，新实现约 300-400 行——四个扩展里最贵的一个，但全部成本压在一个新文件里。

注意：端口契约保证**输入输出类型一致**，不保证**召回结果逐条一致**——FTS5 与 Neo4j 全文索引的召回排序天然有差异。语义上允许（索引本就是可换的检索投影），对齐手段是**端口级契约测试**：同一套行为用例参数化跑在所有实现上，管"行为契约不破"，不管"召回列表完全相同"。

**演练 4 · 知识操作增加（新用例）**

分四档，变化越靠近领域核心越贵——这是软件的根本规律，架构让每档成本"看得见、收得住"：

| 档位 | 例子 | 动哪里 | 成本 |
|---|---|---|---|
| A · 现有操作的变体 | 批量删除、按条件导出 | 服务加 1 个方法 + CLI 加 1 个命令函数 | 小时级 |
| B · 横切增强 | 写操作加审计日志 | 服务层包装饰，或 composition 注入 | 小时级，结构不动 |
| C · 新操作面 | 订阅通知、版本历史 | 新增 1 个 service 文件；需新存储能力时端口加方法（ABC 会**强制**所有实现补齐，mypy 编译期发现，不会漏） | 天级 |
| D · 领域概念变化 | 知识类型加第 5 类、新增实体 | domain/models.py 加枚举值/dataclass → mypy 与测试自动指出所有受影响点 | 领域变化本来就贵，架构给的是"影响面全可见"而非"免费" |

C 档要点：端口加方法是**安全扩展**——ABC 抽象方法让全部实现被 mypy 强制要求实现，不存在"改了接口忘了改某个实现"的暗坑。

**四条扩展轴总结：**

```
变化轴              挡风墙                  新增成本          既有代码改动
─────────────────────────────────────────────────────────────────
新入口        →     interfaces 薄壳        1 个适配文件        0
真值换介质     →     TruthStore 端口       1 个实现类         1 行装配
索引换引擎     →     IndexStore 端口       1 个实现类         1 行装配 + 契约测试
新用例        →     application 服务边界   1 个服务/方法      0 ~ 端口加方法（受控）
```

为什么能这样：**变化被端口挡住了。** 易变的（存储介质、检索引擎、向量模型、交互形态）全在端口外侧；稳定的（领域概念、用例编排）在端口内侧。

测试随之分层：

```
domain 测试      → 纯函数，快
application 测试 → 注入 InMemoryTruthStore/InMemoryIndexStore，不碰盘不起库（毫秒级）
infrastructure   → tmp_path 真文件真 SQLite（集成测试，少量但真实）
e2e             → composition 装配后跑冒烟
```

### 一句话总结

旧结构的问题是"**原则在文档里，结构在碰运气**"；本方案把三条第一原则变成三个物理实体——「检索归系统」= IndexStore 端口，「锚归原文」= TruthStore 端口，「判断归模型」= 入口层薄到没有判断可写。**架构不再靠纪律维持，而靠结构保证。**

---

## 七、迁移计划（分支重建，不搞大爆炸）

**真值兼容是红线**：frontmatter 格式一个字节不变——现有全部知识与锚点必须无损。验收方式：新架构对真实 data/ 执行 rebuild，与旧索引逐节点比对一致。

| 阶段 | 内容 | 验收门禁 |
|---|---|---|
| **0 止血** | 仓库卫生（git rm --cached candidates.json/.playwright-mcp + gitignore 补全 + filter-repo 清历史）；B2 旧结构热修（FTS upsert）；CI 加 ruff/mypy 门 | 旧测试全绿 + ruff/mypy 过 |
| **1 骨架** | domain/models（dataclass 化）+ application/ports + composition 骨架 + InMemory 端口实现 | 新代码 mypy 过；旧代码不动 |
| **2 基础设施** | markdown_truth / sqlite_index / claude_jsonl / config 重写，各带集成测试 | tmp_path 集成测试全绿 |
| **3 应用服务** | 五个 service + 内存端口单测 | 服务层单测全绿 |
| **4 入口切换** | cli.py 单命令树 + http 工厂化 + 包装器变薄 + 旧入口点删除 | e2e：建库→载示例→检索→蒸馏全链路 |
| **5 拆除与文档** | 删旧 api/store/common/cli 及过时测试；真实 data/ 兼容性验收；README 重写（开源向）；doc 更新 | 全部测试绿 + 真实数据无损 |

每阶段独立验收，测试全绿才进下一阶段。阶段 0 先做，避免重构期间继续制造垃圾。

---

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| 大爆炸迁移失控 | 分支开发 + 阶段门禁（旧测试全绿才合并）；新架构并行存在，逐层迁移 |
| 真实数据（20+ 知识/锚点）迁移损坏 | frontmatter 格式不变是红线；阶段 5 用真实 data/ 做 rebuild 逐节点比对验收 |
| filter-repo 重写历史 | 本地操作、开源推送前执行、执行前备份分支 |
| InMemory 端口实现与真实实现行为漂移 | 端口级契约测试（同一测试套件跑两种实现） |

---

## 九、新旧概念映射表

| 旧（第一轮结构） | 新（本方案） | 说明 |
|---|---|---|
| `api/writer.py` MemoryWriter | `KnowledgeService`（知识写）+ `DistillService`（锚点） | 上帝类按用例拆分 |
| `api/reader.py` MemoryReader | `SearchService` | body/sources 改从 truth 组装（D3） |
| `api/distiller.py` Distiller | `DistillService` | 收编 cmd_confirm 编排 |
| `api/importer.py` Importer | `ImportService`（编排）+ `claude_jsonl.py`（格式解析） | 死依赖 store 删除 |
| `api/attach.py` | `DistillService` 内部步骤 | 待专题重设计 |
| `store/base.py` NetworkStore | `application/ports.py` IndexStore | rowid 收回实现内部 |
| `store/sqlite_store.py` | `infrastructure/sqlite_index.py` | + schema.sql 入包 |
| `common/md_parser.py` | `MarkdownTruthStore` 私有方法 | 序列化唯一收口 |
| `common/config.py` | `infrastructure/config.py` | 路径解析重写（D5） |
| `common/embedder.py` | `ports.Embedder` + `hash_embedder.py` | 不变，挪位置 |
| `domain/types.py` | `domain/models.py` | dataclass 化 |
| `cli/*.py` 五个入口 | `interfaces/cli.py` 单命令树 + `http.py` | sys.path hack 全删 |
| `container.py` | `composition.py` | 可注入 config 路径 |

---

## 十、明确不做（边界）

- 不引 DI 框架、不引 pydantic（dataclass 足够）
- 不写第二真值/索引的生产实现（InMemory 仅测试基建）
- 不接真语义模型（Embedder 端口已备好，路线图项）
- 不动 doc/设计实现过程/ 五篇 HTML（历史档案）
- 不做 Neo4j / 可视化写接口接页面（路线图已有）

---

## 十一、遗留专题（另立讨论）

**知识节点如何正确建立关联关系（自动挂靠的详细设计）**——用户指定另立专题。现状：confirm 后用"标题字符重叠 ≥0.3 + 对方有 parent 入边"的启发式自动挂靠（attach.py）。专题需回答：挂靠的判定权在系统还是模型（第一原则下应为模型建议、系统执行）？主题节点的生命周期？跨领域 link 的推荐机制？专题结论落地前，保留现状启发式，挪入 DistillService 并标注 TODO。

---

## 附：拍板记录汇总

- B1 不修（delete 语义契约化，D7）
- B2 修（阶段 0 热修 + 新架构 upsert 语义根除，D3/D4）
- 完全重构路线（拍板 1）
- TruthStore 抽象端口（拍板 2）
- 旧入口点删除（拍板 3）
- 仓库卫生 + git 历史清理（拍板 4）
- 骨架校验维持文档约定（拍板 5）
- 语义兜底配置化（拍板 6，D8）
- README 后置重写（拍板 7）
- 自动挂靠保留现状、另立专题（拍板 8，第十一章）
