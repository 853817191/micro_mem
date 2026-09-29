# src/ 是怎么一回事

## 一、大白话：这层目录是干嘛的

`src/` 是「源码根」，里面只有一个包：**`micro_mem`**。

> 为什么是 `src/micro_mem/` 两层，而不是 `src/` 下面直接放 `api/`、`cli/`？

因为 **`micro_mem` 是包名**。Python 里 `import` 的每一段，都对应一层目录：

```python
import micro_mem                        # 找 src/micro_mem/
from micro_mem.api.writer import ...    # 找 src/micro_mem/api/writer.py
```

如果 `src/` 下面直接是 `api/`、`cli/`，那 import 就变成 `import api`、`import cli` —— 包名不叫 `micro_mem` 了，而且 `api`、`cli`、`store`、`common` 这种太通用的名字，会和别人装的库撞车。

那 `src/` 这层又为什么留着？它是 Python 社区标准的 **src-layout**，作用只有一个：**把「正在开发的包」和「其他一切（tests、examples、web、文档）」隔开**。这样你必须 `pip install -e .` 之后才能 import，import 到的永远是「安装的那一份」，不会出现「我改了代码、测试却在跑旧版本」的隐蔽 bug。

一句话：**`src/` 是隔离层，`micro_mem/` 是包，`api/cli/domain/…` 是包里的子包。**

## 二、每个文件都在干什么

```
src/micro_mem/
├── __init__.py            包的标志文件（内容仅一句注释）
│
├── domain/                领域模型 —— 所有接口参数的基础「值对象」
│   └── types.py           枚举（KnowledgeType/Scope/Status/EdgeType/…）+ 值对象
│                          （Knowledge/Source/ExternalRef/SearchHit/DistillCandidate）
│
├── common/                公共基础件 —— 无业务、被上层复用
│   ├── config.py          读 config.yaml，提供目录/维度/引擎等配置
│   ├── embedder.py        文本 → 向量（Embedder 接口 + HashEmbedder 实现）
│   └── md_parser.py       md 文件 frontmatter 的解析与反序列化
│
├── store/                 引擎层 —— 数据真正落进 SQLite 的地方
│   ├── base.py            NetworkStore 接口（端口，契约定死，可换实现）
│   └── sqlite_store.py    SqliteNetworkStore 实现（默认引擎，五张表读写）
│
├── api/                   业务接口层 —— 面向「四操作」的读写入口
│   ├── writer.py          MemoryWriter：记忆/蒸馏/收录/关联的「写」
│   ├── reader.py          MemoryReader：检索/遍历/取详情的「读」
│   ├── distiller.py       Distiller：AI 候选 → 用户 review → 落库
│   ├── importer.py        Importer：存量 jsonl → 锚点
│   └── attach.py          自动挂靠：落库后把新知识聚合到主题节点
│
├── container.py           装配容器（Composition Root）：全项目唯一装配点
│
└── cli/                   命令入口 —— 人 / AI 真正敲命令的地方
    ├── main.py            统一命令（rebuild/create/search/traverse/get/import）
    ├── server.py          可视化 HTTP 服务（web/ 页面后端）
    ├── build_db.py        建库脚本
    ├── import_one.py      导入单个历史会话
    └── distill.py         蒸馏会话（anchor/distill/confirm 三段）
```

## 三、分层：谁依赖谁

从下到上，**上层依赖下层，下层不碰上层**：

```
第 6 层  cli/         命令入口（main/server/build_db/import_one/distill）
第 5 层  container.py 装配容器（把下面所有零件拼起来）
第 4 层  api/         业务接口（writer/reader/distiller/importer/attach）
第 3 层  store/       引擎（base 接口 + sqlite_store 实现）
第 2 层  common/      基础件（config/embedder/md_parser）
第 1 层  domain/      领域模型（types）
```

箭头方向（依赖只能往下指，不能往上、也不能跨层乱指）：

```
cli ──► container ──► api ──► store ──► common ──► domain
```

`domain` 是地基，谁都能用；`common` 只依赖 `domain`；`store` 依赖 `common`+`domain`；`api` 依赖 `store`+`common`+`domain`；`container` 把 `api`+`store`+`common` 拼起来；`cli` 只认 `container` 拼好的成品，不自己动手拼零件。

## 四、它们是怎么协同工作的

看两条真实链路就懂了。

### 链路 1：收录一条知识（写）

```
cli/main.py:cmd_create
  └ container.build_components()      # 一次性拼好 config/store/writer/reader/…
      └ api/writer.py:create_knowledge(k)
          ├ _validate / _assign_meta           # 校验 + 补 id/时间
          ├ _write_md_file                     # 写真值（data/knowledge/k-*.md）
          ├ _sync_indexes                      # 同步索引
          │    └ store/create_node + save_vector   （store/sqlite_store.py）
          ├ _build_edges                       # 建 parent/link 边
          └ _link_external_refs                # 挂外部锚点
```

### 链路 2：检索（读）

```
cli/main.py:cmd_search
  └ api/reader.py:search(query)
      ├ store/search_keyword     # 字面路（FTS5 + LIKE）
      ├ store/search_semantic    # 语义路（向量）
      └ _rrf_merge               # 两路排名融合 → SearchHit 轻量候选
```

### 一个原则贯穿两条链路

**真值在 md 文件，SQLite 只是索引。** 写时先写真值 md、再同步索引；读时轻量字段走索引、下钻全文再回 md。索引随时能 `rebuild`（清空 + 从 md 全量重建），所以真值文件是唯一的「不能丢」的东西。
