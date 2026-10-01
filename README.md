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
                                         │ 重建（RebuildService，幂等）
                                         ▼
  检索 ◀── SearchService ◀── IndexStore 端口 ──> SQLite（当前实现）
                        （真值投影，随时可弃）     ├─ nodes        节点（无 body，只存可重建副本）
                                                  ├─ nodes_fts    FTS5 trigram 全文索引（中文子串匹配）
                                                  ├─ edges        关系
                                                  ├─ external_refs 外部锚点
                                                  └─ nodes_vec    向量索引（sqlite-vec）
```

**分层边界是这套设计的核心**：索引只是真值的投影，随时可以从 Markdown 真值全量重建，
所以索引可以随便删、随便换实现（SQLite / 内存 / 未来的 Neo4j），真值不受影响。

六边形架构，依赖规则 `interfaces → application → domain ← infrastructure`：

- **三个端口**（`application/ports.py` 的 ABC）：`TruthStore`（真值存取，id/时间戳归它管）、
  `IndexStore`（索引投影，全量替换语义）、`Embedder`（向量化）
- **五个应用服务**（编排全在这里，CLI/HTTP 只是薄壳）：
  `KnowledgeService`（写路径双源协调）/ `SearchService`（读路径组装 + RRF 融合）/
  `DistillService`（蒸馏三段编排）/ `ImportService`（存量导入）/ `RebuildService`（索引重建）
- **基础设施实现**：`MarkdownTruthStore` + `InMemoryTruthStore`；
  `SqliteIndexStore` + `InMemoryIndexStore`；`HashEmbedder`
- **装配根**（`composition.py`）：全项目唯一同时认识端口与实现的地方，
  `assemble()` 生产装配（按 `config.yaml` 选实现）、`assemble_inmemory()` 测试装配
- **写路径铁律（先 truth 后 index）只在 KnowledgeService 一处协调**；
  **读路径（index 出候选、truth 出全文）只在 SearchService 一处组装**

### 检索

`SearchService.search()` 做**双路召回 + RRF 融合**：

1. **字面路** — FTS5（`trigram` 分词器，中文子串可命中，搜"需求"能命中"需求单"）
2. **语义路** — 向量近邻（兜底策略可配：`on_zero_hit` 零命中才兜底 / `always` 总双路 / `off` 仅字面）
3. **RRF** — 两路排名融合（k=60）

`search_multi()` 进一步支持多关键词：按空格拆词、逐词召回、跨词融合，**多词同时命中的知识自动前置**。

### 关于向量：当前是占位实现

当前装配的是 `HashEmbedder`——字符 bigram 的 md5 哈希向量，无外部依赖、确定性，作用是**把向量管道跑通**。

它基于字符重合而非语义，**不等于真的语义检索**。换成真实语义模型（`sentence-transformers` / 远端 API）只需新实现一个 `Embedder` 端口，在装配根换一行。

## 快速开始

> 需要 **Python ≥ 3.10**——源码用 `X | None` 作运行时注解（PEP 604），3.9 会在 import 阶段直接报错。

```bash
# 1. 安装（editable：装依赖 + 注册 mem 入口点）
python -m pip install -e .

# 2. 收录一条知识（首次运行自动建目录 + 建库，幂等）
mem create \
  --type fact --scope domain \
  --title "需求单状态机" \
  --summary "需求单在审批中、已通过、已驳回之间流转，驳回可重新提交。" \
  --body "详细说明……"

# 3. 检索
mem search "审批"                    # 单关键词
mem search "需求 审批 状态" --multi  # 多关键词融合

# 4. 取详情 / 图遍历
mem get k-0001
mem traverse k-0001 --depth 2

# 5. 从 md 真值全量重建索引（索引坏了就跑这个）
mem rebuild

# 6. 可视化
mem serve                            # 打开 http://localhost:8000/
```

> 配置定位优先级：显式传入 > `MEMORY_HOME` 环境变量 > `./config.yaml` > 包内默认值；
> 相对 `data_dir` 按 **config.yaml 所在目录**解析（不依赖当前工作目录），数据永远落在同一个地方。

### 命令一览

| 命令 | 作用 |
|---|---|
| `mem search [--multi] [--limit N]` | 双路召回检索（RRF 融合） |
| `mem get <id>` | 取单条知识全文（真值组装） |
| `mem create --type --scope --title …` | 收录一条知识（来源 `user_declared`） |
| `mem deprecate <id>` | 废弃（留痕不删） |
| `mem delete <id>` | 移出索引工作集（真值保留，`rebuild` 可恢复） |
| `mem traverse <id> [--depth N]` | 沿边做 N 跳图遍历 |
| `mem rebuild` | 清空索引，从 `data/knowledge/*.md` 全量重建 |
| `mem import --dir <目录> [--mode]` | 存量导入历史会话 → 锚点 |
| `mem anchor [jsonl] [标题]` | 会话 jsonl → 保真锚点 |
| `mem distill [anchor_id]` | 增量蒸馏准备（输出增量轮次 + 上下文） |
| `mem confirm <candidates.json>` | 候选落库（幂等 + 溯源校验 + 游标回写） |
| `mem serve [--port 8000]` | 起可视化服务 |
| `pytest` | 跑测试（临时目录隔离，不碰真实数据） |

## 可视化

`mem serve` 后访问 <http://localhost:8000/>：知识图谱（ECharts 力导向图）、节点详情、检索、锚点列表。

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
mem anchor

# ② 增量蒸馏准备：重同步锚点，输出自上次蒸馏以来的新增轮次
mem distill

# ③ 模型读增量轮次 → 产出候选 JSON（这一步是 AI 干的，工具不管）
# ④ 落库：幂等去重 + 溯源校验（sources.ref 必须指向真实锚点）+ 回写蒸馏游标 + 自动挂靠
mem confirm candidates.json
```

之后在需要时用 `search` 检索并把命中的 `summary` 注入上下文，让模型判断要不要 `get` 全文。

`data/temp/distill_state.json` 记录蒸馏游标，所以**增量蒸馏不会重复提炼已经沉淀过的轮次**。

> 同一个 `anchor` / `distill` / `confirm` 也可以直接用 `mem import` 批量做存量导入。

### 装成 Claude Code skill

上面这套流程有一份现成的 skill 定义，在 `clients/skill/memory-knowledge/`：

```bash
# 用户级（所有项目可用）
cp -r clients/skill/memory-knowledge ~/.claude/skills/

# 或项目级（只在该项目可用）
mkdir -p <项目>/.claude/skills && cp -r clients/skill/memory-knowledge <项目>/.claude/skills/
```

它把「从用户问题提取核心词 → `search` → 判断要不要 `get` → 蒸馏时先展示候选再落库」这套**判断规则**写成了模型可执行的流程，也就是核心原则里"判断归模型"的那一半。

把 `clients/cli/` 加入 PATH 后，可以直接用 `mem` 前缀调用所有命令（也可 `pip install -e .` 用标准入口点，见 `clients/README.md`）：

```bash
# Windows（PowerShell，一次性）
setx PATH "$env:PATH;<仓库路径>\clients\cli"

# macOS / Linux
export PATH="<仓库路径>/clients/cli:$PATH"
mem search "关键词"
mem get k-0001
```

`clients/cli/mem.cmd`（Windows）与 `clients/cli/mem.sh`（macOS / Linux）都用**脚本自身位置**推导仓库根，所以仓库可以放在任何位置；需要换位置时用 `MEMORY_HOME` 环境变量覆盖。skill 里不含任何个人路径或私有配置。

## 目录结构

```
micro_mem/
├── pyproject.toml              打包 + 入口点 + 依赖 + ruff/mypy/pytest 配置
├── config.yaml                 配置（可选；缺省用包内默认值）
├── TEST_CASES.md               测试覆盖地图
├── src/
│   ├── README.md               分层与依赖规则说明
│   └── micro_mem/              包根（src-layout，导入名 micro_mem）
│       ├── domain/             领域模型：纯 dataclass + 枚举，零依赖
│       ├── application/        应用层：ports.py（三个端口 ABC）+ 五个服务
│       ├── infrastructure/     基础设施：Markdown/SQLite/内存实现 + 配置 + 反腐解析
│       ├── interfaces/         入口层：cli.py（单 mem 命令树）+ http.py（可视化服务）
│       │   └── web/            可视化页面（随包分发，importlib.resources 读取）
│       ├── composition.py      装配根：assemble() 生产 / assemble_inmemory() 测试
│       └── __main__.py         python -m micro_mem 入口
├── clients/                    入口层（给人和 AI 用）
│   ├── README.md               入口层说明
│   ├── cli/                    命令包装器
│   │   ├── mem.cmd             （Windows）
│   │   └── mem.sh              （macOS / Linux）
│   └── skill/
│       └── memory-knowledge/   Claude Code skill 定义（拷到 ~/.claude/skills/ 使用）
├── tests/                      pytest 用例（conftest.py + test_*.py）
├── examples/                   合成示例数据（虚构，可随意改删）
│   ├── knowledge/              5 条示例知识
│   ├── anchors/                1 个示例锚点
│   └── README.md               示例说明
├── .github/workflows/ci.yml    CI：3.10~3.13 静态检查 → 建库重建 → 校验检索/遍历 → 测试
├── LICENSE                     Apache License 2.0
└── NOTICE                      第三方组件归属
```

## 数据与隐私

> **`data/` 目录是使用者的私有数据，已被 `.gitignore` 排除，永不应提交。**

`data/` 下是你自己的锚点、知识、蒸馏产物与索引数据库，可能包含隐私、内部信息与凭据。**真值只有一份、在本地**，索引可以随时从真值重建，所以索引丢失没有任何损失。

## 已知限制与路线图

- **向量检索未启用真实语义模型**，当前 `HashEmbedder` 仅用于跑通管道（见上文「关于向量」）。
- **真值只有 Markdown 一种实现**（内存版仅测试用）；索引有 SQLite / 内存两种实现，
  未来可接 Neo4j——新实现只需满足 `IndexStore` 端口，并有契约测试（`test_index_contract.py`）防漂移。
- **蒸馏的判断依赖外部模型**，本仓库不含任何 LLM 调用。
- **自动挂靠是现状启发式**（标题字符重叠 ≥ 0.3 + 挂聚合主题），判定权归属与跨领域推荐机制待专题重设计。
- **可视化写接口**（建边/删边/删节点）已实现但页面尚未接入，目前供脚本与维护调用。

## 测试

```bash
pytest                 # 功能用例（性能用例默认排除）
pytest -m perf         # 单独跑性能用例
```

用例覆盖见 `TEST_CASES.md`（测试覆盖地图），**全部使用临时 data 目录与临时 db**，不会污染真实知识库。

## 许可

[Apache License 2.0](LICENSE)。第三方组件归属见 [NOTICE](NOTICE)。
