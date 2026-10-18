# 跨代验证：iPod nano 5G

本文记录同一套 Genius 数据库方案在 iPod nano 第 5 代上的验证结果。测试对象为 2009 年款 Click Wheel 机型（带摄像头），曲库 298 首。

## 1 结论

`.itlp` SQLite 套件与 Genius 流水线在 5G 上可用，与 6G 同构；差异集中在三处。

| 项 | nano 6G | nano 5G |
|---|---|---|
| `.itlp` 为设备读取的权威库 | 是 | 是 |
| Genius Mix 容器 kind | 32 | 32 |
| `genius_config` / `genius_metadata` / `genius_similarities` | 使用 | 同样使用 |
| `op=2` 簇邻接白名单 | 工作 | 工作 |
| `op=1/3` distance | 工作 | 工作 |
| `op=4` 统计过滤 | 工作 | 工作（`item_stats` 结构不同） |
| `op=5` random_jitter | 工作 | 工作 |
| `op=1 field=20` | 需 168B metadata | 删除该条目后可用 |
| `item_stats.genius_id` 列 | 有 | 无 |
| kind-26 容器 | 未建 | 存在空条目，列表不显示 |
| Mix 队列长度 | 50 | 50 |
| 单曲歌单长度 | `default_num_results` | `default_num_results`（置 50 时生成 50 首） |

5G 的等价配置为原始 151,212B 减去第 7 条 `op=1 field=20`，即 151,192B，其余六条逐字节保留。

## 2 验证步骤

### 2.1 `.itlp` 是权威库

把用户播放列表 `LoFi` 改名为 `TEST-B`，重启后设备显示 `TEST-B`，`.itlp` 即设备读取的权威库。同一轮把空的 kind-26 容器改名 `TEST-A`，该条目不出现在列表中。

### 2.2 最小 Genius 栈

取 6 首同流派曲目，写入非零 `item.genius_id`、32B metadata、封闭 similarities 池与 48B `cfg_empty`，并建立 `kind=32` 容器 `G32` 与 `kind=26` 容器 `G26`。

G32 出现并可播，G26 不出现在列表：Genius Mix 的容器 kind 与 6G 相同。

### 2.3 原始配置的消融

| 配置 | 结果 |
|---|---|
| 原始 151,212B（七条） | 不可用 |
| 去掉 `op=4`（151,192B） | 不可用 |
| 去掉 `op=1 field=20`（151,192B） | 可用 |

5G 与 6G 的分歧点是第 7 条。5G 的 metadata 读取器接受 168B 的 blob，第 20 槽不生效，行为与 6G 在 32B metadata 下的停滞一致。`op=4` 在 5G 上正常，其统计路径与 6G 不同（`item_stats` 无 `genius_id` 列），不阻塞生成。

### 2.4 完整流水线

按流派为 298 首建立 12 个 kind-32 容器，使用与 6G 相同的簇映射与库级 similarities（`tools/cluster_align.py`），配置取 151,192B。

12 个 Mix 全部出现并可播，名称与成员数正确。

## 3 长度

与 6G 一致：Mix 队列 50 首；单曲歌单取 `default_num_results`，置 50 时生成 50 首。

Mix 队列在播放期间逐步补满，长度以 Now Playing 的总数为准；随机播放模式下的重复曲目会干扰连续听感判断。

## 4 操作要点

```bash
python tools/cluster_align.py --src <5G itlp 目录> --out <输出目录> \
       --keys mixed --sims library

# genius_config.data 换为 151,192B 变体（原始配置去掉第 7 条）

python tools/deploy_aligned.py --src <输出目录> --dev <盘符> \
       --config-len 151192 --meta-len 168
```

5G 的校验目标为配置 151,192B、metadata 168B；`--config-len` 与 `--meta-len` 覆盖默认的 6G 常量，部署前保留全部 `.itdb` 快照。

## 5 后续测量

1. 5G 的 metadata 槽位上限：确认 32B 是否足够，下标 4 … 19 是否可读。
2. `min_num_results` 在 5G 上的作用（`default_num_results` 已验证）。
3. 5G 上 `op=4` 的统计数据源。
4. 其他世代（nano 4G、Classic 等）的适用性。
