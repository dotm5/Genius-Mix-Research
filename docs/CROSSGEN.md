# 设备级差异与跨代验证

Genius 的数据布局、约束链与状态机与机型无关，规范正文见[格式文档](FORMAT.md)。本文记录在两台 nano 上实测到的全部设备级分歧点，以及用于分离这些分歧的单变量序列。测试对象为第 5 代（曲库 298 首）与第 6 代（曲库 189 首）。

## 1 结论

两代使用同一份 `genius_config.data`：151,212 字节、七条条目，逐字节相同。约束求值器与元数据读取器是同一份实现，模块在两代之间重定位。

设备级差异共四项：

| 项 | 第 6 代 | 第 5 代 |
|---|---|---|
| `item_stats` 列数 | 16 | 15 |
| `item_stats.genius_id` 列 | 有 | 无 |
| Genius 持久标记 | `item_stats.genius_id` | `item.genius_id` |
| 语句初始化 | Genius 代码内联持有串 | 统一初始化器，含 144 条语句 |

`item_stats` 前 15 列一致：

```text
item_pid, has_been_played, date_played, play_count_user, play_count_recent,
date_skipped, skip_count_user, skip_count_recent, bookmark_time_ms,
bookmark_time_ms_common, user_rating, user_rating_common, rental_expired,
play_count_user_original, skip_count_user_original
```

其余七张表（`container`、`item_to_container`、`container_seed`、`container_ui`、`genius_config`、`genius_metadata`、`genius_similarities`）两代列定义一致。

两代接受同一组容器类别与同一组约束条目：Genius Mix 为 `distinguished_kind=32`；`op=1/3/4/5` 与 `op=1 field=20` 在两代均生效，无需按机型删条目。Mix 队列长度与单曲歌单长度在两代取同一组来源。

## 2 单变量消融

第 5 代上的六个变体，每行只有 `genius_itdb` 一个文件与参照组不同：`Library.itdb`、`Dynamic.itdb`、`Extras.itdb`、`Locations.itdb` 逐字节相同。

| 变体 | config | metadata 长度 | metadata 首槽 | 结果 |
|---|---|---:|---|---|
| V1 | 原始 151,212B，七条 | 168B | 命中簇表 | 8 个 Mix 出现并可播 |
| V3 | 原始 151,212B，七条 | 32B | 命中簇表 | 8 个 Mix 出现并可播 |
| V5 | 只留 `op=2` | 168B | 命中簇表 | 8 个 Mix 出现并可播 |
| V7 | 去掉 `op=2`，其余六条保留 | 168B | 命中簇表 | 8 个 Mix 出现并可播 |
| V6 | 原始 151,212B，七条 | 168B | 未命中簇表 | Mix 出现，不可播 |
| V10 | 去掉 `op=2`，其余六条保留 | 168B | 未命中簇表 | 8 个 Mix 出现并可播 |

由此得到 `op=2` 的完整判据：

```text
op=2 不在配置中            -> 约束不实例化，候选全部通过
op=2 在配置中，首槽命中表   -> 通过
op=2 在配置中，首槽未命中表 -> 该候选返回 2
```

“未命中簇表”指 `metadata` 首槽的值不在这 591 个簇 id 之内。V6 与 V10 只差 `op=2`：首槽取值相同时，保留该条得到不可播，去掉后恢复可播，准入由 `op=2` 的命中与否决定。

metadata 长度不参与可播性判定。V1 与 V3 只差这一项，结果相同。

### 2.1 早期记录的更正

5G 的早期记录给出等价配置 151,192B，并记录原始七条不可用。该次测量的 metadata 槽位取值与本次不同；本次序列中配置条目数保持不变，首槽命中簇表。受控对照中原始七条在 32B 与 168B 两种 metadata 下均可播。

## 3 实现一致性

两代的约束求值器与元数据读取器是同一份实现，模块在两代之间重定位；其余部件同构。对应关系由两代共有的字符串引用建立，与字面量地址无关。

| 部件 | 两代关系 |
|---|---|
| 约束求值器 | 同一份实现 |
| 元数据读取器 | 同一份实现 |
| 字节码解释器 | 同构 |
| `op=2` 邻接表 | 同一份数据 |

设备写入行为与机型无关，见[格式规范](FORMAT.md) §7。

## 4 复现

```bash
python tools/custom_graph.py --src <itlp 目录> --out <输出目录> --spec <spec.json>
python tools/custom_graph.py --check <输出目录>
python tools/deploy_aligned.py --src <输出目录> --dev <盘符>
```

两代使用同一组命令；`deploy_aligned.py --dev` 指定设备盘符，`--backup-dir` 指定快照目录。写入前保留全部 `.itdb` 快照，写入后安全弹出。

## 5 后续测量

1. 相邻但不同簇的两组在设备上的收敛效果。
2. metadata 第 1 槽是否参与 `op=2` 判定。
3. 持有 `item_stats.genius_id` 列的固件上，写入该列与 Genius 生效的关系。
4. 其他世代（nano 4G、Classic）的适用性。

## 相关文档

- [格式规范](FORMAT.md)
- [对齐方案](ALIGN.md)
- [边界实测](BOUNDARY.md)
- [真机轮次记录](ROUNDS.md)
- [命名说明](NAMING.md)
