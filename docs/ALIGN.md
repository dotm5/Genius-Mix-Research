# 簇表对齐与原始配置运行方案

本文给出让设备运行未修改的 151,212B 原始 `genius_config.data` 的完整方案：把 `genius_metadata.data` 扩展到 21 个 u64，再用 `op=2` 的簇邻接表收敛流派。108B `cfg_limits` 是兼容替代方案，保留两条 distance 与一条 skip_count。

## 1 运行条件

原始配置的第 7 条为 `#6 [151176,151196) op=1 [20,50,10,10]`。取键规则见[格式文档](FORMAT.md) §4。32B metadata 的 count=4，`field=20` 恒得键 0。种子消费后该键计数为 1，候选逐个落入 `count < distance` 的间隔约束并返回 1；计数只在接受时增长，因此计数停在 1，输出只有种子一首，低于 Mix 下限 2。

把 `genius_metadata.data` 扩展到 21 个 u64（168B）后，`field=20` 读到真实键，停滞解除，`op=2` 的 591 项簇表同时恢复作用。配置本身逐字节保持原厂。

离线隔离（`tools/genius_sim.py`）：

| 配置 | metadata | 结果 |
|---|---|---|
| 原 7 条 | 32B | 全部失败 |
| 去第 7 条 | 32B | 全部可用 |
| 只留第 7 条 | 32B | 全部失败 |
| 去 `op=2` | 32B | 全部失败 |
| 原 7 条 | 168B | 全部可用 |

真机单变量对照（5G）给出的判据与此表的 `去 op=2` 一行不同：去掉 `op=2` 后候选全部通过，原七条在 32B 与 168B 两种 metadata 下均可用。真机结果见[跨代验证](CROSSGEN.md) §2。

## 2 21 槽布局

| 下标 | 字节 | 用途 |
|---:|---:|---|
| 0 | 0 | `op=2` 的簇键 |
| 1 | 8 | `op=1/3` distance 字段 1 |
| 2 | 16 | distance 字段 2 |
| 3 | 24 | distance 字段 3 |
| 4..19 | 32..152 | 空槽；自定义配置追加约束时使用 |
| 20 | 160 | 原始配置第 7 条的 distance 键 |

读取器按 `len(data) >> 3` 处理，变长合法；设备侧要求 `data` 非空。

## 3 簇表结构

`op=2` 的载荷是无向图：键集与邻居集相同，591 个节点全部带自环，共 17,997 个邻居槽位，最大出度 132，33 个连通分量，大小开头为 `319, 62, 27, 26, 22, 18, 18, 17, 13, 12, 7, 6, 6, 5, 4, 3, 3, 3, 2, 2 …`。

其中 62、27、22、18、18、17、13、12、7、6、6、5 个节点的分量为完全图：分量内任意两点相邻，分量之间不相邻。该性质是下表映射的基础。

用原始 65 行样本校验：候选簇落在种子邻居集内的比例为 80/213 ≈ 38%，随机密度基线 17,997 / 591² ≈ 5.2%，富集约 7.3 倍。设备下发的 similarities 是宽候选池，`op=2` 承担流派过滤。

## 4 家族映射

把同一家族的不同流派分配到同一完全分量内的不同簇：族内两两相邻，族间不相邻，`op=2` 即完成流派收敛。

| 家族 | 分量 | 簇 |
|---|---|---|
| A 电子系 | `comp(1022)`，62 节点完全图 | 1022 / 1023 / 1024 / 1025 / 1026 / 1027 |
| B 流行系 | `comp(18)`，27 节点完全图 | 1068 / 1069 / 1070 / 1071 / 1072 / 1073 |
| C 兜底 | `comp(1044)`，12 节点完全图 | 1044 / 1047 / 1049 / 1051 |

簇 id 取自原始配置的表内值，与 `op=2` 的命名空间一致。流派到家族的对应表 `FAMILY` 位于 `tools/cluster_align.py`，可按曲库调整。

## 5 similarities 池

`genius_similarities` 是候选来源，`genius_metadata` 提供约束键。`--sims` 提供三档：

| 模式 | 候选池 | 实测结果 |
|---|---|---|
| `library`（默认） | 全库，顺序为同流派 → 同家族 → 其余 | 全部可用 |
| `family` | 同家族 | 与 `library` 逐首相同（`op=2` 已先行过滤） |
| `container` | 同容器 | 小容器候选不足：2 首的容器生成 2 首后低于下限，设备判为不可播 |

运行配置取 `library` 池，由 `op=2` 收敛流派。

## 6 `--keys` 粒度

`--keys` 控制 `metadata[1..3]` 的取值：

| 模式 | 取值 | 效果 |
|---|---|---|
| `unique` | 每首全局唯一 | distance 约束不触发 |
| `coarse` | 8 / 12 / 16 个值循环 | distance 约束最强 |
| `mixed`（默认） | 24 / 48 / 全局唯一 | 折中 |

真机（第 10 轮）：`coarse` 下 8 个容器全部可播，键池最小的档位同样不会被间隔约束耗尽；默认仍取 `mixed`。

## 7 自定义聚类

`op=2` 判定候选的依据是候选 `metadata[0]` 是否落在种子 `metadata[0]` 的邻居集合内，因此自定义聚类的全部自由度落在“每首曲目使用哪个簇 id”这一项上。

构造步骤：

1. 建立曲目之间的偏好关系，得到无向图；边表示“允许同框”。
2. 对图做聚类：连通分量或社区发现均可。
3. 为每个聚类选一个簇 id。需要互相同框的聚类从同一个完全分量内取 id；需要隔离的聚类取自不同分量。单一聚类只用一个 id 时自环保证内部一致。
4. 写入 `item.genius_id`、`genius_metadata.data`（首槽放该 id）、`genius_similarities.data`；可选地为每个聚类建立 `kind=32` 容器。

候选顺序由 `genius_similarities` 提供，`op=2` 只做准入判定。写入时按同聚类、相邻聚类、其余三段排序，`op=2` 再完成收敛。

```bash
python tools/cluster_graph.py --config <genius_config.data>        # 列出分量与完全分量
python tools/custom_graph.py --src <itlp 目录> --out <输出目录> --spec <spec.json>
python tools/custom_graph.py --check <输出目录>
```

`spec.json` 支持两种写法：直接给出聚类与标签，或给出无向边列表由工具求连通分量。工具在写入前输出聚类之间的邻接矩阵，写入后校验每个容器内标签两两相邻。

## 8 校验

离线模拟（`tools/genius_sim.py`，Python RNG 近似设备 LCG）：

| 场景 | 结果 |
|---|---|
| Genius Mix（target 10 / minimum 2），8 容器 × 12 个随机种子 | 96/96 可用 |
| 按曲目歌单（target 25 / minimum 10），189 首 | 189/189 可用，均达 25 首 |

真机校验：

| 轮次 | 设置 / 场景 | 结果 |
|---|---|---|
| 5 | 17 个非空容器，成员 1 … 189，流派 1 … 7 | 全部可播并带 Mix 封面 |
| 6 | `default_num_results=50, min_num_results=5` | 单曲歌单生成 50 首；容器仅 8 首的 K-Pop 同样到 50 首，候选池为库级 |
| 7 | `default_num_results=50, min_num_results=100` | 三首 Dance 曲目的单曲歌单在 target 50 处停止，`50 >= 100`，返回 unplayable |
| 6 / 7 | 8 个 Mix | 长度不受这两列影响 |

两条阈值的取值来源由此闭合：target = `default_num_results`，minimum = `min_num_results`。Mix 走路径固定参数，初始目标 10、可用下限 2，播放期的队列容量见[格式文档](FORMAT.md) §5。逐轮改动见[真机轮次记录](ROUNDS.md)。

## 9 操作顺序

```bash
python tools/cluster_align.py --src <itlp 目录> --out <输出目录> \
       --keys mixed --sims library
python tools/genius_sim.py --db <输出目录> --all-mixes
python tools/deploy_aligned.py --src <输出目录> --dev <盘符>
```

第一步在独立目录生成，不触碰设备；第三步自动快照备份（`--backup-dir` 指定快照目录，默认 `out/devbk`）。写入后安全弹出并重启，设备在挂载时缓存数据库。

## 10 运行约束

- iTunes 重新同步会覆盖 `Genius.itdb`，需重跑第 1、3 步。
- 设备从不通告 `SupportsSoundCheck`，同步后 `item.relative_volume` 可能被重置为 0。
- 空容器（成员数为 0）出现在列表中，无法播放。
- 容器数量、成员数（1 … 189）与流派跨度均不设上限。
- 原始 151,212B 配置在 32B 与 168B 两种 metadata 下均可用，两代一致，见[跨代验证](CROSSGEN.md)。
- 数据保留：`genius_config`、`genius_metadata`、`genius_similarities` 三张表没有时间列，Mix 歌单按需生成，重新同步是唯一的失效路径。`op=4` 的 604800 秒窗口作用于跳过统计（只看最近一周的记录），与数据年龄无关。
