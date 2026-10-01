# src/ 是怎么一回事

## 一、大白话：这层目录是干嘛的

`src/` 是「源码根」，里面只有一个包：**`micro_mem`**。

> 为什么是 `src/micro_mem/` 两层，而不是 `src/` 下面直接放 `application/`、`interfaces/`？

因为 **`micro_mem` 是包名**。Python 里 `import` 的每一段，都对应一层目录：

```python
import micro_mem                              # 找 src/micro_mem/
from micro_mem.application.ports import ...  # 找 src/micro_mem/application/ports.py
```

如果 `src/` 下面直接是 `application/`、`interfaces/`，那 import 就变成 `import application` —— 包名不叫 `micro_mem` 了，而且 `application`、`interfaces` 这种太通用的名字，会和别人装的库撞车。

那 `src/` 这层又为什么留着？它是 Python 社区标准的 **src-layout**，作用只有一个：**把「正在开发的包」和「其他一切（tests、examples、docs）」隔开**。这样你必须 `pip install -e .` 之后才能 import，import 到的永远是「安装的那一份」，不会出现「我改了代码、测试却在跑旧版本」的隐蔽 bug。

一句话：**`src/` 是隔离层，`micro_mem/` 是包，`domain/application/infrastructure/interfaces` 是包里的子包。**

## 二、分层与依赖规则（六边形架构）

```
interfaces → application → domain ← infrastructure
                     ↑__________________|
                    composition.py（唯一交叉口）
```

**依赖方向**：`interfaces`（CLI/HTTP）只调 application 服务；`application` 只依赖 domain 模型与自己的端口 ABC；
`infrastructure` 实现 application 声明的端口、依赖 domain；`composition.py` 是唯一同时 import 两侧的地方。

```
src/micro_mem/
├── __main__.py              python -m micro_mem 入口 → interfaces.cli.main
├── domain/                  领域模型：纯 dataclass + 枚举，零依赖
│   └── models.py            Knowledge/Anchor/DistillCandidate/NodeRecord/SearchHit…
├── application/             应用层：用例编排
│   ├── ports.py             三个端口 ABC：TruthStore / IndexStore / Embedder
│   ├── knowledge_service.py 写路径：create/update/deprecate/delete + 双源协调（D6）
│   ├── search_service.py    读路径：search/search_multi/traverse/get（D3/D8）
│   ├── distill_service.py   蒸馏三段：anchor/prepare/confirm（D9）
│   ├── import_service.py    存量导入：scan jsonl → 锚点
│   └── rebuild_service.py   索引重建：TruthStore → IndexStore 全量投影
├── infrastructure/          基础设施：端口的具体实现 + 配置 + 反腐解析
│   ├── config.py            配置定位/合并/路径解析（D5）
│   ├── markdown_truth.py    TruthStore：Markdown 真值（frontmatter 字节级兼容）
│   ├── sqlite_index.py      IndexStore：SQLite（FTS5 trigram + sqlite-vec）
│   ├── memory_truth.py      TruthStore：内存实现（单测基建）
│   ├── memory_index.py      IndexStore：内存实现（单测基建）
│   ├── hash_embedder.py     Embedder：哈希占位实现（跑通向量管道）
│   ├── claude_jsonl.py      Claude 会话 jsonl → 对话文本（纯函数，反腐层）
│   └── schema.sql           SQLite 表结构（随包分发，importlib.resources 读取）
├── interfaces/              入口层：参数解析与打印，无业务逻辑
│   ├── cli.py               单 mem 命令树（12 子命令）
│   ├── http.py              可视化服务（make_handler 工厂，无模块级副作用）
│   └── web/                 可视化页面（随包分发）
└── composition.py           装配根：assemble() 生产 / assemble_inmemory() 测试
```

## 三、关键约定（为什么这么分）

- **端口在 application，实现在 infrastructure**：用例声明"我需要什么样的外部世界"（ABC），
  基础设施去满足。换引擎/换真值存储不动业务代码，只换装配根一行。
- **真值与索引是两条线**：`TruthStore` 是权威（id/时间戳归它管），`IndexStore` 是投影
  （全量替换语义，随时可由 `RebuildService` 重建）。写路径"先 truth 后 index"只在
  `KnowledgeService` 一处协调；读路径"index 出候选、truth 出全文"只在 `SearchService` 一处组装。
- **删除的语义（D7）**：`delete` 只把节点移出索引工作集，真值档案保留，`rebuild` 会恢复；
  想彻底消失用 `deprecate`（留痕）或手动删真值文件。
- **契约测试防漂移**：`tests/test_index_contract.py` 用同一份用例跑内存与 SQLite 两套
  IndexStore 实现——新实现必须通过同一契约。

## 四、调用链长什么样

以 `mem create` 为例：

```
interfaces/cli.py:cmd_create
└ composition.assemble()               # 生产装配：config → MarkdownTruthStore + SqliteIndexStore + …
  └ application/knowledge_service.py:create(k)
    ├ truth.save_knowledge(k)          # 真值落盘（分配 id/时间戳）  → infrastructure/markdown_truth.py
    └ _sync_index(kid)                 # 读回真值全量 → 投影索引
      ├ index.upsert_node(node, body)  # nodes + FTS5                  → infrastructure/sqlite_index.py
      ├ index.add_edge(...)            # edges（parent/link）
      ├ index.add_external_ref(...)    # external_refs
      └ index.save_vector(...)         # nodes_vec（embedder.embed(summary)）
```

`mem rebuild` 则完全绕过写服务：`RebuildService.rebuild()` =
`index.clear_all()` → 遍历 `truth.list_knowledge()` → 逐条投影（节点/边/外部锚点/向量）。
这是"索引只是真值的衍生品"这条架构自证链路的可执行证据。
