# 真机测试轮次记录

本文按轮次记录 iPod nano 6G（N20）上的验证过程。每轮只改变一个变量，改动前对全部 `.itdb` 做 SQLite 快照，改完安全弹出并重启后再观察。前三轮对应格式解码与消融实验，结论见[格式文档](FORMAT.md)与[对齐文档](ALIGN.md)；本文覆盖第 4 轮起的结构性验证。

## 1 每轮不变量

| 项 | 值 |
|---|---|
| `genius_config.data` | 原始 151,212B；第 6 / 7 / 9 轮只改 `default_num_results` 与 `min_num_results` 两列 |
| `genius_metadata.data` | 168B（21 个 u64） |
| `genius_similarities.data` | 库级候选池 |
| 容器 | `distinguished_kind=32`，成员通过 `item_to_container` 关联 |

## 2 第 4 轮：容器级候选池与边界容器

改动：similarities 收窄到容器级；新增 5 个合成容器（1 / 100 / 189 / 0 / 60 + 重复）。

| 容器 | 成员 | 观察 |
|---|---:|---|
| C1–C6 | 16–49 | 可播 |
| C7 | 8 | 可播，生成 8 首 |
| C8 | 2 | 不可播 |
| B1 | 1 | 可播 |
| B2 / B3 / B5 | 100 / 189 / 60 | 可播，当时未见 Mix 封面 |
| B4 | 0 | 出现在列表，不可播 |

容器级候选池让 2 首的容器低于下限；空容器是可见条目，生成器返回空。

## 3 第 5 轮：库级候选池与 17 容器

改动：候选池改为库级；新增 D1–D4，与 B 系列合计 17 个非空容器。

| 观察 | 结论 |
|---|---|
| 17 个非空容器全部出现并可播 | 容器数量：本机型固件按实际容器数枚举并生成 |
| 成员数覆盖 1 … 189 | 成员数量无限制；抽样上限 `k = min(n, 100)` 无副作用 |
| 流派覆盖 1 … 7 | 流派跨度不影响可播性 |
| 17 个全部带 Mix 封面 | 第 4 轮的无封面是生成延迟：设备在数据库变更后需要时间生成封面 |
| C8 恢复可用 | 第 4 轮 C8 的失败来自候选池过窄 |

## 4 第 6 轮：阈值正向验证

改动：`default_num_results=50`、`min_num_results=5`。

| 观察 | 结论 |
|---|---|
| Dance 曲目启动单曲 Genius，生成 50 首 | target 取自 `default_num_results` |
| K-Pop 曲目启动，同样 50 首（其容器仅 8 首） | 候选池为库级，容器大小不限制歌单长度 |
| 8 个 Mix | 长度不受这两列影响 |

## 5 第 7 轮：阈值反向验证

改动：`default_num_results=50`、`min_num_results=100`。

三首 Dance 曲目的单曲 Genius 全部不可用：歌单在 target 50 处停止，`50 >= 100`，可用判据取反，返回 unplayable。

两条阈值由此闭合：target = `default_num_results`，minimum = `min_num_results`。

## 6 第 8 轮：最小字段集与 Mix 队列长度

| 改动 | 观察 |
|---|---|
| C1（49 首）`smart_criteria = NULL` | 可播 |
| C8（2 首）`smart_criteria = NULL` | 可播 |
| C7（8 首）`smart_is_genius = 0` | 可播 |

结论：`smart_criteria`（384B SLst）与 `smart_is_genius` 不参与 Genius Mix 运行期，枚举条件为 `distinguished_kind=32`。最小容器记录为 `container` + `item_to_container` + `Dynamic.itdb.container_ui` 三处。

同一轮把一个 Mix 连续播到结束：队列长度 50 首，开启 shuffle 后播完回到第一首。初始生成目标为 10，播放期间由 `NextMixTrackCommand` 补货至队列容量，机制见[格式文档](FORMAT.md) §5。

## 7 第 9 轮：op=4 统计过滤

构造封闭候选池：5 首同流派曲目两两互为 similarities，只给最后一首写入跳过统计；`min_num_results` 临时降为 2，使短歌单可用。

| 曲目 | `item_stats` |
|---|---|
| T1–T4 | 正常统计 |
| T5 | `skip_count_user=5`、`date_skipped=`当前时间、`play_count_user=0` |

从 T1 启动单曲 Genius：生成 4 首，T5 未出现。接受判据的三条同时成立（`skip >= 3`、跳过占比 > 50%、最近 604800 秒内跳过），T5 被 `op=4` 拒绝。

时间基准：`item_stats` 的时间列是 Mac epoch（2001-01-01 00:00:00 UTC）秒数，与 Unix 时间相差 978,307,200 秒。首次尝试用 Unix 时间写入，`date_skipped` 落到 2057 年，`now - date_skipped` 成为大负数，窗口判据放行该候选；改用 Mac epoch 后复现过滤效果。可用既有 `date_played` 值校验基准，见[格式文档](FORMAT.md) §4。

## 8 第 10 轮：`--keys` 粒度

改动：`--keys coarse`，即 `metadata[1..3]` 取 8 / 12 / 16 个值循环，distance 约束最强。

8 个容器全部可播。键池最小的档位同样不会被间隔约束耗尽，库级候选池的供给量高于约束消耗；默认仍取 `mixed`。

## 9 汇总

| 机制 | 结论 | 轮次 |
|---|---|---|
| 原始配置 + 168B metadata | 可用 | 3、4、5 |
| `op=2` 簇白名单 | 随簇映射生效 | 3、5 |
| `op=1/3` distance（三档粒度） | 均可用 | 5、10 |
| `op=5` random_jitter | 随配置参与 | 3 |
| `op=4` 统计过滤 | 生效（`skip`、占比、604800 秒窗口） | 9 |
| target = `default_num_results` | 置 50 时生成 50 首 | 6 |
| minimum = `min_num_results` | 置 100 时全部不可用 | 7 |
| Mix 队列长度 | 50 首 | 8 |
| 容器数量 / 成员数 / 流派跨度 | 17 容器、1 … 189 成员、1 … 7 流派均可播 | 5 |
| `smart_criteria` / `smart_is_genius` | 运行期不读取 | 8 |
| 数据保留 | 三张表无时间列，按需生成，不随时间失效 | — |

## 10 复现注意

1. 每轮改动后安全弹出并重启：设备在挂载时缓存数据库。
2. Mix 封面有生成延迟，部署后立即查看会看到空白（第 4 轮）。
3. 写入 `item_stats` 时间列时使用 Mac epoch，窗口判据依赖该基准（第 9 轮）。
4. 测试容器与基线容器共用曲目，观测结束后用 `--clean` 移除。
