# micro_mem 工程化整改 · 设计文档（评审稿）

> 状态：**待 review** ｜ 日期：2026-09-28 ｜ 版本：v0.1
>
> 本文件是整改的**评审稿**。你的职责是 review 下面每一节，标注「同意 / 要改 / 不同意」，定稿后我们再按阶段动手。

---

## 一、背景与目标

**现状**：micro_mem 用 AI 证明了可行性，功能已跑通（42/43 测试通过），但**项目结构、代码设计、测试、打包**都是"能跑就行"的级别，没有按软件工程标准组织。

**目标**：工程化规范开发，四个维度一起整改：

| 维度 | 含义 |
|---|---|
| 结构重构 | 目录、入口、包边界清晰，根目录零裸 py |
| 代码设计规范 | 分层边界干净、无死代码、封装不破、序列化对称 |
| 测试工程化 | pytest 化、消除跨层依赖、性能测试分离 |
| 打包与工具链 | pyproject.toml、标准入口点、依赖声明、CI 更新 |

**推进方式**：先出评审稿（本文档）→ 你 review → 分阶段动手，每阶段独立验收。

---

## 二、现状基线

### 2.1 现有项目结构

```
micro_mem/
├── config.yaml / requirements.txt / schema.sql
├── build_db.py                  ⚠️ 根目录裸 py 入口
├── import_one.py                ⚠️ 根目录裸 py 入口
├── distill_this_session.py      ⚠️ 根目录裸 py 入口（含业务逻辑）
├── TEST_CASES.md
├── src/
│   ├── main.py                  统一命令入口
│   ├── server.py                可视化 HTTP（装配硬编码引擎）
│   ├── config.py / types.py / embedder.py / md_parser.py   ⚠️ 基础件裸放
│   ├── api/                     writer / reader / distiller / importer
│   └── store/                   base（接口）+ sqlite_store（实现）
├── tests/run_tests.py           手写测试执行器（469 行）
├── skills/memory-knowledge/     AI 入口（规则）
├── bin/mem.cmd · mem.sh         人入口（shell 包装器）
├── examples/                    合成示例数据（CI 回归样本）
├── web/  .github/workflows/ci.yml  doc/
```

### 2.2 代码调用链路（入口 → 接口 → 引擎）

```
入口层（5 个 python 入口，散落两处 + 2 个 shell 包装器）
  bin/mem.cmd · mem.sh ──分流──► distill_this_session.py（anchor/distill/confirm）
                                src/main.py（search/get/create/traverse/rebuild/import）
  src/server.py（长驻，不走 bin）  build_db.py（不走 bin）  import_one.py（不走 bin）

接口层（四接口，业务编排）
  MemoryWriter(写) · MemoryReader(读) · Distiller(蒸馏) · Importer(导入)

引擎层（端口-适配器）
  NetworkStore(接口) ──► SqliteNetworkStore(实现)     Embedder(接口) ──► HashEmbedder

基础层（无依赖）
  Config · types.py(领域模型) · md_parser.py · embedder.py
```

**蒸馏主链路（用户告知 → 沉淀入库）**：

```
mem anchor <jsonl>
  └ distill_this_session.py:cmd_anchor()
       ├ setup() → Config → SqliteNetworkStore → HashEmbedder → 四接口
       ├ Importer._parse_jsonl → _parse_turns → _render_message    # jsonl → 可读对话
       └ writer.save_anchor → _generate_anchor_id → _write_anchor_file

mem distill <anchor_id>
  └ cmd_distill()
       ├ importer._parse_jsonl + _parse_turns
       ├ writer.resync_anchor / get_anchor_distilled_until         # 算增量轮次
       └ 写 data/temp/distill_state.json + 打印增量（给 AI）

（AI 读增量 → 产出 candidates.json）★ 模型干的，工具不参与

mem confirm <candidates.json>
  └ cmd_confirm()
       └ distiller.confirm_distill()
            ├ _find_existing        # 幂等去重
            ├ _validate_sources     # 溯源校验
            ├ _to_knowledge
            ├ writer.create_knowledge   ──► 见写入子链路
            └ _build_suggested_edges
       ├ writer.mark_distilled      # 回写蒸馏游标
       └ auto_attach_all → auto_attach → _is_topic_node（碰 store._conn）
```

**写入子链路（create_knowledge 内部）**：

```
writer.create_knowledge(k)
  ├ _validate → _assign_meta → _write_md_file（真值 md）
  ├ _sync_indexes → store.create_node → rowid → embedder.embed → store.save_vector
  ├ _build_edges → store.add_edge（parents→parent, links→link）
  └ _link_external_refs → store.add_external_ref
```

### 2.3 数据流向（真值线 + 索引线）

```
【真值线 · Markdown，可读、可版本管理】
  对话 jsonl ──Importer──► 锚点 md  data/anchors/s-*.md（保真不可变）
                              │  ★AI 提炼（模型判断）
                              ▼
                           知识 md  data/knowledge/k-*.md（真值）
  知识 md ──同步──► SQLite 索引（nodes / nodes_fts / nodes_vec / edges / external_refs）

【索引线 · SQLite，可随时从真值全量重建】
  检索 query ─► search_keyword(FTS+LIKE) ┐
               search_semantic(vec)      ├─► RRF 融合 ─► SearchHit(轻量) ─► 模型判断 ─► get() 下钻
                                          ┘
  rebuild：clear_all → parse_file(k-*.md) → create_knowledge(skip_md=True) → 索引重建
```

> 三个与一致性直接相关的事实（整改设计时据此判断）：
> 1. **body 存两处**：真值在 k-*.md 正文，索引副本在 nodes_fts.body；读时 body 走 FTS，sources 走 md（split-brain）。
> 2. **sources 只进真值不进索引**：五张表无 sources 列，读时必须按 file 回读 md 补全。
> 3. **rowid 是对齐枢纽**：nodes / nodes_fts / nodes_vec 靠同一 rowid 对齐，该 rowid 已从引擎漏到业务层。

---

## 三、问题诊断（按四维度，每条带位置）

### 3.1 结构问题（Structure）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| S1 | 入口脚本散落两处 | 根目录 build_db/import_one/distill_this_session；src/ main/server | 5 个入口边界不清 |
| S2 | 装配逻辑重复 4 处 | main.py:51 `build_components`、distill_this_session.py:34 `setup`、import_one.py 内联、server.py:47 模块级（硬编码 SqliteNetworkStore） | 换引擎/改装配要改 4 处 |
| S3 | 基础件与包混放 | src/ 根目录 config/types/embedder/md_parser | 无归类，领域模型与工具不分 |
| S4 | 业务逻辑住在 CLI 脚本 | distill_this_session.py:177-226 `auto_attach/_is_topic_node` | 不可复用、不可测 |
| S5 | rebuild 解析逻辑位置不当 | main.py:141 `_knowledge_from_meta` 被 tests 跨层 import（223/355 行） | 测试依赖 CLI 私有函数 |
| S6 | 建库绕过引擎 | build_db.py 直接 sqlite3，与 sqlite_store.py:57 `_ensure_schema` 重复 | 双份建库逻辑 |
| S7 | 入口命名技术化 | bin/（联想编译产物）+ skills/（生态黑话） | 未表达"给不同用户的入口" |

### 3.2 代码设计问题（Design）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| D1 | body 双源（split-brain） | reader.py:95-107，body 走 `get_fts_body`(103)、sources 走 `_read_sources`(105) | 同一对象字段来自两处，语义不统一 |
| D2 | rowid 泄漏到业务层 | writer.py:214-217 `create_node` 返回 rowid 再 `save_vector`；base.py:22 `NodeRecord.rowid` | 业务层感知 FTS/vec 对齐细节 |
| D3 | 死代码 | base.py:51 `count_nodes` 抽象方法从未被调用 | 契约虚设 |
| D4 | 序列化不对称 | types.py：Source/ExternalRef 有 `from_dict`，Knowledge/DistillCandidate 无 | 反序列化职责残缺 |
| D5 | 封装被破坏 | distill_this_session.py:221 直访 `store._conn.execute` | 绕过 NetworkStore 接口 |
| D6 | 更新语义绕 | writer.py:246 `get_fts_body` 后再传回 update_node 的 old_body 参数 | 参数职责绕 |

### 3.3 测试问题（Test）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| T1 | 手写 runner 自造 pytest 轮子 | tests/run_tests.py 通篇 record/report/性能统计 | 469 行里大量重复造轮 |
| T2 | 跨层依赖 | run_tests.py:223/355 `from src.main import _knowledge_from_meta` | 同 S5，D2 整改会断此 import |
| T3 | 缺 UTF-8 重配置 | run_tests.py 无 `sys.stdout.reconfigure`（main/distill 都有） | Windows GBK 下 `✓` 崩溃 |
| T4 | 性能用例混入必过门槛 | run_tests.py:332 TC-P8 rebuild 阈值 10s | CI 不稳（当前唯一失败 42/43） |
| T5 | TestConfig 手写 duck-type | run_tests.py:25-35 | 未用真实 Config + fixture |

### 3.4 打包/工具链问题（Packaging）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| P1 | 无 pyproject.toml | 无 | 无打包/入口点/lint 配置 |
| P2 | 依赖声明不全 | requirements.txt 仅 pyyaml | sqlite-vec(已装 0.1.9) 未声明 |
| P3 | Python 版本无声明 | 源码用 PEP 604 `X\|None`（需 3.10+） | 3.9 会在 import 时直接崩 |
| P4 | 无标准入口点 | 靠 bin/mem.cmd + `python src/main.py` 直跑 | 无 pip install 后统一入口 |

---

## 四、目标结构（整改后）

### 4.1 目标目录树

```
micro_mem/
├── pyproject.toml                  # 新增：打包 + 入口点 + ruff/mypy 配置
├── config.yaml / schema.sql / README.md
├── src/
│   └── micro_mem/                  # 包根（src-layout，可 pip install）
│       ├── __init__.py
│       ├── domain/                 # 领域模型（原 types.py）
│       │   └── types.py
│       ├── common/                 # 公共基础件（原 config/embedder/md_parser）
│       │   ├── config.py
│       │   ├── embedder.py
│       │   └── md_parser.py
│       ├── api/                    # 四接口
│       │   ├── writer.py / reader.py / distiller.py / importer.py
│       ├── store/                  # 引擎
│       │   ├── base.py / sqlite_store.py
│       ├── container.py            # 新增：单一装配点（收敛 4 处装配）
│       └── cli/                    # 所有命令入口
│           ├── main.py             # 原 src/main.py
│           ├── server.py           # 原 src/server.py
│           ├── build_db.py         # 原根目录 build_db.py
│           ├── import_one.py       # 原根目录 import_one.py
│           └── distill.py          # 原根目录 distill_this_session.py（业务逻辑抽出）
├── clients/                        # 给不同用户的入口（原 bin/ + skills/）
│   ├── README.md                   # 新增：大白话说明两个入口
│   ├── cli/                        # 原 bin/
│   │   ├── mem.cmd / mem.sh
│   └── skill/                      # 原 skills/
│       └── memory-knowledge/SKILL.md
├── tests/                          # pytest 化
├── examples/  web/  .github/workflows/ci.yml  doc/
```

### 4.2 已对齐的命名与决策（你已拍板）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 四维度范围 | 结构重构 + 代码设计规范 + 测试工程化 + 打包工具链 |
| 2 | 推进方式 | 先评审稿，分阶段动手 |
| 3 | D1 body 读法 | body 不读文件，保持从引擎/FTS 读，文档标注为索引副本 |
| 4 | D2 入口迁移 | main/server 迁 `src/micro_mem/cli/` |
| 5 | 根目录零裸 py | build_db/import_one/distill 全收 `cli/` |
| 6 | 一键切换引擎 | 本次不做 |
| 7 | 基础件归类 | types → `domain/`；config/embedder/md_parser → `common/` |
| 8 | 入口层命名 | bin+skills → `clients/`（下分 `cli/` + `skill/`） |
| 9 | 包名/布局 | 采用 src-layout，包名 `micro_mem` |

> ⚠️ 第 9 条「包名 micro_mem + src-layout」是我基于标准做法的**提议**，若你倾向 flat-layout（`micro_mem/` 直接在根目录）或别的包名，请在此标注。

---

## 五、整改方案（分阶段，每阶段独立验收）

### 阶段 0 · 打底（立脚手架，不动业务逻辑）

**目标**：先让工程有"壳"，后续阶段往里填。

- 新增 `pyproject.toml`：声明 `requires-python >= 3.10`、依赖（pyyaml + sqlite-vec）、包布局、入口点。
- 新增 `src/micro_mem/__init__.py`。
- 引入 pytest + ruff + mypy 依赖与配置骨架（暂不迁移用例）。

**验收**：`pip install -e .` 成功；`pytest` 能空跑通过。

### 阶段 1 · 结构重构

**目标**：达成 4.1 的目录树，行为不变。

- S1/S7：目录改名 `bin/ + skills/` → `clients/cli/ + clients/skill/`；更新 README/SKILL 里的路径引用。
- S1/S4：5 个入口脚本迁入 `cli/`；`distill_this_session.py` 里的 `auto_attach/_is_topic_node` 业务逻辑抽出到 `api/`（建议归入 `Distiller` 或新建 `api/attach.py`）。
- S3：`types.py` → `domain/`；`config/embedder/md_parser` → `common/`。
- S2：装配收敛到 `container.py`（删 4 处重复装配，server 不再硬编码引擎）。
- S5：`_knowledge_from_meta` 从 CLI 迁到 `common/md_parser.py` 或 `domain/`（让测试只依赖接口层）。
- S6：`build_db.py` 改走 `SqliteNetworkStore._ensure_schema`（复用建库逻辑）。

**验收**：根目录零裸 py；`clients/cli/mem.cmd search "xx"` 与整改前行为一致；全部 import 路径改写后测试仍通过（先保证不红）。

### 阶段 2 · 代码设计规范

**目标**：修 D1~D6，边界干净。

- D1：明确 body 的单一真值来源，`reader._build_knowledge` 统一（按决策 3，body 从 FTS，sources 从 md，代码与注释一致）。
- D2：封装 rowid——`save_vector` 改由 store 内部按业务 id 定位 rowid，业务层不再拿 rowid。
- D3：删除死代码 `count_nodes`。
- D4：补齐 `Knowledge.from_dict` / `DistillCandidate.from_dict`，与 Source/ExternalRef 对称。
- D5：`_is_topic_node` 改为走 `NetworkStore` 公开接口（新增 `has_parent_edge(id)` 之类），不再直访 `_conn`。
- D6：整理 `update_node` 参数语义，去掉绕圈的 old_body 传参。

**验收**：`grep` 确认无 `_conn` 直访、无 dead 方法；mypy 通过。

### 阶段 3 · 测试工程化

**目标**：pytest 化 + 消 T1~T5。

- T1：`run_tests.py` 迁移为 pytest 用例（用例逻辑平移，fixture 代替 TestConfig/手写 record）。
- T2：消除 `_knowledge_from_meta` 跨层依赖（依赖阶段 1 的 S5）。
- T3：pytest 配置里统一 UTF-8（`pyproject.toml` 或 conftest 重配置）。
- T4：性能用例（P1~P11）从必过门槛拆出，做成可选/独立 marker（CI 不因机器快慢而红）。
- T5：用真实 `Config` + 临时 config.yaml fixture 替代 TestConfig duck-type。

**验收**：`pytest` 一键跑通，功能用例全绿；CI 稳定不再有"机器性能"类 flaky 失败。

### 阶段 4 · 打包与工具链

**目标**：P1~P4 + 两份说明文档。

- P1/P4：pyproject 入口点（`mem` = `micro_mem.cli.main:main` 等），`pip install -e .` 后直接 `mem` 可用。
- P2/P3：补全依赖声明 + Python 版本约束。
- 更新 `.github/workflows/ci.yml` 对齐新入口（`python src/main.py` → 入口点/`python -m`）。
- **新增 `clients/README.md`**：大白话说明 cli + skill 两个入口 + 各自如何调用后面的 py 文件。
- **新增 `src/README.md`**：四段式（大白话 → 每个文件干啥 → 分层 → 如何协同工作）。
- 更新根 README 的命令表/目录结构，与整改后一致。

**验收**：全链路（安装 → 建库 → 载示例 → 检索 → 测试）在干净环境跑通；两份 README 落地。

---

## 六、待办清单（快照）

**已拍板决策**：见 4.2 表。

**新增待办（随阶段 4 一起做）**：
- `clients/README.md` —— 大白话说明 cli + skill 两个入口及各自如何调后面的 py 文件。
- `src/README.md` —— 四段式：大白话 → 每文件干啥 → 分层 → 协同工作。

---

## 七、本次不做（遗留 / 路线图）

| 项 | 原因 |
|---|---|
| "一键切换引擎"（Memory/Neo4j 实现） | 本次不扩展，NetworkStore 接口已留好 |
| 真实语义模型（替换 HashEmbedder） | 路线图项，接口已留好 |
| 可视化写接口接入页面 | 已实现未接页面，非本次工程化范围 |
| 引入真实数据/隐私治理 | 非结构问题 |

---

## 八、review 指引（你怎么反馈）

逐节标注即可，例如：
- 「4.1 第 9 条 flat-layout 我选 flat」
- 「阶段 2 的 D2 想先做」
- 「clients 下我还想加 xxx」

标完回给我，我按你的标注出**定稿版 + 阶段执行清单**，再开始动手。
