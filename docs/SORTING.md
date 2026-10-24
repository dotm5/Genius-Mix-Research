# 歌曲 / 艺术家 / 专辑排序字段

设备列表顺序由 `Library.itdb:item` 的 `*_order` 整数列决定，`sort_*` 是排序用的字符串。
导入工具常把 `title_order` 写成导入序号（100、200、…），于是「歌曲」按导入顺序显示，
而不是按标题；本项目的重建工具会把这些字段全部重算。

## 1 固件读取点

设备 SQL 直接对 `title_order` 排序：

```sql
SELECT item.pid, item.album_pid, item_stats.has_been_played
FROM item, item_stats
WHERE item.pid = item_stats.item_pid AND item.media_kind & ?1
ORDER BY item.title_order;

CREATE INDEX item_title_order_idx ON item (title_order);
CREATE INDEX item_artist_sort_order_idx
  ON item (artist_order, album_order, disc_number, track_number, sort_title, physical_order);
CREATE INDEX item_album_order_idx
  ON item (album_order, disc_number, track_number, artist_order, sort_title, physical_order);
```

依赖表同样有独立排序键：

| 表 | 字段 | 来源 |
|---|---|---|
| `item` | `title_order` / `artist_order` / `album_order` / `album_artist_order` / `genre_order` / `composer_order` | 逐项 100×排名 |
| `track_artist` | `name_order` / `sort_name` | 与 `item.artist_order` / `sort_artist` 一致 |
| `artist` | `name_order` / `sort_name` | 与 `item.album_artist_order` / `sort_album_artist` 一致 |
| `album` | `name_order` / `sort_order` / `artist_order` / `sort_name` | `item.album_order` 与专辑艺术家顺序 |
| `genre_map` | `genre_order` | `item.genre_order` |
| `composer` | `name_order` / `sort_name` | 当前导入库未建行，见 §5 |

`item.album_by_artist_order` 在原厂基线里为 `NULL`，重建时保持 `NULL`。

## 2 原厂排序风格

`title_order` 的取值是 `100 × 排名`；相同排序键共享同一个值。顺序为：

1. **拉丁字母 A-Z**，大小写不敏感，忽略开头的 `The` / `A` / `An`；
2. **汉字按拼音**；假名 / 谚文与汉字同处 CJK 段；
3. **数字**；
4. 其它符号。

原厂基线（189 首）验证：`A Sul Hae` 排第一，`The One That Got Away` 的 `sort_title`
是 `One That Got Away`，排在 `One` 附近；`1M / 360 / 365` 排在最后。
`sort_title` 保存的是去掉冠词后的显示字符串，`title` 本身不改。

汉字拼音排序使用 Windows `LCMapStringEx`：

```text
locale   = zh-CN
flags    = LCMAP_SORTKEY | SORT_CHINESE_PRC
```

它同时处理汉字（拼音）、假名和谚文；不依赖第三方 Python 包。

## 3 重建工具

```bash
# 预览（不写库）
python tools/fix_sort_orders.py --dev I: --dry-run

# 写库（自动快照 + 刷盘）
python tools/fix_sort_orders.py --dev I: --apply

# 保留 The/A/An 开头（不省略冠词）
python tools/fix_sort_orders.py --dev I: --apply --keep-articles
```

工具会同时更新 `item`、`track_artist`、`artist`、`album`、`genre_map`，
并在写入前把全部 `.itdb` / `.cbk` 备份到 `artifacts/snapshots/devbk/sortfix-<时间>`
（CN 仓库无 `artifacts/` 时退回 `out/devbk`）。写完后安全弹出、重新插拔让设备重读。

## 4 校验

- `title_order` 首项应为 A/B/C 开头，而不是导入顺序的首项；
- `item.artist_order == track_artist.name_order`；
- `item.album_artist_order == artist.name_order`；
- `item.album_order == album.sort_order`；
- `item.genre_order == genre_map.genre_order`；
- `album.artist_order == artist.name_order`；
- `item.album_by_artist_order` 全为 `NULL`；
- `pragma integrity_check` 为 `ok`。

## 5 已知边界

- `composer` 表在当前导入库中为 0 行，`item.composer_pid` 全为 0；`item.composer_order`
  已按作曲者文本重算，但设备「作曲者」菜单不会出现条目。需要时另行建行并回填
  `composer_pid`。
- 重复标题共享同一个 `title_order`，设备按后续稳定键排列（通常是 `pid` / `physical_order`）。
- 排序字段只影响显示顺序，不影响播放、Genius 或 `relative_volume`。
