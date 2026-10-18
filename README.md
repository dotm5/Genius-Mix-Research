# Genius Mix 研究：数据格式、约束链与离线工具

**简体中文** | [English](README.en.md)

[![CI](https://github.com/dotm5/Genius-Mix-Research/actions/workflows/ci.yml/badge.svg)](https://github.com/dotm5/Genius-Mix-Research/actions/workflows/ci.yml)

针对 iPod nano 第 5 代与第 6 代的 iTunes Genius Mix 研究。仓库给出设备 `.itdb` 数据库的字节级布局、Genius 约束链求值语义、簇邻接图、Mix 状态机门槛与歌单门控，以及一套仅依赖 Python 标准库的修复、对齐、聚类与模拟工具。文末的发现路径给出结论从容器枚举到字节码消融的收敛过程。

Genius 由四个独立数据面构成：Kind-32 容器保存候选种子，`genius_similarities` 提供候选边，`genius_metadata` 存储约束键，`genius_config.data` 是逐条约束的字节码。Mix 的可用阈值为 `count >= 2`（目标 10 首）；按曲目生成歌单的默认目标为 25 首、下限 10 首。

第 5 代与第 6 代使用同一份 151,212 字节的原厂 `genius_config.data`，数据库结构只差 `Dynamic.itdb.item_stats` 的一列。逐项对照见[跨代验证](docs/CROSSGEN.md)。

## 机理总览

```text
按曲目生成 Genius 歌单                     Genius Mix
当前曲目有效且 genius_id 非零               Kind-32 容器遍历器
            |                             item_to_container 按 physical_order
            |                             排除 item.genius_id == 0
            |                                      |
            |                       LCG 随机种子抽样器 (Primary 门控)
            |                       n=0 失败；n=1 直接取唯一种子
            |                       n>1 无放回抽样，至多 min(n,100)
            |                       仅 j=0 成功时设置 primary
            |                       后续成功进入 extras，不补 primary
            +----------------------+---------------+
                                   |
                 Mix 曲目生成入口 (NextMixTrack 执行器)
                                   |
                    Genius 图遍历与歌单构建共用核心
                                   |
            genius_metadata 查询种子：无行或空 data 即失败
              32B 基线 = 4×小端 u64 约束键，供 op=1/2 按 field 取键
                                   |
        genius_similarities：读取候选 genius_id -> 扩展、去重、解析 metadata
                                   |
           Config 约束字节码求值器 (Constraint Evaluator)
           op=2 簇邻接白名单 / op=1、3 同键间隔 / op=4 跳过统计
           op=5 随机抖动；返回 0 接受、1 暂拒、2 拒绝并标记已见
                                   |
                   接受曲目 -> 更新约束状态 -> 继续扩展
                                   |
           Mix：target=10 / minimum=2；单曲歌单：默认 25 / 10
           达到目标停止；count >= minimum 且对象有效即可用
```

修复工具按同容器步长 1/5/9/13 写入四个合法 `genius_id`，得到每行 32B 的约束键。该构造早期称为“同容器稠密连通图”；第三轮解码确认槽位语义为约束键，候选可达性由 `genius_similarities` 提供。每节点最多四个引用，小容器允许重复键。

## 消融实验

| 变体 | 保留条目 | 大小 | 最终结果 |
|---|---|---:|---|
| `cfg_empty` | 无 | 48 B | 可用 |
| `cfg_jitter` | `#6` random_jitter | 52 B | 可用 |
| `cfg_limits` | `#2/#3` distance、`#4` skip_count | 108 B | 可用 |
| `cfg_notable` | `#1..#6`，无大表 | 120 B | 可用 |
| `cfg_tableonly` | `#0` 591 项大表 | 151,140 B | 失败 |

`cfg_tableonly`（只含 591 项大表）在离线模型上失败。设备基线（第 11–16 轮）把准入定在 `op=2` 的命中与否：命中通过，未命中返回 2，该条目缺席时约束不实例化；metadata 长度与 `field=20` 的取值都不改变可播性，见[跨代验证](docs/CROSSGEN.md) §2。108B `cfg_limits` 是保留两条 distance 与一条 skip_count 的紧凑方案，详见[格式文档](docs/FORMAT.md)。

## 原始配置路径

原始 151,212B 配置在两代设备上逐字节不改即可运行，载体是 `genius_metadata.data` 的 168B（21 个 u64）布局：第 7 条的 `field=20` 因此按曲目读到真实键，间隔约束对每首曲目生效。32B 布局下该条目取键 0，可播性由 `op=2` 的命中与否决定，与长度无关（[跨代验证](docs/CROSSGEN.md) §2）。

再把 `metadata[0]` 填成簇表内的 id，`op=2` 的簇邻接白名单就会按流派收敛候选。簇表有 33 个连通分量，其中若干是**完全图**；把同一家族的流派放进同一分量的不同簇，族内互兼容、族间被过滤。候选仍来自 `genius_similarities`，使用**库级**池——收窄到容器级会让小容器候选不足。

完整方案、参数与校验见[对齐文档](docs/ALIGN.md)；边界实测见[边界记录](docs/BOUNDARY.md)，逐轮真机验证见[真机轮次记录](docs/ROUNDS.md)，回放增益见[增益说明](docs/REPLAYGAIN.md)，Mix 名称与本地化见[命名说明](docs/NAMING.md)，设备级差异见[跨代验证](docs/CROSSGEN.md)。

## 快速上手

环境为 Python 3.9+，仅依赖标准库。操作前退出 iTunes 等同步软件。工具在已有 Kind-32 容器与已有非空 similarities 上就地改写约束键和配置。

```powershell
$dbDir = Read-Host '请输入 iTunes Library.itlp 目录'
$backupDir = Read-Host '请输入仓库外的私人备份目录'
python tools/apply_genius_mix_fix.py "$dbDir" --backup-dir "$backupDir"
```

工具先校验输入，用 SQLite 备份接口保存全部 `.itdb` 快照，然后在单一事务内改写 `Genius.itdb` 的 metadata 与 `id=1` 配置。默认构造 108B `cfg_limits`；`--config empty` 选择 48B 空约束变体。全程无外部二进制依赖。

**输入基线：**

- 容器为 Kind-32，每个 Mix 至少包含两个不同且非零的 `genius_id`
- 成员具备 metadata 与非空 similarities，各 Mix 成员互不重叠
- `Dynamic.itdb.item_stats.genius_id` 与 `Library.itdb.item.genius_id` 一致
- 工具沿既有 similarities 图求值；新增可达性通过实际播放确认

完成后安全弹出并重启 iPod，检查实际播放。同步软件会覆盖离线改动。恢复时退出同步软件，把备份目录中的数据库文件放回原库目录。

## 工具

| 文件 | 用途 |
|---|---|
| `tools/apply_genius_mix_fix.py` | 兼容方案：保留容器，写入 32B 约束键与 48B / 108B 配置 |
| `tools/cluster_align.py` | 原始配置方案：簇对齐 + 168B metadata + 库级 similarities |
| `tools/cluster_graph.py` | 解码 `op=2` 簇邻接表，输出连通分量与完全分量 |
| `tools/custom_graph.py` | 自定义聚类：按 spec 写出 metadata 首槽与 similarities，并校验容器内标签相邻 |
| `tools/genius_sim.py` | 离线模拟器：按固件语义复现生成流程，部署前校验 |
| `tools/deploy_aligned.py` | 部署：快照备份 + 输入校验 + 写入 |
| `tools/boundary_matrix.py` | 边界测试夹具：按规格合成 Kind-32 容器 |

全部工具只依赖 Python 标准库；`genius_sim.py` 与 `cluster_align.py` 可完全离线运行。

## 发现路径

1. **定位容器**：在 `Library.itdb` 中按 `container.distinguished_kind=32` 枚举 Genius Mix 容器，由 `item_to_container` 取出成员与 `physical_order`。
2. **收敛调用点**：沿 `genius_metadata` 查询与 `genius_config.data` 读取点，把生成逻辑收敛到 Mix 遍历器与 `NextMixTrack` 执行器。
3. **解码字节码**：按 opcode 决定长度解析 `data`，得到 op=1/3 distance、op=2 簇邻接、op=4 统计过滤、op=5 随机抖动，并还原 151,212B 原配置的七条条目与控制尾。
4. **二分消融**：固定容器与 similarities，仅替换 `data`，得到 48B / 52B / 108B / 120B 可用与 151,140B 失败。
5. **隔离冲突**：离线模型把失败点收敛到 `op=1 field=20` 在 32B metadata 下的键 0 停滞。
6. **落地修复**：按同容器步长 1/5/9/13 写入 32B 约束键，配套 SQLite 快照备份与单事务写入。
7. **解码簇表**：`op=2` 的邻接表为无向图，591 个节点全部带自环，含多个完全图分量；把流派映射到同一分量的不同簇即可获得出厂语义。
8. **修正失败点**：离线模型把失败点收敛到第 7 条 `op=1 field=20` 对 32B metadata 的越界读取；第 11–16 轮的真机单变量序列把准入判据定在 `op=2` 的命中与未命中，原七条在 32B 与 168B 两种 metadata 下均可播。
9. **边界实测**：合成 1 … 189 个成员、1 … 7 个流派的容器，确认容器数量、成员数与流派跨度均无上限；空容器可见但不可播；重复成员行无副作用。
10. **跨代复核**：在 nano 5G 上重跑单变量序列，确认两代共用同一份原厂 `genius_config.data`，结构差异只在 `Dynamic.itdb.item_stats` 的一列。
11. **自定义聚类**：以 `op=2` 的完全分量为标签池，把自定义聚类映射到簇 id，由 `tools/custom_graph.py` 写出并校验。

## 使用范围

本项目给出 iPod nano 的 Genius 数据库结构与整数布局，以及修复、对齐、聚类与模拟工具和规范文本。模拟结果来自离线分析，真机结果取自测量记录。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题见 [SECURITY.md](SECURITY.md)。
