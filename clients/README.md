# clients/ 是怎么一回事

## 一、大白话：这层目录是「人和 AI」的入口

`clients/` 不是源码，是**入口层**。它把「怎么用 micro_mem」和「micro_mem 代码长什么样」两件事拆开：

- 源码在 `src/micro_mem/`，是给开发者看的；
- 入口在 `clients/`，是给**每天真正用它的人**（你）和 **AI**（Claude Code skill）用的。

这里只放两个子目录：

| 目录 | 给谁用 | 是什么 |
|---|---|---|
| `cli/` | 人（命令行） | 命令包装器 `mem.cmd` / `mem.sh` |
| `skill/` | AI（Claude Code） | `memory-knowledge` skill 定义 |

## 二、cli/：命令包装器（人用的入口）

`cli/mem.cmd`（Windows）和 `cli/mem.sh`（macOS / Linux）是**一行转发脚本**：

```text
mem <任何子命令>
  └──> 执行 PYTHONPATH=<仓库根>/src python -m micro_mem <任何子命令>
```

所有子命令（`search` / `get` / `create` / `deprecate` / `delete` / `traverse` / `rebuild` /
`import` / `anchor` / `distill` / `confirm` / `serve`）都在同一个 `mem` 入口下，不再有转发分支。

为什么要有这层包装器，而不是直接敲 `python -m micro_mem`：

1. **不用装包**：设好 `PYTHONPATH` 就跑，不依赖 `pip install`；多人共享一个仓库时尤其方便。
2. **自动推导仓库根**：脚本按自身位置找仓库根（`clients/cli/../..`），仓库放哪都行；需要换位置用 `MEMORY_HOME` 环境变量覆盖。

## 三、skill/：Claude Code skill（AI 用的入口）

`skill/memory-knowledge/SKILL.md` 是给 **Claude Code** 的 skill。它把 micro_mem 核心原则里「判断归模型」的那一半，写成了模型可执行的流程：

- 从用户问题提取核心词 → `mem search` → 判断要不要 `mem get` 全文 → 蒸馏时先展示候选再落库。

装法：把 `skill/memory-knowledge/` 拷到 `~/.claude/skills/`（用户级）或 `<项目>/.claude/skills/`（项目级）。

> 注意：skill 里同样**统一走 `mem` 包装器**，不手拼 python 路径——理由和上面一样。

## 四、还有一条路：pip 安装后的标准入口点

除了上面的包装器，micro_mem 也支持标准 Python 打包：

```bash
pip install -e .
mem search "关键词"      # 直接可用（这是 pip 装的入口点，不再是 clients/cli/mem.cmd）
mem rebuild              # 首次运行自动建库（幂等）
mem serve                # 起可视化服务
```

两条路的区别：

| 方式 | 需要装包？ | 适用场景 |
|---|---|---|
| `clients/cli/mem.cmd` | 否 | 只想用、不想 `pip install`；或多人共享一个仓库 |
| pip 入口点 `mem` | 是（`pip install -e .`） | 正式部署、CI、脚本集成 |

它们最终都调到同一个入口 `micro_mem.interfaces.cli:main`，**功能完全一致**，只是「怎么敲命令」不同。
