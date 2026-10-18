# iPod nano 6G Genius 数据库格式与 Mix 修复

**简体中文** | [English](README.en.md)

iPod nano 第 6 代（已停产）的 Genius Mix 离线修复工具与格式规范。仓库给出设备 `.itdb` 数据库的字节级布局、Genius 约束链求值语义、Mix 状态机门槛，以及一套仅依赖 Python 标准库的修复、对齐与模拟工具。文末的发现路径给出结论从容器枚举到字节码消融的收敛过程。

Genius 由四个独立数据面构成：Kind-32 容器保存候选种子，`genius_similarities` 提供候选边，`genius_metadata` 存储 4 槽约束键，`genius_config.data` 是逐条约束的字节码。Mix 的可用阈值为 `count >= 2`（目标 10 首）；按曲目生成歌单的默认目标为 25 首、下限 10 首。

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

修复工具按同容器步长 1/5/9/13 写入四个合法 `genius_id`，得到每行 32B 的约束键。早期资料把这种构造称为“同容器稠密连通图”；第三轮解码确认槽位语义为约束键，候选可达性由 `genius_similarities` 决定。每节点最多四个引用，小容器允许重复键。

## 消融实验

| 变体 | 保留条目 | 大小 | 最终结果 |
|---|---|---:|---|
| `cfg_empty` | 无 | 48 B | 可用 |
| `cfg_jitter` | `#6` random_jitter | 52 B | 可用 |
| `cfg_limits` | `#2/#3` distance、`#4` skip_count | 108 B | 可用 |
| `cfg_notable` | `#1..#6`，无大表 | 120 B | 可用 |
| `cfg_tableonly` | `#0` 591 项大表 | 151,140 B | 失败 |

`cfg_tableonly` 的失败点定位在第 7 条 `op=1 field=20`：field 20 超出 32B metadata 的四个槽位，求值器取键 0，种子消费后该键计数为 1，后续候选全部落在间隔约束内，计数停在 1。第三轮离线隔离逐项复现了该结果。108B `cfg_limits` 是兼容方案，保留两条 distance 与一条 skip_count，详见[格式文档](docs/FORMAT.md)。

## 原始配置路径

把 `genius_metadata.data` 从 32B 扩展到 **168B（21 个 u64）** 后，原始 151,212B 配置可以不加修改地运行：第 7 条的 `field=20` 从越界键 0 变为真实约束键，停滞解除，`op=2` 的 591 项簇表也恢复作用。

再把 `metadata[0]` 填成簇表内的 id，`op=2` 的簇邻接白名单就会按流派收敛候选。簇表有 33 个连通分量，其中若干是**完全图**；把同一家族的流派放进同一分量的不同簇，族内互兼容、族间被过滤。候选仍来自 `genius_similarities`，使用**库级**池——收窄到容器级会让小容器候选不足。

完整方案、参数与校验见[对齐文档](docs/ALIGN.md)；边界实测见[边界记录](docs/BOUNDARY.md)，回放增益见[增益说明](docs/REPLAYGAIN.md)，Mix 名称与本地化见[命名说明](docs/NAMING.md)。

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
| `tools/genius_sim.py` | 离线模拟器：按固件语义复现生成流程，部署前校验 |
| `tools/deploy_aligned.py` | 部署：快照备份 + 输入校验 + 写入 |
| `tools/boundary_matrix.py` | 边界测试夹具：按规格合成 Kind-32 容器 |

全部工具只依赖 Python 标准库；`genius_sim.py` 与 `cluster_align.py` 可完全离线运行。

## 发现路径

1. **定位容器**：在 `Library.itdb` 中按 `container.distinguished_kind=32` 枚举 Genius Mix 容器，由 `item_to_container` 取出成员与 `physical_order`。
2. **收敛调用点**：沿 `genius_metadata` 查询与 `genius_config.data` 读取点，把生成逻辑收敛到 Mix 遍历器与 `NextMixTrack` 执行器。
3. **解码字节码**：按 opcode 决定长度解析 `data`，得到 op=1/3 distance、op=2 簇邻接、op=4 统计过滤、op=5 随机抖动，并还原 151,212B 原配置的七条条目与控制尾。
4. **二分消融**：固定容器与 similarities，仅替换 `data`，得到 48B / 52B / 108B / 120B 可用与 151,140B 失败。
5. **隔离冲突**：把失败点收敛到 `op=1 field=20` 对 32B metadata 的键 0 停滞；168B 的 21 槽扩展解除该冲突。
6. **落地修复**：按同容器步长 1/5/9/13 写入 32B 约束键，配套 SQLite 快照备份与单事务写入。
7. **解码簇表**：`op=2` 的邻接表为无向图，591 个节点全部带自环，含多个完全图分量；把流派映射到同一分量的不同簇即可获得出厂语义。
8. **修正失败点**：离线隔离证明失败点是第 7 条 `op=1 field=20` 对 32B metadata 的越界读取；扩展到 168B 后原始配置直接可用。
9. **边界实测**：合成 1 … 189 个成员、1 … 7 个流派的容器，确认容器数量、成员数与流派跨度均无上限；空容器可见但不可播；重复成员行无副作用。

## 使用范围

本项目面向已停产个人设备的本地数据库研究与维修维护，内容为公开的数据库结构与整数布局，以及自研修复脚本与整理后的规范文本。中文探索中的模拟结果来自离线分析，真机行为以来源记录为准。
