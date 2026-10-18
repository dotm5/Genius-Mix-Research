#!/usr/bin/env python3
"""iPod nano 6G 合成 Genius Mix 修复：保留容器，改写约束键与最小配置。

默认使用 108B cfg_limits；48B cfg_empty 供回溯已有消融结论。
全部配置从公开的整数布局构造。

保留原修复工具的同容器步长 1/5/9/13 构造方法：每行 32B = 4×u64。
后续记录已更正：这些 u64 是约束键；真正的候选图来自 similarities。
输入前提：已有非空 similarities；容器与曲目 ID 由同步软件维护。
"""

import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import struct


# 历史变体的 44B 公共后缀。早期切片起于 random_jitter 的参数，
# 承载原始实测的 48/108B 字节序列。
# 解释器将前两个 u32 作为一个 u64，再读取一个 u32；剩余字节保留。
COMPAT_SUFFIX = (70, 100, 1, 20, 50, 10, 10, 0, 0, 10, 20)


def build_config(mode="limits"):
    """从零构造历史最小互操作性配置，所有整数均为小端 u32。

    cfg_limits：4B 条目数 + 3×20B 约束 + 44B 后缀 = 108B。
    两条 distance 分别使用字段 2/3；第三条是 skip_count 统计过滤。
    cfg_empty：4B 零条目数 + 44B 同一后缀 = 48B。
    “三条限额”是历史简称，精确语义为两条间隔约束加一条统计过滤。
    """
    if mode == "limits":
        words = (3, 3, 2, 2, 10, 6, 3, 3, 3, 10, 6,
                 4, 2, 604800, 0, 50) + COMPAT_SUFFIX
    elif mode == "empty":
        words = (0,) + COMPAT_SUFFIX
    else:
        raise ValueError("未知配置模式")
    return struct.pack("<%dI" % len(words), *words)


def connect(path, writable=False):
    """按可写/只读模式打开已有数据库。"""
    return sqlite3.connect(path.resolve().as_uri() +
                           ("?mode=rw" if writable else "?mode=ro"), uri=True)


def backup_databases(db_dir, backup_root):
    """使用 SQLite 备份接口保存一致快照，兼容存在日志页的数据库。

    备份含私人曲库数据，必须位于仓库之外；内容为 SQLite 快照。
    每次建立独立时间目录，失败时保留已完成的备份供人工检查。
    """
    destination = backup_root / datetime.now().strftime("applied-%Y%m%d-%H%M%S-%f")
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(db_dir.glob("*.itdb")):
        with closing(connect(path)) as source:
            with closing(sqlite3.connect(destination / path.name)) as target:
                source.backup(target)
    return destination


def prepare_updates(library, genius):
    """按现有 Kind-32 容器构造 32B 约束键，全部检查先于写入。

    对 N 首成员，以 (k+1/5/9/13) mod N 取四个合法 genius_id。
    小容器允许重复键；候选可达性由 similarities 图提供。
    交叠成员直接拒绝：同一曲目跨容器会互相覆盖约束键。
    其余 metadata 沿用原工具的全库池回退方法。
    """
    ids = [row[0] for row in genius.execute(
        "SELECT genius_id FROM genius_metadata ORDER BY genius_id")]
    if not ids or any(not isinstance(g, int) or not 0 < g < 2**64 for g in ids):
        raise ValueError("metadata 必须已有非零、可编码为 u64 的曲目 ID")
    available = set(ids)
    playable = {row[0] for row in library.execute(
        "SELECT genius_id FROM item WHERE genius_id != 0")}
    if not available <= playable:
        raise ValueError("metadata 含不对应 Library 曲目的 ID，请先清理孤儿行")
    containers = library.execute(
        "SELECT pid FROM container WHERE distinguished_kind=32 ORDER BY pid").fetchall()
    if not containers:
        raise ValueError("没有现成的 Kind-32 容器；本工具只修复已有 Mix")
    membership = {}
    for (pid,) in containers:
        members = [g for (g,) in library.execute(
            "SELECT i.genius_id FROM item i JOIN item_to_container t "
            "ON t.item_pid=i.pid WHERE t.container_pid=? "
            "ORDER BY t.physical_order", (pid,))]
        if len(members) < 2 or len(set(members)) != len(members):
            raise ValueError("每个现有 Mix 至少需要两个具有不同 genius_id 的成员")
        if not set(members) <= available:
            raise ValueError("容器成员含零 ID 或缺少 metadata 的曲目")
        for gid in members:
            if gid in membership:
                raise ValueError("存在跨 Mix 复用成员，约束键会互相覆盖，请先拆分成员")
            membership[gid] = members
            row = genius.execute(
                "SELECT data FROM genius_similarities WHERE genius_id=?", (gid,)).fetchone()
            if not row or not row[0]:
                raise ValueError("成员缺少 similarities 候选数据，请先由同步软件重建相似度图")
    config = genius.execute("SELECT data FROM genius_config WHERE id=1").fetchone()
    if config is None:
        raise ValueError("缺少 id=1 的 genius_config 行")
    updates = []
    for gid in ids:
        members = membership.get(gid, ids)
        k = members.index(gid)
        neighbors = [members[(k + step) % len(members)] for step in (1, 5, 9, 13)]
        updates.append((struct.pack("<4Q", *neighbors), gid))
    return updates


def main():
    """显式指定库目录及仓库外备份位置，成功后安全弹出并重启设备。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_dir", type=Path, help="iTunes Library.itlp 目录")
    parser.add_argument("--backup-dir", type=Path, required=True, help="仓库外的私人备份目录")
    parser.add_argument("--config", choices=("limits", "empty"), default="limits",
                        help="默认 limits=108B；empty=48B")
    args = parser.parse_args()
    db_dir, backup_root = args.db_dir.resolve(), args.backup_dir.resolve()
    repo = Path(__file__).resolve().parents[1]
    if backup_root == repo or repo in backup_root.parents:
        parser.error("私人数据库备份必须放在仓库之外")
    if backup_root == db_dir or db_dir in backup_root.parents:
        parser.error("备份目录必须独立于输入数据库目录")
    for name in ("Library.itdb", "Genius.itdb"):
        if not (db_dir / name).is_file():
            parser.error("缺少必要数据库：" + name)
    with closing(connect(db_dir / "Library.itdb")) as library:
        with closing(connect(db_dir / "Genius.itdb", writable=True)) as genius:
            updates = prepare_updates(library, genius)
            backup = backup_databases(db_dir, backup_root)
            # 备份成功后，在单一事务内同时改写 metadata 和配置。
            # Library 和 Dynamic 保持既有关系；stats 的 ID 同步属于输入前提。
            config = build_config(args.config)
            with genius:
                genius.executemany(
                    "UPDATE genius_metadata SET data=? WHERE genius_id=?", updates)
                genius.execute("UPDATE genius_config SET data=? WHERE id=1", (config,))
    print("备份：", backup)
    print("metadata：%d 行；配置：%dB" % (len(updates), len(config)))
    print("完成。安全弹出 → 重启 iPod → 检查 Genius Mix 实际播放。")


if __name__ == "__main__":
    main()
