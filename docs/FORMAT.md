# Genius 数据结构与约束链规范

本文以来源资料第三轮更正后的解码结果为准，给出设备端 Genius 的数据布局、约束字节码与状态机语义。偏移均指数据库字节流内偏移，所有整数为小端。

## 1 数据库关系

设备端 `.itdb` 是明文 SQLite。Genius 使用三张核心表：

```sql
CREATE TABLE genius_config (
  id INTEGER NOT NULL, version INTEGER,
  default_num_results INTEGER DEFAULT 0,
  min_num_results INTEGER DEFAULT 0,
  data BLOB, PRIMARY KEY(id), UNIQUE(version));
CREATE TABLE genius_metadata (
  genius_id INTEGER NOT NULL, version INTEGER,
  data BLOB, PRIMARY KEY(genius_id));
CREATE TABLE genius_similarities (
  genius_id INTEGER NOT NULL, version INTEGER,
  data BLOB, PRIMARY KEY(genius_id));
```

样本中的配置行为 `id=1, version=2, default_num_results=25, min_num_results=10`。长度阈值存放在独立列，`data` 只承载约束字节码。`Library.itdb` 中 `item.pid` 是本地条目主键，`item.genius_id` 是 Genius 查询键，两者分属不同命名空间。

| 数据对象 | 职责 |
|---|---|
| `container.distinguished_kind=32` | Genius Mix 容器类型；Kind-26 属于另一容器类别 |
| `item_to_container` | 保存候选种子成员，按 `physical_order` 排序 |
| `container_seed` | 兼容容器数据，种子由 `item_to_container` 提供 |
| `Dynamic.itdb.container_ui` | 容器显示与播放状态 |
| `Dynamic.itdb.item_stats` | `genius_id` 对齐及播放/跳过统计 |

种子查询：

```sql
SELECT item.genius_id FROM item
INNER JOIN item_to_container ON item_to_container.item_pid=item.pid
WHERE item.genius_id != 0 AND item_to_container.container_pid=?1
ORDER BY item_to_container.physical_order;
```

历史创建工具在容器上写入 `smart_is_genius=1`、`smart_is_dynamic=1`、`smart_is_filtered=1`、`smart_limit_kind=2`，并复用已有 384B `smart_criteria`。公开修复工具沿用既有容器并保持这些字段原值。

## 2 metadata 与 similarities

### 2.1 metadata：4 槽约束键

32B 基线的布局：

| 字节偏移 | 类型 | 用途 |
|---:|---|---|
| 0 | 小端 u64 | op=2 的簇键；也可作为指定字段的 distance 键 |
| 8 | 小端 u64 | distance 字段 1 |
| 16 | 小端 u64 | distance 字段 2 |
| 24 | 小端 u64 | distance 字段 3 |

读取器按 `len(data) >> 3` 处理变长 u64 数组，32B 是基线长度。查询无行或 `data` 为空时，种子装载失败。把长度扩展到 21 个 u64（168B）后，原始配置第 7 条 `op=1 field=20` 读到真实键，配置不改动即可运行；布局与实测见[对齐文档](ALIGN.md)。

原工具按同容器索引 `k` 写入 `[(k+1)%N,(k+5)%N,(k+9)%N,(k+13)%N]` 对应的四个合法 `genius_id`，得到每行 32B。早期资料把该构造称为“同容器稠密连通图”。步长 1 使这个引用图可绕行全部成员；端侧把这些槽位解释为约束键，候选可达性由 `genius_similarities` 提供。每节点最多四个不同引用，小容器允许重复或包含自身。

### 2.2 similarities：候选边

未压缩记录的布局：

| 偏移 | 类型 | 含义 |
|---:|---|---|
| 0 | u32 | flag=0 表示明文 |
| 4 | u32 | payload_size=`4+8*N` |
| 8 | u32 | count=N |
| 12 | N×u64 | 候选 `genius_id` 数组 |

总长 `12+8*N`。现有记录中 N=15/19/20/32，对应 132/164/172/268B。每个候选先命中 metadata，再进入约束链。flag 非零时记录为 56B 头加压缩负载，头内带版本标识 `1.2.3`；公开工具处理明文记录。

## 3 Config 字节流

Config 是带类型、按算子决定长度的字节流：

```text
u32 entry_count
entry_count 次：
    u32 opcode
    由 opcode 决定的参数
随后可选控制尾：先读 u64，再视剩余长度读 u32
```

所有整数小端，无隐含对齐。头部是整个 u32 条目数；清空配置即写入 `0` 并重建条目区。

| op | 组件 | 参数 | 总长度 |
|---:|---|---|---:|
| 1 / 3 | distance 同键间隔 | field, floor, distance, jitter，4×u32 | 20B |
| 2 | compatible_genre 簇邻接白名单 | field、outer_count，随后为变长邻接记录 | 变长 |
| 4 | skip_count 统计过滤 | min_skips, window_sec, play_gap, skip_pct | 20B |
| 5 | random_jitter | round0_threshold, later_threshold | 12B |
| 6 | 空指令 | 无 | 4B |
| 7 | 异常数据的终止分支 | 公开生成器不使用 | — |

### 3.1 原配置的精确偏移

下表给出原配置七条条目的精确偏移。

| 历史最终序号 | 半开字节范围 | op | 参数 |
|---|---|---:|---|
| 头 | [0,4) | — | count=7 |
| #0 | [4,151084) | 2 | field=0，591 项簇邻接表 |
| #1 | [151084,151104) | 3 | [1,2,6,6] |
| #2 | [151104,151124) | 3 | [2,2,10,6] |
| #3 | [151124,151144) | 3 | [3,3,10,6] |
| #4 | [151144,151164) | 4 | [2,604800,0,50] |
| #5 | [151164,151176) | 5 | [70,100] |
| #6 | [151176,151196) | 1 | [20,50,10,10] |
| 尾 | [151196,151204) | — | u64=0，循环组合为 OR 模式 |
| 尾 | [151204,151208) | — | u32=10 |
| 余量 | [151208,151212) | — | u32=20，当前解析不消费 |

解码修订：第一轮把大表末端记为 151096，把 `[1,2,6,6]` 后半段记作两条 nop，并把 op=5 当作零参数，参数与第 7 条因此落入“尾部”；第二轮修正大表边界，仍误读最后一条；第三轮给出上表。

### 3.2 48B / 108B 自主构造

工具沿用历史切片产生的布局，以保留既有的实测变体。

```text
公共兼容后缀，11×u32=44B：
70,100,1,20,50,10,10,0,0,10,20

cfg_empty：
[0] + 公共兼容后缀                              = 48B

cfg_limits：
[3]                                             # 三个条目
[3,2,2,10,6]                                    # distance field=2
[3,3,3,10,6]                                    # distance field=3
[4,2,604800,0,50]                               # skip_count
+ 公共兼容后缀                                 = 108B
```

108B 的精确偏移：条目数 [0,4)，第一条 [4,24)，第二条 [24,44)，第三条 [44,64)，公共后缀 [64,108)。48B 的后缀位于 [4,48)。后缀开头两个 u32 由控制尾读取器合并为 u64，随后读取 u32=1；其余 32B 保留。该后缀承载历史切片的字节序列，其数值独立于 op 参数。

“#3/#4/#5 三条限额”沿用旧切片编号；最终序号为 `#2/#3` 两条 distance 与 `#4` skip_count。108B 去掉簇白名单与字段 20，保留两种多样性间隔与跳过统计，是历史真机验证过的紧凑兼容方案。

## 4 约束链求值

每条约束持有独立 state；生效条目按配置顺序求值。返回 0 接受，1 拒绝本候选并在约束放松后重试，2 拒绝并标记已见。接受曲目后消费 metadata、更新计数；metadata 解析失败的候选同样标记已见。

distance 以 `metadata[field]` 为键。初始有效距离为 `distance + [1..jitter]`，jitter=0 时不附加随机项。键未出现则通过；已有键计数小于有效距离则返回 1。消费时命中键重置为 2，其他键递增，新键插入计数 1。轮询每轮把有效距离减 1，直至 floor。

op=2 以种子 `metadata[field]` 在簇邻接表中查找集合。无集合指针时通过；集合为空时返回 2；候选键位于邻居集合内时通过，否则返回 2。无集合与空集合是两种独立状态。

op=4 使用四个统计列：kind1=`date_played`，kind2=`play_count_user`，kind3=`date_skipped`，kind4=`skip_count_user`。参数 `[2,604800,0,50]` 的判据为：

```text
skip != 0
且 2 < skip
且 (play_gap == 0 或 play_gap > date_played - date_skipped)
且 604800 > now - date_skipped
且 100*skip > 50*(skip+play)
```

全部满足返回 1，即最近七天多次跳过、跳过占比超过 50% 的曲目被拒绝。`play_gap=0` 关闭该比较维度。

`item_stats` 的时间列（`date_played`、`date_skipped`）为 Mac epoch（2001-01-01 00:00:00 UTC）秒数，与 Unix 时间相差 978,307,200 秒。写入时以 Mac epoch 为基准；用 Unix 时间会把时间点推到 2057 年，`now - date_skipped` 成为大负数，窗口判据放行该候选。校验基准：`813996291` 对应 2026-10-18。

op=5 首轮从 1..100 抽样，超过 70 暂拒；状态推进后阈值升为 100，不再据此拒绝。该指令携带两个 u32 参数。

## 5 种子抽样与长度门控

LCG 更新式为 `s = 1103515245*s + 12345`。早期记录索引为 `(rand()+1)%n`；第二轮记录回调返回 `((rand+1)%n)+1`，调用方夹到有效索引。两式相差一位，分别对应各自的实现切片；随机抽样由设备端 LCG 执行。

多种子时无放回抽样至多 min(n,100) 次。仅当第 0 次抽到的种子具有非空 metadata 时设置 primary；后续成功只加入 extras。primary=0 时共用核心直接失败，extras 不补位。n=0 失败；n=1 不抽样，直接使用唯一种子。

单曲路径额外检查当前曲目有效且 `genius_id` 非零。命令类型 1 为 PreparePlaylist，类型 4 为 PrepareMixPlaylist；两者种子来源不同，随后共用 metadata 装载、相似度候选扩展与约束链求值。

第一首输出种子本身，然后由 similarities 构造候选队列；已解析的 metadata 进入缓存复用。候选接受后更新 state 与输出，继续图扩展和约束放松。循环控制位决定两个继续条件取 AND 或 OR。

| 路径 | target | minimum | 取值来源 |
|---|---:|---:|---|
| Genius Mix | 10 | 2 | 路径固定参数 |
| 单曲 Genius 歌单 | 默认 25 | 默认 10 | 配置列 default_num_results / min_num_results |

停止判据为对象有效且 `count == target`；可用判据为对象有效且 `count >= minimum`。Mix 准备路径记录有“下一首为空或轮计数 >20 则失败”的分支。结果码：1 成功，2 初始化无效，3 unplayable。

Mix 的初始生成在 target 10 处停止。播放期间 `NextMixTrackCommand` 每次把队列补到至少 5 首待播，补货走追加路径，独立于 target 判据；队列构造时带 `reserve 50` 容量提示，真机连续播放停在 50 首（第 8 轮）。Mix 长度分为三个概念：初始生成目标 10、队列容量 50、可用下限 2。

生成路径在内存中构造歌单；静态检索覆盖的生成路径中没有向容器成员回写的语句。

## 6 二分法消融

| 变体 | 条目描述 | 大小 | 第一轮状态 | 最终状态 |
|---|---|---:|---|---|
| cfg_empty | 无条目 | 48B | 可用 | 可用 |
| cfg_jitter | #6 random_jitter | 52B | 未测 | 可用 |
| cfg_limits | #3/#4/#5 三条限额 | 108B | 可用 | 可用 |
| cfg_notable | #1..#6，无大表 | 120B | 可用 | 可用 |
| cfg_tableonly | #0 591 项大表 | 151,140B | 预期失败 | 失败 |

早期解释假设 metadata 槽位存放候选曲目 ID：自造 metadata 填簇 ID 时候选解析失败，填合法 `genius_id` 时大表成员资格失败，记录表 ID 与库内 ID 交集为 0。第三轮确认槽位是约束键，候选 ID 全部来自 similarities；`cfg_notable` 与 `cfg_limits` 可用是实验事实，`cfg_tableonly` 的失败点由离线隔离定位到第 7 条 `op=1 field=20`。

591 项表的结构为 `u32 field + u32 outer_count + outer_count×{u64 cluster_id, u32 inner_count, u64 neighbors[inner_count]}`。统计结果：591 节点、17,997 个邻居槽位、591 个 distinct ID，值域 2..50000068，最大 inner_count=132；图全部对称，含 591 个自环，共 33 个连通分量，分量大小开头为 [319,62,27,26,22,18,18,17,13,12,7,6,6,5,4,3,3,3,2,2]。规范采用 17,997 槽位与 max inner_count=132 的计数口径。

原始 65 行样本中，50 行的簇键命中节点；相似候选簇落在种子邻居集内的比例为 80/213≈38%，随机密度基线为 5.2%。

第三轮离线模拟的隔离结果：

| 变体 | metadata | 结果 |
|---|---|---|
| 原 7 条 | 32B | count=1，低于下限 2 |
| 去第 7 条 | 32B | 全部达到可用门槛 |
| 只第 7 条 | 32B | count=1 |
| 去 op=2 | 32B | count=1 |
| 去 op=2 与第 7 条 | 32B | 全部达到可用门槛 |
| 原 7 条 | 168B | 全部达到可用门槛 |

机制：field=20 超出 32B 的 4 槽范围，求值器取键 0；种子消费后该键计数为 1，后续候选按间隔约束返回 1；计数只在接受时增长，floor=50 高于初始距离，形成一首的停滞。把 metadata 扩展到 21 槽后字段 20 可读，约束链恢复推进。

168B 方案使用簇完全分量映射与库级 similarities 池，模拟记录为 96/96 Mix、189/189 单曲歌单可用；该模型使用 Python RNG，部署记录包含数据库完整性检查。完整步骤见[对齐文档](ALIGN.md)，边界实测见[边界记录](BOUNDARY.md)。

## 7 工具链

`tools/apply_genius_mix_fix.py`（兼容方案，48B / 108B 配置）、`tools/cluster_align.py`（原始配置方案，168B metadata + 库级 similarities）、`tools/genius_sim.py`（离线模拟）、`tools/deploy_aligned.py`（快照备份 + 部署）、`tools/boundary_matrix.py`（边界夹具）、`tools/check_links.py`（文档相对链接检查）。全部工具仅依赖 Python 标准库，模拟与对齐可完全离线运行。原 108B 工具在来源记录中有真机可用结果。

相关文档：[对齐文档](ALIGN.md)、[边界记录](BOUNDARY.md)、[真机轮次记录](ROUNDS.md)、[跨代验证](CROSSGEN.md)、[命名说明](NAMING.md)、[增益说明](REPLAYGAIN.md)。

早期分量大小为 49/42/32/25/16/13/8/2 的样本给出一个边界：小容器满足 Mix 的两首门槛时，单曲歌单的默认十首仍可能不足；可用性同时取决于候选图、约束链与状态机。验证设备范围为 nano 6G 与 nano 5G；两代的分歧点见[跨代验证](CROSSGEN.md)。
