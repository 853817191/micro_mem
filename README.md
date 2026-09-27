# micro_mem

> 把 AI 对话蒸馏成**可检索、可溯源**的结构化知识库。
>
> 检索归系统，判断归模型，锚归原文。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

---

## English Abstract

**micro_mem** is a distillation-first long-term memory system for AI conversations.
It turns raw conversation logs into a structured, searchable and traceable knowledge
base: *anchors* preserve the original dialogue verbatim, *knowledge* entries are
curated Markdown atoms, and a pluggable *network* layer links them into a graph.

The design principle is a strict division of labour: **retrieval is the system's job,
judgement is the model's job, and every claim points back to a verbatim anchor.**
Truth lives in plain Markdown with frontmatter — the SQLite index is fully rebuildable
from it at any time.

---

## 为什么做这个

AI 对话里沉淀了大量可复用经验，但它们**散落在时间线里**：能翻到，不能复用；能搜索，不能判断。

常见做法有两种，都不太够用：

- **纯向量库**：召回一堆相似片段，但片段之间没有结构，也无法回答"这条结论是从哪次对话的哪一轮来的"。
- **纯 Markdown 笔记**：可读、可版本管理，但积累到几百条后检索就失效了。

micro_mem 的思路是把两者拆开——**真值用 Markdown（人和 AI 都能直接读写），检索用索引（可随时重建）**，中间加一层"锚点"做溯源。

## 核心原则

| 原则 | 含义 |
|---|---|
| **检索归系统** | 字面 + 语义双路召回 + RRF 融合，不要求模型自己想起来 |
| **判断归模型** | 系统只给候选，每条附一段 `summary`，模型据此判断"是不是这件事"，不读全文 |
| **锚归原文** | 每条知识都能回溯到保真的对话原文（锚点），杜绝模型凭印象编造 |

## 概念模型

```
锚点 anchor ──蒸馏──> 知识 knowledge ──关联──> 网 network
（对话原文保真）      （Markdown 真值）         （parent / link / trace）
```

- **锚点（anchor）**：一次会话的保真副本，含完整过程与工具调用，落在 `data/anchors/s-*.md`。
- **知识（knowledge）**：从对话中提炼出的知识原子，落在 `data/knowledge/k-*.md`。
  两个维度刻画：
  - `type` × `scope` = **3 × 3**

    | | `universal` | `domain` | `personal` |
    |---|---|---|---|
    | `event` 事件 | 普适事件 | 领域事件 | 个人事件 |
    | `method` 方法 | 普适方法 | 领域方法 | 个人方法 |
    | `fact` 事实 | 普适事实 | 领域事实 | 个人事实 |

  - `status` 成熟度：`draft` → `evolving` → `settled` → `deprecated`
- **网（network）**：知识之间的关系。边类型 `parent`（挂靠主题）/ `link`（关联）/ `trace`（溯源）；
  另有 `external_refs` 把知识挂到外部真实世界（`idev` / `mr` / `uat` / `url`）。

四个操作面：**记忆**（建锚点）、**蒸馏**（对话→知识）、**收录**（手工/存量导入）、**关联**（建边）。

## 架构

```
                    ┌─────────── 真值（可读、可版本管理、不可由索引反推）───────────┐
  对话 jsonl ──> 锚点 md ──AI 提炼──> 知识 md
                    └───────────────────────────────────────────────────────────┘
                                         │ 重建（幂等）
                                         ▼
  检索 ◀── 四接口 ◀── NetworkStore 引擎 ──> SQLite
                        （可插拔）            ├─ nodes        节点（无 body，只存可重建副本）
                                             ├─ nodes_fts    FTS5 trigram 全文索引（中文子串匹配）
                                             ├─ edges        关系
                                             ├─ external_refs 外部锚点
                                             └─ nodes_vec    向量索引（sqlite-vec，第二批）
```

**分层边界是这套设计的核心**：索引层永远可以从 Markdown 真值全量重建，所以索引可以随便删、随便换实现（SQLite / Neo4j / 内存），真值不受影响。

- 四接口：`MemoryWriter`（写）/ `MemoryReader`（读）/ `Distiller`（蒸馏）/ `Importer`（导入）
- 引擎接口：`NetworkStore`，当前实现 `SqliteNetworkStore`
- 向量化接口：`Embedder`，可插拔

### 检索

`MemoryReader.search()` 做**双路召回 + RRF 融合**：

1. **字面路** — FTS5（`trigram` 分词器，中文子串可命中，搜"需求"能命中"需求单"）
2. **语义路** — 向量近邻
3. **RRF** — 两路排名融合

`search_multi()` 进一步支持多关键词：按空格拆词、逐词双路召回、跨词融合，**多词同时命中的知识自动前置**，且不要求这些词连续出现。

### 关于向量：当前是占位实现

`config.yaml` 里写的是 `embedding_model: bge-m3` / `embedding_dim: 1024`，但**当前代码装配的是 `HashEmbedder`**——字符 bigram 的 md5 哈希向量，无外部依赖、确定性，作用是**把向量管道跑通**。

它基于字符重合而非语义，**不等于真的语义检索**。换成真实语义模型（`sentence-transformers` / 远端 API）是路线图里的事，接口（`Embedder`）已经留好了，替换不需要动上层。

同理，`nodes_vec` 表在 `schema.sql` 中是**注释状态**，需要 `sqlite-vec` 扩展，第二批启用。

## 快速开始

> 需要 **Python ≥ 3.10**——源码用 `X | None` 作运行时注解（PEP 604），3.9 会在 import 阶段直接报错。

```bash
# 1. 依赖（目前只有一个）
python -m pip install -r requirements.txt

# 2. 建目录 + 建库（幂等，可重复执行）
python build_db.py

# 3. 收录一条知识
python src/main.py create \
  --type fact --scope domain \
  --title "需求单状态机" \
  --summary "需求单在审批中、已通过、已驳回之间流转，驳回可重新提交。" \
  --body "详细说明……"

# 4. 检索
python src/main.py search "审批"                 # 单关键词
python src/main.py search "需求 审批 状态" --multi   # 多关键词融合

# 5. 取详情 / 图遍历
python src/main.py get k-0001
python src/main.py traverse k-0001 --depth 2

# 6. 从 md 真值全量重建索引（索引坏了就跑这个）
python src/main.py rebuild

# 7. 可视化
python src/server.py                            # 打开 http://localhost:8000/
```

> 命令请在**项目根目录**执行。数据目录 `data_dir` 在 `config.yaml` 中配置，相对路径按**项目根**解析（不依赖当前工作目录），所以数据永远落在同一个地方。

### 命令一览

| 命令 | 作用 |
|---|---|
| `python src/main.py create` | 收录一条知识（来源标记为 `user_declared`） |
| `python src/main.py search [--multi] [--limit N]` | 双路召回检索 |
| `python src/main.py get <id>` | 取单条知识全文 |
| `python src/main.py traverse <id> [--depth N]` | 沿边做 N 跳图遍历 |
| `python src/main.py rebuild` | 清空索引，从 `data/knowledge/*.md` 全量重建 |
| `python src/main.py import --dir <目录> [--mode backfill\|incremental]` | 存量导入历史会话 |
| `python src/server.py [--port 8000]` | 起可视化服务 |
| `python tests/run_tests.py` | 跑测试（临时目录隔离，不碰真实数据） |

## 可视化

`python src/server.py` 后访问 <http://localhost:8000/>：知识图谱（ECharts 力导向图）、节点详情、检索、锚点列表。

API：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stats` | 总览统计 |
| GET | `/api/graph` | 全量图数据（nodes + edges） |
| GET | `/api/node/{id}` | 单条知识详情 |
| GET | `/api/search?q=…` | 检索 |
| GET | `/api/traverse/{id}?depth=2` | 图遍历 |
| GET | `/api/anchors` | 锚点列表 |
| POST | `/api/link/add` | 建边（幂等） |
| POST | `/api/link/delete` | 删边 |
| POST | `/api/node/delete` | 删节点 |

## 接入 Claude Code

micro_mem 的定位是**给 AI 做长期记忆的底座**，它只提供机制（锚点、真值、索引、检索），**蒸馏的判断由模型做**。典型闭环：

```bash
# ① 把一次会话落成保真锚点（缺省取 ~/.claude/projects 下最新会话）
python distill_this_session.py anchor

# ② 增量蒸馏准备：重同步锚点，输出自上次蒸馏以来的新增轮次
python distill_this_session.py distill

# ③ 模型读增量轮次 → 产出候选 JSON（这一步是 AI 干的，工具不管）
# ④ 落库：幂等去重 + 溯源校验（sources.ref 必须指向真实锚点）+ 回写蒸馏游标 + 自动挂靠
python distill_this_session.py confirm candidates.json
```

之后在需要时用 `search` 检索并把命中的 `summary` 注入上下文，让模型判断要不要 `get` 全文。

`data/temp/distill_state.json` 记录蒸馏游标，所以**增量蒸馏不会重复提炼已经沉淀过的轮次**。

> 同一个 `anchor` / `distill` / `confirm` 也可以直接用 `python src/main.py import` 批量做存量导入。

### 装成 Claude Code skill

上面这套流程有一份现成的 skill 定义，在 `skills/memory-knowledge/`：

```bash
# 用户级（所有项目可用）
cp -r skills/memory-knowledge ~/.claude/skills/

# 或项目级（只在该项目可用）
mkdir -p <项目>/.claude/skills && cp -r skills/memory-knowledge <项目>/.claude/skills/
```

它把「从用户问题提取核心词 → `search` → 判断要不要 `get` → 蒸馏时先展示候选再落库」这套**判断规则**写成了模型可执行的流程，也就是核心原则里"判断归模型"的那一半。

把 `bin/` 加入 PATH 后，可以直接用 `mem` 前缀调用所有命令：

```bash
# Windows（PowerShell，一次性）
setx PATH "$env:PATH;<仓库路径>\bin"

# macOS / Linux
export PATH="<仓库路径>/bin:$PATH"
mem search "关键词"
mem get k-0001
```

`bin/mem.cmd`（Windows）与 `bin/mem.sh`（macOS / Linux）都用**脚本自身位置**推导仓库根，所以仓库可以放在任何位置；需要换位置时用 `MEMORY_HOME` 环境变量覆盖。skill 里不含任何个人路径或私有配置。

## 目录结构

```
micro_mem/
├── config.yaml                 配置
├── requirements.txt            依赖
├── schema.sql                  SQLite 表结构
├── build_db.py                 建目录 + 建库
├── import_one.py               导入单个会话 → 锚点
├── distill_this_session.py     蒸馏当前会话（anchor / distill / confirm）
├── TEST_CASES.md               测试用例清单
├── src/
│   ├── main.py                 统一命令入口
│   ├── server.py               可视化 HTTP 服务
│   ├── config.py               配置加载
│   ├── types.py                领域类型
│   ├── md_parser.py            Markdown + frontmatter 解析
│   ├── embedder.py             向量化接口 + HashEmbedder
│   ├── api/                    四接口：writer / reader / distiller / importer
│   └── store/                  引擎：base（接口）+ sqlite_store（实现）
├── tests/run_tests.py          测试执行器
├── skills/
│   └── memory-knowledge/       Claude Code skill 定义（拷到 ~/.claude/skills/ 使用）
├── bin/
│   ├── mem.cmd                 命令包装器（Windows）
│   └── mem.sh                  命令包装器（macOS / Linux）
├── examples/                   合成示例数据（虚构，可随意改删）
│   ├── knowledge/              5 条示例知识
│   ├── anchors/                1 个示例锚点
│   └── README.md               示例说明
├── web/
│   ├── index.html              可视化页面
│   └── vendor/echarts.min.js   Apache ECharts
├── .github/workflows/ci.yml    CI：3.10~3.13 建库 → 载示例 → 校验检索/遍历/测试
├── LICENSE                     Apache License 2.0
└── NOTICE                      第三方组件归属
```

## 数据与隐私

> **`data/` 目录是使用者的私有数据，已被 `.gitignore` 排除，永不应提交。**

`data/` 下是你自己的锚点、知识、蒸馏产物与索引数据库，可能包含隐私、内部信息与凭据。**真值只有一份、在本地**，索引可以随时从真值重建，所以索引丢失没有任何损失。

## 已知限制与路线图

- **向量检索未启用真实语义模型**，当前 `HashEmbedder` 仅用于跑通管道（见上文「关于向量」）。
- **引擎只有 SQLite 一种实现**；`NetworkStore` 接口已抽出，Neo4j / 内存实现未做。
- **蒸馏的判断依赖外部模型**，本仓库不含任何 LLM 调用。
- **可视化写接口**（建边/删边/删节点）已实现但页面尚未接入，目前供脚本与维护调用。

## 测试

```bash
python tests/run_tests.py
```

用例按 `TEST_CASES.md` 编号组织，**全部使用临时 data 目录与临时 db**，不会污染真实知识库。

## 许可

[Apache License 2.0](LICENSE)。第三方组件归属见 [NOTICE](NOTICE)。
