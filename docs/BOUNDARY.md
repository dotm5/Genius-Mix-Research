# Genius Mix 边界实测

本文给出真机边界测试的构造方法、矩阵与结论。测试容器由 `tools/boundary_matrix.py` 构造，使用库级 similarities 与原始配置，只改变结构参数。逐轮过程与改动见[真机轮次记录](ROUNDS.md)。

## 1 构造方法

可用的 Genius Mix 容器需要三处记录：

| 位置 | 记录 |
|---|---|
| `Library.itdb` | `container`：`distinguished_kind=32` 为枚举条件；`smart_is_genius` 与 `smart_criteria` 取任意值均可播（第 8 轮） |
| `Library.itdb` | `item_to_container`：`physical_order` 连续 |
| `Dynamic.itdb` | `container_ui`：容器显示状态 |

种子来自 `item_to_container`，`container_seed` 不参与运行期查询。曲目需要非零 `item.genius_id`，并具备非空 `genius_metadata` 与 `genius_similarities`。

## 2 测试矩阵

| 容器 | 成员 | 流派数 | 结果 |
|---|---:|---:|---|
| 基线 C1–C8 | 2–49 | 1–2 | 可播，有封面 |
| B1 | 1 | 1 | 可播，有封面 |
| B2 | 100 | 多 | 可播，有封面 |
| B3 | 189 | 多 | 可播，有封面 |
| B4 | 0 | — | 出现在列表，无法播放 |
| B5 | 60（50 个成员 + 10 行重复） | 多 | 可播，有封面 |
| B6 | 10 | 1 | 可播，有封面 |
| D1 | 40 | 1 | 可播，有封面 |
| D2 | 91 | 7 | 可播，有封面 |
| D3 | 48 | 4+ | 可播，有封面 |
| D4 | 16 | 1 | 可播，有封面 |

合计 17 个非空容器同时存在，全部被枚举并可播。

## 3 边界结论

1. 容器数量：17 个非空容器全部枚举并生成，固件按实际容器数枚举。
2. 成员数量：1 … 189 均可生成，抽样上限 `k = min(n, 100)` 无副作用。
3. 流派跨度：1 … 7 个流派的容器都带 Mix 封面。
4. 空容器：列表照常显示，生成器返回空，`count < minimum`。
5. 重复行：`item_to_container` 的重复行不改变生成结果。
6. 候选池：取库级 similarities；收窄到容器级会让小容器（例如 2 首）低于下限，流派收敛交给 `op=2`。
7. 最小字段集：`smart_criteria`（384B SLst）与 `smart_is_genius` 不参与运行期，容器只需 `container` + `item_to_container` + `container_ui` 三处记录。

## 4 封面修正

第四轮曾观测到 100/189/60 成员的容器没有 Mix 封面，当时归因于流派跨度。第五轮用同一批成员复测，全部有封面。

设备在数据库变更后需要时间生成 Mix 封面，部署后立即查看会看到空白。封面与容器规模、流派跨度无关。

## 5 统计过滤实证

第 9 轮构造封闭候选池：5 首同流派曲目两两互为 similarities，只给最后一首写入跳过统计，`min_num_results` 临时降为 2。

| 曲目 | `item_stats` |
|---|---|
| T1–T4 | 正常统计 |
| T5 | `skip_count_user=5`、`date_skipped=`当前时间、`play_count_user=0` |

从 T1 启动单曲 Genius：生成 4 首，T5 未出现。接受判据的三条同时成立（`skip >= 3`、跳过占比 > 50%、最近 604800 秒内跳过），T5 被 `op=4` 拒绝。

时间基准为 Mac epoch（2001-01-01 00:00:00 UTC）秒数，与 Unix 时间相差 978,307,200 秒；用 Unix 时间写入会让 `date_skipped` 落到 2057 年，`now - date_skipped` 成为大负数，窗口判据放行该候选。详见[格式文档](FORMAT.md) §4。

## 6 复现

```bash
python tools/boundary_matrix.py --dir <itlp 副本目录> --spec <测试规格.json> --apply
python tools/deploy_aligned.py --src <itlp 副本目录> --dev <盘符>
python tools/boundary_matrix.py --dir <itlp 副本目录> --clean
```

测试规格为 JSON 数组，每项形如：

```json
{"name": "D1", "n": 40, "dup": 0, "sel": "genre:Dance"}
```

`sel` 取值：`any`、`genre:<流派名>`、`family:A`、`family:B`。测试容器与基线容器共用曲目，观测结束后用 `--clean` 移除。
