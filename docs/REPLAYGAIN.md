# 回放增益（ReplayGain / Sound Check）

本文给出 nano 6G 的回放增益支持范围：文件标签与设备数据库两条路径的字段、换算与写入方式。

## 1 支持范围

| 路径 | 支持 | 依据 |
|---|---|---|
| ALAC/M4A 内的 `iTunNORM` 标签（`com.apple.iTunes` freeform） | 否 | 读路径只接受 `iTunSMPB` |
| `item.relative_volume`（`Library.itdb`） | 是 | 实机 −1000 测试曲全程显著降音量 |
| 设备设置项 Sound Check | 无效 | 设置槽无读取者，且固件不通告 `SupportsSoundCheck` |

## 2 `iTunNORM` 的处理路径

- 全镜像字节扫描：`iTunNORM` 字符串出现 1 处，位于一份未被引用的 MP4 原子模板中（`ilst` → `----` → `mean=com.apple.iTunes` → `name=iTunNORM` → `data`，值形如 10 段 `%08X`，长度 90 字节）；对模板起始地址做指针扫描为 0 命中。
- 全部 36 个字符串比较点中，没有一处比较 `iTunNORM`。
- MP4 元数据读取器只接受 `name == "iTunSMPB"`（无缝播放信息）。

模板所在区域同时包含通用 freeform 原子构造器与 `writeToBuffer` 日志串，属于写入侧代码；读路径指向 `iTunSMPB`。

## 3 `item.relative_volume`

### 3.1 语义

字段由 item 查询读取（`SELECT … relative_volume, eq_preset, genre_id … FROM item WHERE pid=?1`），并在播放时应用。取值换算沿用 iTunes 侧写入器：

```text
relative_volume = 1000 * vol16 / 255        vol16 ∈ [-255, 255]
```

| 值 | 线性增益 | 说明 |
|---:|---:|---|
| −1000 | 0.0× | 静音档（实测显著降音量） |
| 0 | 1.0× | 不调整 |
| +1000 | 2.0× | +6.02 dB，上限 |

即 `gain_factor = 1 + relative_volume / 1000`，可调范围 −∞ … +6.02 dB。

### 3.2 写入

```sql
UPDATE item SET relative_volume = ? WHERE pid = ?;
```

写入后安全弹出并重启，设备在挂载时缓存数据库。单条测试曲用 −1000 可做二元判定：音量明显下降即字段生效。

### 3.3 由响度计算增益

```python
# target_lufs: -18 (ReplayGain 2.0 / EBU R128 参考) 或 -16
# true_peak_db: 积分响度分析得到的真峰值
gain_db = min(target_lufs - integrated_lufs, -1.0 - true_peak_db)
rv = round(1000 * (10 ** (gain_db / 20) - 1))
rv = max(-1000, min(1000, rv))
```

响度分析用 `ffmpeg -af loudnorm=print_format=json` 或 `ebur128`；真峰值约束避免削波，+6 dB 上限由字段范围决定。

## 4 其它相关字段

| 字段 | 位置 | 说明 |
|---|---|---|
| `avformat_info.volume_normalization_energy` | `Library.itdb` | u16，由 iTunes 原样写入 |
| `item_stats.play_count_user` / `skip_count_user` | `Dynamic.itdb` | 供 `op=4` 使用，不参与增益 |
| `item_stats.user_rating` / `bookmark_time_ms` / `*_recent` / `*_original` | `Dynamic.itdb` | 设备有查询读取，Genius 链不使用 |

## 5 同步行为

固件从不通告 `SupportsSoundCheck`，宿主可能据此判定设备不支持，同步后把 `relative_volume` 重置为 0：修改后避免再次同步，或同步后重跑写入。设备设置界面中的 Sound Check 开关没有对应读取者，切换它不改变行为。
