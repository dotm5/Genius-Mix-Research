# 排障：Mix 能打开但歌单为空（6G）

## 1 现象

- Genius Mix 出现在设备列表里，可以进入；
- 进入后没有曲目，无法切歌，封面为空；
- 数据库侧 `container` / `item_to_container` / `genius_metadata` /
  `genius_similarities` 看起来都正常，离线模拟器也报可用。

## 2 根因

第 6 代的 Genius 持久标记是 **`Dynamic.itdb.item_stats.genius_id`**，
不是 `Library.itdb.item.genius_id`（见[跨代验证](CROSSGEN.md) §1）。

两处必须逐首完全一致。只改 `item.genius_id` 而 `item_stats.genius_id`
仍为 0（或仍是旧值）时，设备知道每首歌属于哪个 Genius 条目，但按 stats
解析不到可播放条目，结果就是歌单出现但为空 / 提示所选歌曲不可播放。
该失败模式是 6G 的已知问题；`item_stats.genius_id` 与 `item.genius_id` 必须逐首一致，见 `docs/CROSSGEN.md` §1 与 `README.md` 的输入基线。

离线模拟器 `tools/genius_sim.py` 不读 `item_stats.genius_id`，因此它报
`usable=True` 不能排除这个原因。

## 3 触发的典型操作

`tools/custom_graph.py` 会重新分配全部 `item.genius_id`（`LABEL_BASE` 起），
但它早期只写 `Library.itdb` 与 `Genius.itdb`。在 6G 上运行后，
`item_stats.genius_id` 会全部停在 0 或旧值 —— 正好命中上述根因。

## 4 修复

写入前退出 iTunes 等同步软件，先备份 `Dynamic.itdb`，然后：

```sql
-- 在 Dynamic.itdb 上，逐首同步（item 表在 Library.itdb，需在脚本里取映射）
UPDATE item_stats SET genius_id = :item_genius_id WHERE item_pid = :item_pid;
```

校验：

```
item_stats.genius_id 与 item.genius_id 不一致 0 行
```

随后 `fsync` + `Write-VolumeCache <盘符>`，安全弹出、重新插拔让设备重读。

## 5 工具侧已修

所有会写库或部署的入口共用 `tools/genius_stats.py`：

| 工具 | 行为 |
|---|---|
| `custom_graph.py` | 写入后同步；`--check` 报告两表不一致行数 |
| `cluster_align.py` | 生成后同步 |
| `apply_genius_mix_fix.py` | 快照备份后同步 |
| `boundary_matrix.py` | 构造边界容器后同步 |
| `deploy_aligned.py` | 部署前拒绝不一致的源库；写入后 `fsync` + 刷卷缓存 |
| `genius_sim.py` | 不一致时警告；`--strict-stats` 直接失败 |

另外 `genius_metadata` 的 distance 槽位（1/2/3/20）在 `custom_graph.py` 中
改为逐首唯一，避免间隔约束误杀候选。5G 的 `item_stats` 没有 `genius_id` 列，
上述同步自动降级为无操作。

## 6 验证顺序

```bash
python tools/custom_graph.py --config <原厂 config> --out <输出目录> --check
python tools/genius_sim.py --db <输出目录> --all-mixes
# 部署 -> 安全弹出 -> 重新插拔 -> 上机
```

真机判据：Mix 可进入、有曲目、可切歌、有封面。
