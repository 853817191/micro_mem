# examples —— 合成示例数据

这里的数据**全部是虚构的**，主题是一个与代码无关的日常话题（自制酸面包），
仅用于演示 micro_mem 的数据格式与检索效果。

**不含任何真实个人信息、内部项目名或凭据。** 你可以随意查看、修改、删除。

## 内容

```
examples/
├── knowledge/          5 条知识（演示 type × scope 与三种边类型）
│   ├── k-0001_酸面包基础工艺.md        method/domain  ← 聚合主题，其余 4 条挂靠它
│   ├── k-0002_发酵程度看状态不看时间.md  method/universal
│   ├── k-0003_全麦粉吸水率高于高筋粉.md  fact/domain     ← link → k-0002
│   ├── k-0004_用折叠代替揉面.md         method/domain    ← link → k-0003
│   └── k-0005_第一次烤出满意的酸面包.md  event/personal   ← sources 指向示例锚点
└── anchors/
    └── s-demo-001.md   示例锚点（虚构对话，distilled_until: 3）
```

覆盖的情况：

| 演示点 | 在哪 |
|---|---|
| 三种知识类型 | `event` k-0005 / `method` k-0002 / `fact` k-0003 |
| 三种成立范围 | `universal` k-0002 / `domain` k-0001 / `personal` k-0005 |
| `parent` 边（挂靠） | k-0002 ~ k-0005 → k-0001 |
| `link` 边（关联） | k-0003 → k-0002，k-0004 → k-0003，k-0005 → k-0002/k-0004 |
| 溯源到锚点 | k-0005 的 `sources.ref: s-demo-001` |
| 外部锚点 | k-0005 的 `external_refs: url` |
| 成熟度演进 | `settled` k-0001 / `evolving` k-0003 |

## 用法

```bash
# 前置：先建目录与库
python build_db.py

# 把示例数据放进真实数据目录
cp examples/knowledge/*.md data/knowledge/
cp examples/anchors/*.md   data/anchors/

# 从 md 真值重建索引
python src/main.py rebuild          # → rebuild 完成：5 条知识重建

# 试检索（多关键词融合）
python src/main.py search "发酵 温度" --multi

# 看图
python src/server.py                # → http://localhost:8000/
```

## 清空示例数据

`data/` 完全属于你自己，删掉即可重新开始：

```bash
rm -rf data && python build_db.py
```

## 为什么目录叫 `examples/` 而不是 `data/`

`data/` 是**使用者的私有数据目录**，已被 `.gitignore` 排除，永不入库。
示例数据如果放进 `data/`，会有两个问题：一是可能被误当成真实数据，二是容易和红线目录混在一起。
所以示例单独放在 `examples/`，由你自己决定什么时候、要不要拷进去。

CI 会自动加载这里的示例数据并校验检索结果，所以这些文件同时充当**格式回归样本**——
如果哪天格式解析坏了，CI 会先报警。
