# 回放增益与音量平衡（ReplayGain / Sound Check）

nano 5G/6G 有两条**独立**的回放增益链，均直接写 `Library.itdb`，不需要修改音频文件。
设备端的设置项「音量平衡」（Sound Check）只使用第二条链。

## 1 两条链

| 机制 | 字段 | 换算 | 语义 |
|---|---|---|---|
| ReplayGain（逐曲） | `item.relative_volume` | `rv = clamp(round(1000*(10^(G/20)-1)), -1000, 1000)` | 线性幅度增益；`0` = 不调整；负值衰减，正值提升（上限 +6.02 dB） |
| Sound Check | `avformat_info.volume_normalization_energy` | `X = clamp(round(1000*10^(-G/10)), 0, 65534)` | `X` 是 SoundCheck 值；`0` = 无数据/等同 `1000`；`1000` = 0 dB；`X>1000` 衰减，`X<1000` 提升 |

`G` 是目标增益（dB），负值表示曲目偏响、需要衰减。

### 1.1 文件标签

- 固件**不读**文件内的 `iTunNORM` / `REPLAYGAIN_*` 标签；MP4 元数据读取器只接受
  `iTunSMPB`。
- 宿主 iTunes/Music 会读 `iTunNORM` 并生成设备 DB 里的 Sound Check 数据；
  若宿主从未分析过曲目，设备端 `vne=0`，开关不会有听感变化。
- Apple 用户指南明确：iTunes 未打开 Sound Check 时，只在设备端打开没有效果。

## 2 音量平衡设置影响什么

```text
设置 → 音乐 → 音量平衡
  Playback.SoundCheck (iPodSettings.xml)
    -> 固件读取并写入播放器对象
    -> 换曲/切换时按 avformat_info.volume_normalization_energy 计算增益
```

- 设置本身有读取者，不是无效开关。
- 它**不读** `item.relative_volume`；改 ReplayGain 的 `relative_volume` 不会让
  音量平衡开关生效。
- `vne=0` 时增益为 1.0（无变化）；这是「打开了没效果」的常见原因。

## 3 部署

### 3.1 生成专辑级 ReplayGain 缓存

```bash
python tools/apply_album_replaygain.py --dev I: --dry-run --resume --jobs 4
```

- 把整张专辑 concat 后跑 ffmpeg `replaygain`（EBU R128 / ReplayGain 2.0，
  参考 −18 LUFS），得到专辑 gain/peak。
- 结果增量写入 `out/replaygain_albums.json`；`--dry-run` 只扫描并写缓存，不改设备。
- 缓存条目带「文件名 + 大小」指纹；曲目集合变化的专辑会自动重扫。

### 3.2 写入音量平衡（推荐）

```bash
python tools/apply_soundcheck_from_replaygain.py --dev I: --apply
```

- 从缓存计算 `X = round(1000 * 10^(-gain_db/10))`，写
  `avformat_info.volume_normalization_energy`；同一专辑所有曲目同值。
- 默认同时清零 `item.relative_volume`（旧 ReplayGain 链），避免两条链叠加。
- 写入前自动备份全部 `.itdb` / `.cbk`，写后校验并刷盘。
- 完成后安全弹出 → 重启，播放中切换「音量平衡」验证。

### 3.3 旧机制：直接写 relative_volume

```bash
python tools/apply_album_replaygain.py --dev I: --apply --resume --jobs 4
```

只写 `item.relative_volume`，与音量平衡开关无关；适合不需要 Sound Check 的场景。

## 4 真机结论

在测试库全量写入 vne、并清零 `relative_volume` 后：

- 播放中切换「设置 → 音乐 → 音量平衡」，**音量差异明显**；
- 证明 `Playback.SoundCheck` 读取路径、`volume_normalization_energy` 消费者与
  `X=1000*10^(-G/10)` 公式在本机成立；
- 之前的「无效果」只因 vne=0，不是设置失效。

## 5 同步注意

- iTunes 同步可能覆盖 `avformat_info` / `item.relative_volume`；同步后重跑 3.2。
- 设备设置里的音量平衡需保持开启。
- 两条链不要同时部署；推荐 Sound Check（`vne`），并把 `relative_volume` 清零。

## 6 参考

- libgpod `src/itdb.h` `@soundcheck`：`X = 1000 * 10^(-0.1 * Y)`；`0` 特殊，等同 `1000`。
- podkit `normalization.ts`：`replayGainToSoundcheck = round(1000 * 10^(gainDb/-10))`。
- reupen/ipod_manager `itunesdb_track.cpp`：`1000 * pow(10, -0.1 * db_adjustment)`。
- Apple `ITLibMediaItem.volumeNormalizationEnergy`；iPod nano 6G 用户指南（Sound Check）。
- ID3.org「iTunes Normalization settings」：iTunNORM 的 0/1 对为音量调整值，6/7 对为 peak。
