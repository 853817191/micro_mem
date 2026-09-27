# memory-system 测试用例

> 目的：验证记忆知识系统功能正确性、数据一致性、健壮性与接口性能。
> 执行方式：按优先级 P0 → P1 → P2 逐步执行；每条用例记录【通过 / 失败 + 现象】。
> 隔离约定：所有测试使用**临时 data 目录 + 临时 db**，不污染真实知识库（21 条）。

---

## 一、功能测试用例

### L1-L4 核心链路（P0，必测）

| 编号 | 接口 | 测试步骤 | 预期结果 |
|---|---|---|---|
| TC-W1 | create_knowledge 基本写入 | 创建 1 条知识（type/scope/title/summary/body） | 返回 id；`knowledge/k-{id}_*.md` 文件生成；SQLite 可查到 |
| TC-W2 | create_knowledge id 自动生成 | 连续创建 3 条 | id 递增（k-xxxx）；文件与索引一致 |
| TC-W3 | save_anchor 锚点 | 保存一段对话 | `anchors/s-*.md` 生成；id 唯一不覆盖 |
| TC-W4 | update_knowledge 元数据 | 改 title/status | nodes 更新；md 重写；检索新 title 命中 |
| TC-W5 | update_knowledge body 更新 | 改 body（验证 FTS 同步） | 新词可搜到、旧词搜不到（delete+insert 正确） |
| TC-W6 | deprecate_knowledge | 废弃一条 | status=deprecated；数据保留（不删） |
| TC-W7 | add_edge / remove_edge | 建边→删边 | edges 表增删正确 |
| TC-W8 | add_external_ref / remove_external_ref | 加/删外部锚点 | external_refs 表正确 |
| TC-R1 | search 关键词命中 | search("记忆") | 命中相关候选（id/title/summary） |
| TC-R2 | search 中文 2 字词 | search("审批")（无 FTS 命中场景） | LIKE 兜底命中 title/summary 含词的知识 |
| TC-R3 | search 过滤 | search + type/scope 过滤 | 只返回符合过滤的 |
| TC-R4 | search 无命中 | search("不存在的词xyz") | 返回空，不报错 |
| TC-R5 | traverse 双向 | 从父 traverse、从子 traverse | 父→子 和 子→父 都能到（双向） |
| TC-R6 | traverse 多跳 | traverse(id, depth=3) | 按跳数返回，去重正确 |
| TC-R7 | get 完整知识 | get(k-0004) | 返回摘要/正文/关联/外部锚点/sources |
| TC-R8 | get 无效 id | get("k-9999") | 返回 None，不报错 |
| TC-D1 | 数据一致性 | create 后比对 md frontmatter 与 nodes 行 | 字段一致 |
| TC-D2 | rebuild 恢复 | 清空索引 → rebuild | 从 md 全量重建，检索仍命中 |
| TC-D3 | update 一致性 | update 后 md 与索引一致 | 一致 |

### L5 蒸馏 / 导入（P1）

| 编号 | 接口 | 测试步骤 | 预期结果 |
|---|---|---|---|
| TC-DI1 | confirm_distill 落库 | 构造候选（1 keep + 1 reject + 1 edit） | keep/edit 落库、reject 跳过；关联按建议建边 |
| TC-DI2 | import_history | 造 2 个临时 JSONL → 导入 | 生成 2 个锚点；锚点内容包含对话文本 |

### L6 健壮性（P0）

| 编号 | 场景 | 测试步骤 | 预期结果 |
|---|---|---|---|
| TC-RO1 | 空库 | 空临时库执行 search/traverse/get | 不报错，返回空 |
| TC-RO2 | 特殊字符查询 | search 含引号/空格/emoji | 不崩溃（FTS 语法错误有兜底） |
| TC-RO3 | 重复 id | 手动建同 id 知识 | 幂等或明确报错，不脏数据 |
| TC-RO4 | 长文本 | body 10KB+ | 正常存取；FTS 可检索 |
| TC-RO5 | 重复边 | 同 from/to/type 建两次 | 幂等（不重复） |

### L8 可视化 API（P2）

| 编号 | 接口 | 测试步骤 | 预期结果 |
|---|---|---|---|
| TC-E1 | /api/stats | GET | 返回 total/type_dist/edge_count |
| TC-E2 | /api/graph | GET | nodes/edges 与库一致 |
| TC-E3 | /api/node/{id} | GET | 完整知识 JSON |
| TC-E4 | /api/search?q= | GET | 候选列表 |
| TC-E5 | /api/traverse/{id} | GET | 关联网络 |
| TC-E6 | /api/anchors + /api/anchor/{id} | GET | 锚点列表 + 内容 |

---

## 二、性能测试用例（P1）

> 方法：预热（warmup 10 次）→ 测 N 次 → 记录 平均 / P95 / P99（ms）。
> 数据量档位：小（100 条）/ 中（1000 条）/ 大（5000 条）。

| 编号 | 接口 | 数据量 | 次数 | 预期阈值（平均） |
|---|---|---|---|---|
| TC-P1 | save_anchor | 中 | 100 | ≤ 50ms |
| TC-P2 | create_knowledge | 中 | 100 | ≤ 50ms |
| TC-P3 | update_knowledge | 中 | 100 | ≤ 50ms |
| TC-P4 | search 关键词（FTS） | 中 | 100 | ≤ 100ms |
| TC-P5 | search 语义（向量） | 中 | 100 | ≤ 200ms |
| TC-P6 | traverse（3 跳） | 中 | 100 | ≤ 50ms |
| TC-P7 | get | 中 | 100 | ≤ 10ms |
| TC-P8 | rebuild | 中 | 5 | ≤ 2s |
| TC-P9 | import_history | 10 会话 | 3 | ≤ 5s |
| TC-P10 | confirm_distill | 10 候选 | 10 | ≤ 500ms |
| TC-P11 | 可视化 /api/graph | 中 | 10 | ≤ 200ms |

**性能数据量注**：向量检索依赖 sqlite-vec 扩展（已装）；大档（5000 条）用于观察线性度，不强制阈值。

---

## 三、执行清单（逐步核对）

### P0 批次
- [ ] TC-W1 ~ TC-W8（Writer 8 条）
- [ ] TC-R1 ~ TC-R8（Reader 8 条）
- [ ] TC-D1 ~ TC-D3（一致性 3 条）
- [ ] TC-RO1 ~ TC-RO5（健壮性 5 条）

### P1 批次
- [ ] TC-DI1 ~ TC-DI2（蒸馏/导入 2 条）
- [ ] TC-P1 ~ TC-P11（性能 11 条）

### P2 批次
- [ ] TC-E1 ~ TC-E6（可视化 API 6 条）

---

## 四、环境与前置

- Python 3.13 + sqlite-vec 0.1.9 + pyyaml（已装）
- 测试用临时目录，测试结束清理
- 执行入口：`tests/run_tests.py`（待建，按本清单执行并输出 通过/失败 报告）
