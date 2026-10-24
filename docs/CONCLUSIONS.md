# 关键结论

本文只保留可直接使用的结论。推导过程与逐轮实验记录见各专题文档。

## 1 Genius Mix：6G 歌单为空

第 6 代 Genius 的持久标记在 `Dynamic.itdb.item_stats.genius_id`，必须与
`Library.itdb.item.genius_id` 逐首一致。只改后者或只重建容器时，设备能枚举
Mix 容器，但按 stats 解析不到可播放条目，表现为「歌单能打开但为空 / 所选
歌曲不可播放」。

- 5G 的 `item_stats` 没有这一列，走 `item.genius_id`，无需同步。
- `tools/genius_stats.py` 负责同步、校验与刷盘；`custom_graph.py`、`cluster_align.py`、
  `apply_genius_mix_fix.py`、`boundary_matrix.py` 写入时自动同步。
- `genius_sim.py --strict-stats` 在 `item_stats` 不一致时直接失败；
  `deploy_aligned.py` 部署前拒绝不一致的源库。
- 详见 [排障文档](TROUBLESHOOTING_EMPTY_MIX.md)。

## 2 音量平衡（Sound Check）与 ReplayGain 是两条链

nano 6G 有两个独立的回放增益字段：

| 机制 | 字段 | 公式 | 语义 |
|---|---|---|---|
| ReplayGain（逐曲） | `item.relative_volume` | `rv = clamp(round(1000*(10^(G/20)-1)), -1000, 1000)` | 线性幅度增益；`0` = 不调整；负值衰减，正值提升（上限 +6 dB） |
| Sound Check | `avformat_info.volume_normalization_energy` | `X = clamp(round(1000*10^(-G/10)), 0, 65534)` | `0` = 无数据/等同 `1000`；`1000` = 0 dB；`X>1000` 衰减，`X<1000` 提升 |

- `G` 是目标增益（dB），负值表示曲目偏响、需要衰减。
- 文件内的 `iTunNORM` 标签设备**不读**；固件的 MP4 元数据读取器只接受
  `iTunSMPB`。要改变设备音量，必须改 itdb。
- 「设置 → 音乐 → 音量平衡」读取 `Playback.SoundCheck`，作用的是
  `volume_normalization_energy`，**不读** `item.relative_volume`。
- 设备只在宿主生成过 Sound Check 数据时才能工作；`vne=0` 时开关无听感变化。
  Apple 用户指南同样写明：iTunes 未打开 Sound Check，设备端打开也没有效果。
- 真机验证：写入 vne 后，播放中切换音量平衡差异明显；同时清零
  `relative_volume`，确认差异来自 Sound Check 链。
- 公式与字段定义与 libgpod、podkit、reupen/ipod_manager 的公开实现一致。
- 详见 [回放增益文档](REPLAYGAIN.md)。

## 3 歌曲排序

设备「歌曲」列表按 `Library.itdb.item.title_order` 排序。导入工具若把
`title_order` 写成导入序号，列表就会按导入顺序显示，而不是标题。

原厂风格：

1. 拉丁字母 A-Z，大小写不敏感，忽略开头的 `The` / `A` / `An`；
2. 汉字按拼音（Windows `LCMapStringEx`，`zh-CN` + `SORT_CHINESE_PRC`）；
3. 数字；
4. 其它符号。

`title_order = 100 × 排名`。依赖表同样需要重算：

- `track_artist.name_order` / `sort_name`
- `artist.name_order` / `sort_name`
- `album.name_order` / `sort_order` / `artist_order` / `sort_name`
- `genre_map.genre_order`
- `item.album_by_artist_order` 原厂为 `NULL`

`tools/fix_sort_orders.py` 负责重算并校验，写入前自动快照。详见
[排序文档](SORTING.md)。

## 4 工具链不变量

- 6G 上 `item_stats.genius_id == item.genius_id`，5G 无该列。
- 部署前校验 `genius_config` / `genius_metadata` / `genius_similarities` 非空；
  部署后刷盘。
- 离线模拟器不建模 `item_stats.genius_id`，需要时加 `--strict-stats`。
- 所有会写设备的工具在写入前备份 `.itdb` / `.cbk`。

## 5 相关文档

- [回放增益与音量平衡](REPLAYGAIN.md)
- [歌曲/艺术家/专辑排序](SORTING.md)
- [Genius Mix 空歌单排障](TROUBLESHOOTING_EMPTY_MIX.md)
- [数据格式与约束链](FORMAT.md)
