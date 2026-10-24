#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genius 数据库一致性辅助。

第 6 代把 Genius 的持久标记放在 ``Dynamic.itdb.item_stats.genius_id``；
它必须与 ``Library.itdb.item.genius_id`` 逐首一致。只改前者而不同步后者时，
设备能枚举 Mix 容器，但按 stats 解析不到可播放条目，表现为「歌单能打开但为空 /
所选歌曲不可播放」。第 5 代没有这一列，所有函数自动降级为无操作。

本模块只依赖标准库，供 tools/ 下的生成器、部署器与模拟器共用。
"""
import os
import subprocess
import sys


def has_genius_id(dynamic):
    """Dynamic.itdb 的 item_stats 是否有 genius_id 列（6G 有，5G 无）。"""
    return "genius_id" in [r[1] for r in dynamic.execute("pragma table_info(item_stats)")]


def item_ids(library):
    return {r[0]: r[1] for r in library.execute("select pid, genius_id from item")}


def stats_ids(dynamic):
    return {r[0]: r[1] for r in dynamic.execute(
        "select item_pid, genius_id from item_stats")}


def mismatches(library, dynamic):
    """返回 [(item_pid, item_genius_id, stats_genius_id)]；5G 无列时为空。"""
    if not has_genius_id(dynamic):
        return []
    item = item_ids(library)
    stats = stats_ids(dynamic)
    return [(p, g, stats.get(p)) for p, g in item.items() if stats.get(p) != g]


def sync(library, dynamic):
    """把 item_stats.genius_id 同步成 item.genius_id。

    返回 ``(has_column, updated, zeroed, remaining)``：
    has_column 为 False 表示 5G（无该列），其余为 0。
    """
    if not has_genius_id(dynamic):
        return False, 0, 0, 0
    item = item_ids(library)
    stats = stats_ids(dynamic)
    updates = [(g, p) for p, g in item.items() if stats.get(p) != g]
    extras = [(p,) for p, g in stats.items() if p not in item and g != 0]
    dynamic.executemany("update item_stats set genius_id=? where item_pid=?", updates)
    dynamic.executemany("update item_stats set genius_id=0 where item_pid=?", extras)
    now = stats_ids(dynamic)
    remaining = sum(1 for p, g in item.items() if now.get(p) != g)
    return True, len(updates), len(extras), remaining


def describe(library, dynamic):
    """返回一行可打印的同步结果说明。"""
    has_col, updated, zeroed, remaining = sync(library, dynamic)
    if not has_col:
        return "item_stats 无 genius_id 列（5G），跳过同步"
    return "item_stats.genius_id 同步：写入 %d 行，清零 %d 行，剩余不一致 %d 行" % (
        updated, zeroed, remaining)


def warn_if_inconsistent(library, dynamic, label="数据库"):
    """只检查不写入；返回不一致行数，并在 stderr 给出提示。"""
    bad = mismatches(library, dynamic)
    if bad:
        print("[警告] %s：item_stats.genius_id 与 item.genius_id 不一致 %d 行。"
              "6G 上会导致 Mix 出现但为空/不可播放，请先同步后再部署。"
              % (label, len(bad)), file=sys.stderr)
    return len(bad)


def fsync_file(path):
    """把单个文件的页缓存刷到介质；失败返回 False。"""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        os.fsync(fd)
        return True
    finally:
        os.close(fd)


def flush_volume(drive):
    """Windows 上刷卷缓存；其他平台或失败返回 False（不抛异常）。"""
    if os.name != "nt":
        return False
    letter = (drive or "").strip().rstrip(":\\/")[:1]
    if not letter:
        return False
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Write-VolumeCache -DriveLetter %s" % letter],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
