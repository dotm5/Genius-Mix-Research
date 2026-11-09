# nano 6G：由原厂 iTunes 同步自建 Genius Mix

范围：Windows iTunes 12.13.11.1、nano 6G 固件 1.2、27 首实验曲目；这是对该组合的实验记录，不是适用于其他版本或设备的兼容性保证。设备侧所有写入由未修改的 iTunes 完成，不通过设备库补丁维持 Mix；宿主资料库与 Genius 数据仍需事先构造。

## 原因与数据路径

直接写入设备的 kind=32 容器及图不是 iTunes 宿主资料库的权威内容，下一次同步可能被覆盖。该版本的 iTunes 会从宿主 `.itl` 的 `msdh` type `0x0e` 投影 Mix：kind=35 父容器、kind=32 子容器、关联成员与 `mhoh` type 104 中的种子 PID；曲目的 Genius ID 与宿主配对的 `iTunes Library Genius.itdb` 中的 metadata、similarities 相互对应。iTunes 不需要被 hook 或修改。

本机 IDA 定位的 track 字段为 `.itl` 解码后的 `mith+0x1e4` 64 位 Genius ID 和 `mith+0x1f0` 32 位 Genius 变更标记。仅创建有效 Mix/种子并填入 Genius ID 时，iTunes 曾写出容器，但新增曲目的设备 Genius 图仍为空；对相同图设置逐曲非零标记后，iTunes 写出对应图。实验用 `CRC32(metadata_blob || similarities_blob)`，结果为零则使用 1；这是**实验性变更标记，不是已还原的 Apple checksum 算法**，也不能消除碰撞。

## 实机结果

- 单 Mix 实验：iTunes 写出 kind=32、种子与 27/27 metadata/similarities 行；退出重启 iTunes、重复同步后保留。用户在 nano 6G 确认 Mix 播放、更新和封面正常，并确认每首曲目都能生成单曲 Genius 队列。
- 后续两个 Mix、两个种子和 27 行图跨重启及重复同步保留；两组是同曲目集，不等于不同曲目集均已验证。测试图稠密，不能用于判断推荐质量。
- 将宿主一行相似候选交换并更新该曲标记，iTunes 同步出新行；随后以最新宿主库为基准恢复该行并同步，设备对应 blob 逐字节恢复。同期用户真实 3 星评分、两个 Mix 和 Sound Check 值保留。
- 最终快照中曲目、容器、成员、种子、图无增删；主曲库有 16 个 physical_order 重排，两个 Mix 成员不变。设备评分 3 星从设备回写宿主，重启后保留。

## 可运行部分：暂存逐曲变更标记

`tools/stage_itunes_revisions.py` 使用 Python 标准库及 Windows 自带 CNG，读取**已复制的** `.itl` 与匹配的**已解密宿主 Genius SQLite 副本**，检查图行及 ID 并输出一个尚不存在的新 `.itl`。只重写解码后的逐曲 revision 字段，输出会被重新解码并逐字节核验。工具不读取设备、不写入音频、不部署资料库、不创建 Mix、也不解密或更新宿主 Genius 库；上游解密和建图流程目前未作为完整工具发布。本工具不是一键原生同步方案。

```powershell
python tools/stage_itunes_revisions.py --itl <copied-library.itl> `
    --genius-plain <matching-decrypted-genius.sqlite> --out <new-staged-library.itl>
```

退出 iTunes 后再获取一致的宿主 `.itl` / Genius 数据库副本，提供正确配对的解密 SQLite；错误配对可能通过结构检查，不能仅凭脚本成功就部署。先备份完整宿主资料库、Genius 数据及媒体；数据库快照不是完整媒体备份。实际替换、选择同步及设备验证必须另外审计。不要把旧设备快照整体覆盖到当前资料库；那会丢失新曲、评分或更新。`--out` 已存在时工具拒绝覆盖。

独立复现：针对本次试验的输入副本，新实现生成的 `.itl` 与之前用于复现成功同步的暂存文件逐字节相同。尚未用本仓库代码生成的文件重新进行独立真机同步；不能把逐字节对照扩大为其他数据集的实机结论。

## 其他原生读写与局限

- Sound Check：宿主读取实验媒体副本中的 `iTunNORM` 后，27 首对应设备响度值经原生同步保留；设备设置开启。固件有消费链，本轮用户没有听出显著差异，尚无受控听感 A/B。直接只改宿主 `.itl` 中缓存值并未触发设备更新。
- EQ：宿主曲目选项中的“低音增强”经 iTunes 写到设备并跨同步保留；音色差异未通过本轮受控 A/B。
- 宿主对评分、播放/跳过次数、书签及记住位置的受控更改可下发；实际设备评分回写成功，但一次播放后切歌没有增加播放或跳过计数。不能由 SQL 或手动写值推断日常自动计数或推荐自学习；评分不在已定位 Genius 统计查询中。
- 宿主中英文排序元数据和中文流派可同步；设备 order 由 iTunes 重算，不保证等同旧工具的拼音排序策略。
- 曲目新增、删除、重新导入、PID 重映射、并发评分/书签冲突、大型图、不同曲集的 Mix 和其他版本均未闭环。设备 CDB / `.cbk` 的 HASHAB 签名本轮因缺少验证依赖而未验证。
