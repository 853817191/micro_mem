---
name: memory-knowledge
description: micro_mem 记忆知识管理系统入口——查询知识（search/get）+ 蒸馏沉淀对话为知识（anchor/distill/plan/confirm 四轮协议）。触发场景：用户问"查记忆系统""记忆知识""之前讨论过 X""知识管理里的 X""帮我蒸馏""沉淀这段对话"。写入/维护走命令，skill 只管查询与蒸馏。
---

# micro_mem 记忆知识管理系统

历史对话蒸馏沉淀的结构化知识库（锚点 + 知识 + 网）。本 skill 提供**查询**与**蒸馏**两个能力。

> **MEMORY_HOME** = 本仓库的根目录（clone 下来的位置）。下文所有 `<MEMORY_HOME>` 需替换为实际绝对路径；也可用环境变量 `MEMORY_HOME` 指定，包装器优先读它。

## 〇、命令规范（mem 包装器）

micro_mem 所有命令**统一走包装器** `clients/cli/mem.cmd`（Windows）/ `clients/cli/mem.sh`（macOS、Linux），**不要**手拼 python 全路径——Windows 的反斜杠路径在 Bash 下会被转义吞掉，路径必坏。

把 `clients/cli/` 加入 PATH 后即可直接调用：

- **PowerShell / cmd**：`mem search "关键词"` / `mem get k-0001`
- **Bash**：`mem.cmd search "关键词"`（Git Bash 不补 `.cmd` 扩展名，必须显式写）；macOS / Linux 用 `mem.sh search "关键词"`
- **手动查询**：`! mem search 关键词`（会话内直接跑，绕过 AI）
- **定位 MEMORY_HOME**：包装器用脚本自身位置推导仓库根，通常无需手动指定；要换位置时设 `MEMORY_HOME` 环境变量覆盖
- **检索失败** → 先检查 shell 工具选择与路径写法，再考虑重试（勿盲目重试）

两处平台差异要注意：包装器扩展名（Git Bash 不自动补全），以及 Python 解释器名（Windows 多为 `python`，macOS / Linux 为 `python3`，可用 `PYTHON` 环境变量覆盖）。

## 一、查询知识

### 搜索（多路检索）

```
mem search --multi "<核心词1 核心词2>"   # 多关键词一次融合；Bash 下用 mem.cmd search --multi
```

- **提取核心词**：从用户问题提取 2-3 个核心业务词，去掉停用词（记得/之前/讨论/那个/我们/的事情 等）和修饰词
  - 例："记得我们之前讨论的记忆与知识库的讨论吗" → 核心词「记忆 知识库」
- **多路检索**：**一次 `--multi` 全词**（空格分隔）——内部逐词双路召回 + 跨词 RRF 融合，多词同时命中的知识自动前置、不要求词连续出现；无需多次跑命令
- 返回候选：`id / title / summary / type（event|model|fact|method）/ scope`
- **无命中时依次尝试**：
  1. 换同义词 / 相近词（如"知识库"→"知识管理"）
  2. 放宽词（用更短的核心词，如"记忆系统"→"记忆"）
  3. 确认 `MEMORY_HOME` 指向的确实是那份数据目录（否则可能读到空库）
- 命中后按需用 `get` 取详情；检索不到时如实说明"记忆系统中未找到"

### 取详情
```
mem get <id>
```
- 返回：摘要 / 正文 / 关联（parents/links）/ 外部锚点 / 来源（sources）
- 溯源对话：`Read <MEMORY_HOME>/data/anchors/*.md`
- 可视化：`mem serve [--port 8000]`（长驻服务）后访问 http://localhost:8000/

## 二、蒸馏对话为知识（v2 四轮协议）

> **蒸馏前必读两篇**：`source/SOURCE.md`（S0 素材准备：五种输入通道 + 确认闸门 + 素材纪律）→ `domain/DISTILL.md`（R1 定位三选一与坐标选择、R3 正文写作规范）。
> 本期只做领域蒸馏（mode=domain）；事件蒸馏另立专题（`event/`，未实现）。
> 旧版蒸馏规范已归档至 `_archive/`（v1 单轮批处理范式，仅历史参考，勿遵循）。

机制（供给 / 校验 / 落库 / 游标）全部归系统，AI 只做两个判断点：**R1 定位** 与 **R3 正文写作**。

1. **R1 定位**（AI 产计划草案，只写坐标 + gist，**不写正文**）：
   - 会话驱动（维护期增量）：`mem distill <anchor_id>` → 系统供增量轮次全文 + 预检索相关子树
   - 主题驱动（建树/补全期）：`mem distill --domain <领域>` → 系统供领域树全景 + 相关锚点清单 + 空缺统计
2. **R2 审计划**（用户）：审结构变更单——蒸什么 / 长在哪 / 跳过什么；批准 / 改坐标 / 增删项
3. **R3 成型**（AI）：`mem plan <计划文件>` 提交归档（得 plan_id）→ 逐条**只回读 source.turns 标注的轮次原文**，写 title/summary/body 填回归档计划文件（`data/plans/plan-<id>.json`）
4. **R4 落库**（系统）：`mem confirm <plan_id>` → 硬校验 + 幂等落库 + 计划回写归档（`data/plans/`）+ 游标回写（仅会话驱动；主题驱动不推游标）

配套命令：

```
mem anchor <路径|URL>            # S0+S1：素材（jsonl/md/html/URL）→ 锚点（先 --preview 核对再落库）
mem anchor --text "描述"         # 对话里直接贴的描述 → 单轮锚点
mem anchor <输入> --preview      # 确认闸门：只渲染素材确认视图，不落库
```
