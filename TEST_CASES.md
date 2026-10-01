# micro_mem 测试覆盖地图

> 全部用例自动化（pytest），无手工清单。执行：`pytest`（功能）/ `pytest -m perf`（性能）。
> 隔离约定：所有测试使用**临时目录或纯内存装配**（`assemble_inmemory` / `tmp_path`），不碰真实 `data/`。

## 覆盖矩阵（103 条 = 92 功能 + 11 性能）

| 测试文件 | 条数 | 覆盖 |
|---|---|---|
| `test_memory_stores.py` | 12 | 内存端口行为基建：id 分配、时间戳、深拷贝隔离、子串检索、L2 最近邻、BFS 遍历 |
| `test_infra_config.py` | 7 | 配置定位优先级（MEMORY_HOME > ./config.yaml > 默认）、嵌套合并、相对路径解析、非法值报错 |
| `test_infra_markdown_truth.py` | 8 | **字节级兼容红线**：frontmatter 字段顺序/时间戳格式与旧版一致、游标点状改写不动其他字节、读回 created/updated |
| `test_index_contract.py` | 16 | **端口契约**（同一用例 × 内存 + SQLite 两实现）：upsert 全量替换、空 body 更新不丢数据（B2）、delete 级联、边幂等、检索过滤 |
| `test_infra_sqlite_index.py` | 6 | schema 入包可读、FTS trigram 中文子串命中、向量存取、DIST_RATIO 距离过滤 |
| `test_infra_claude_jsonl.py` | 4 | jsonl 轮次切分（tool_result 并入前轮）、工具过程渲染、目录扫描 |
| `test_services.py` | 20 | 应用服务编排（全在内存端口上）：<br>**D6 写路径** create/update 双源同步；<br>**D7 删除** 移出索引、真值保留、rebuild 复活；<br>**D3 读路径** get 从真值组装、索引 body 丢失不影响；<br>**D8 语义兜底** off/on_zero_hit/非法值；<br>**D9 蒸馏** MAIN 两段式占位替换、游标回写与状态文件消费、幂等去重、溯源校验拒绝、EDIT、自动挂靠（≥0.3 重叠 + 聚合主题）；<br>**边同步真值** add/remove_edge 后 rebuild 不丢 |
| `test_cli.py` | 10 | 统一入口输出格式（CI grep 守卫的 rebuild/search/traverse 格式）、蒸馏三段 CLI、失败路径（退出码、游标不回写） |
| `test_perf.py` | 11 | 性能阈值（200 条中量库，默认排除）：create/update ≤50ms、search ≤100ms、get ≤10ms、rebuild ≤10s 等 |

## 架构决策 → 测试的映射

| 决策 | 守卫用例 |
|---|---|
| D2 frontmatter 字节级兼容 | `test_infra_markdown_truth.py::test_save_creates_file_with_legacy_byte_format` |
| D3 body 只从真值出 | `test_services.py::test_get_reads_full_body_from_truth` |
| D7 删除语义 | `test_services.py::test_delete_removes_from_index_but_keeps_truth` + `test_rebuild_resurrects_deleted` |
| D8 语义兜底三策略 | `test_services.py::test_semantic_fallback_*` |
| D9 蒸馏编排收编服务层 | `test_services.py::test_confirm_*`（MAIN/游标/幂等/溯源/EDIT/自动挂靠） |
| B2 空 body 更新丢数据 | `test_index_contract.py`（两实现同测）+ `test_services.py::test_update_partial_none_means_unchanged` |
| 索引可重建自证 | `test_services.py::test_rebuild_preserves_node_fields_and_edges`、真实数据验收（见 `doc/优化设计文档/micro_mem-架构重设计方案.md` 迁移阶段 5） |
| 输出格式回归（CI grep） | `test_cli.py::test_rebuild_output_format` / `test_search_output_format` / `test_traverse_output_format` |

## 真实数据验收（迁移阶段 5 已执行）

用真实 `data/`（35 条知识）验证过：新栈 rebuild 产物与旧索引逐节点对比，
title/summary/type/scope/status **零差异**；差异仅两处且方向均为改进——
created/updated 忠实读取 frontmatter（旧栈 rebuild 曾统一刷成重建时刻）、
旧索引漏收的 1 个节点（k-0015，真值在而旧索引无）被正确收编。
真值文件 rebuild 前后 md5 全量一致（rebuild 只写索引，不碰真值）。
