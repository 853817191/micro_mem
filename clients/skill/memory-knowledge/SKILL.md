---
name: memory-knowledge
description: micro_mem 记忆知识管理系统入口——查询知识（search/get）+ 蒸馏沉淀对话为知识（anchor/distill/confirm）。触发场景：用户问"查记忆系统""记忆知识""之前讨论过 X""知识管理里的 X""帮我蒸馏""沉淀这段对话"。写入/维护走命令，skill 只管查询与蒸馏。
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
- 返回候选：`id / title / summary / type（event|method|fact）/ scope`
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
- 可视化：`python <MEMORY_HOME>/src/micro_mem/cli/server.py`（长驻服务，保持 python 直跑，路径用正斜杠）后访问 http://localhost:8000/

## 二、蒸馏对话为知识

当用户说"帮我蒸馏"或要求沉淀一段对话时：

### 流程
1. **读取对话**：
   - 当前上下文对话：直接用（本次会话内容）
   - 历史锚点：`Read <MEMORY_HOME>/data/anchors/s-*.md`
2. **检索相关主题（挂靠准备）**：对要沉淀的知识，**先 search 知识库**找相关主题/总结点（用核心词检索，如"Nginx 反向代理"）；命中则把主题 id 记入候选的 `suggested_parents`
3. **AI 提炼候选**：读对话 → 按下方 JSON 格式产出候选清单 → **同类知识填 `suggested_parents` 指向已有主题** → 写入
   `<MEMORY_HOME>/data/temp/candidates.json`
4. **用户 review**：展示候选清单，用户标注 keep / edit / reject（改 decision 字段）
5. **确认落库**：
   ```
   mem confirm <MEMORY_HOME>/data/temp/candidates.json
   ```
   落库后系统会尝试**自动挂靠**到相关主题节点（见蒸馏原则-同类聚合）

完整蒸馏命令（包装器已封装，路径坑在壳内解决）：
```
mem anchor <jsonl_path>        # 建锚点：jsonl → data/anchors/s-*.md（缺路径时自动取最新会话）
mem distill <anchor_id>        # 增量准备：重同步锚点 + 输出增量轮次（写 distill_state.json）
mem confirm <candidates.json>  # 落库：幂等去重 + 溯源校验 + 回写蒸馏游标 + 自动挂靠
```

### 候选 JSON 格式
```json
[
  {
    "type": "event | method | fact",
    "scope": "universal | domain | personal",
    "title": "知识标题",
    "summary": "判断用摘要（3~5 句）",
    "body": "正文",
    "suggested_parents": ["k-0004"],     // 挂靠建议（可空）
    "suggested_links": ["k-0005"],       // 关联建议（可空）
    "sources": [{"type": "conversation_distilled", "ref": "anchors/s-xxx.md"}],
    "decision": "keep | edit | reject"
  }
]
```

### 蒸馏原则
- **值得沉淀**：问题解决过程、可复用经验/方法、达成的决策、领域共识
- **不值得沉淀**：闲聊、一次性问答、纯操作过程
- 候选的 suggested_parents/links 由 AI 给出，用户 review 时可改
- **同类知识聚合**：同一类工作事项（如"Nginx 反向代理配置"）应挂靠到已有主题节点，不散落成游离事情
- **先展示候选，用户确认后才落库**（不自动入库）
