#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ReplayGain 专辑缓存切换成 Sound Check（音量平衡）部署。

- 读取 out/replaygain_albums.json 的专辑增益 `gain_db`；
- SoundCheck 值：X = clamp(round(1000 * 10^(-gain_db/10)), 0, 65534)；
  0 为特殊值（无数据，等同 1000）；1000 = 0 dB；
- 写入 Library.itdb:avformat_info.volume_normalization_energy，同一专辑所有曲目同值；
- 同时把之前写入的 Library.itdb:item.relative_volume 清零；
- 写入前备份全部设备 itdb，写后校验并刷盘。

用法:
    python tools/apply_soundcheck_from_replaygain.py --dev I: --dry-run
    python tools/apply_soundcheck_from_replaygain.py --dev I: --apply
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from genius_stats import fsync_file, flush_volume

ITLP = os.path.join('iPod_Control', 'iTunes', 'iTunes Library.itlp')
DEFAULT_CACHE = os.path.join('out', 'replaygain_albums.json')


def x_from_gain(gain_db):
    x = int(round(1000.0 * (10.0 ** (-gain_db / 10.0))))
    if x <= 0:
        x = 1000
    return min(65534, x)


def x_from_rv(rv):
    """Peak-capped 条目：从已生效的 relative_volume 反推同一增益的 X。"""
    factor = 1.0 + rv / 1000.0
    if factor <= 0:
        return 65534
    return min(65534, max(1, int(round(1000.0 / (factor * factor)))))


def album_key(aa, album):
    return (aa or '') + chr(0) + (album or '')


def main():
    ap = argparse.ArgumentParser(description='ReplayGain 专辑缓存 -> Sound Check 部署')
    ap.add_argument('--dev', default='I:', help='设备盘符，默认 I:')
    ap.add_argument('--db', help='iTunes Library.itlp 目录（优先于 --dev）')
    ap.add_argument('--cache', default=DEFAULT_CACHE, help='ReplayGain 专辑缓存 JSON')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--keep-relative-volume', action='store_true',
                    help='不清零 item.relative_volume（默认清零）')
    ap.add_argument('--backup-dir', default=None)
    a = ap.parse_args()

    if a.db:
        lp = a.db
    else:
        dev = a.dev
        if len(dev) == 2 and dev[1] == ':':
            dev += os.sep
        lp = os.path.join(dev, ITLP)
    if not os.path.isdir(lp):
        raise SystemExit('设备目录不存在: %s' % lp)

    cache = json.load(open(a.cache, encoding='utf-8'))
    lib = sqlite3.connect(os.path.join(lp, 'Library.itdb'))
    rows = lib.execute('''select pid, coalesce(nullif(album_artist,''),artist),
                                 album, relative_volume from item''').fetchall()
    groups = {}
    for pid, aa, album, rv in rows:
        groups.setdefault(album_key(aa, album), []).append(pid)

    missing = [k for k in groups if k not in cache or 'gain_db' not in cache[k]]
    if missing:
        print('缺少缓存专辑 %d 张:' % len(missing))
        for k in missing[:20]:
            print('  ', k.replace(chr(0), ' / '))
        raise SystemExit('请先补全 out/replaygain_albums.json')

    x_by_key = {}
    for key, pids in groups.items():
        ent = cache[key]
        x = x_from_rv(ent['rv']) if ent.get('capped') else x_from_gain(ent['gain_db'])
        x_by_key[key] = x

    xs = sorted(x_by_key.values())
    print('设备=%r  曲目=%d  专辑=%d  缓存=%d' % (lp, len(rows), len(groups), len(cache)))
    print('SoundCheck X: min=%d median=%d max=%d  (0=无数据, 1000=0 dB)'
          % (xs[0], xs[len(xs) // 2], xs[-1]))
    print('gain_db: %.2f .. %.2f dB' % (
        min(cache[k]['gain_db'] for k in groups),
        max(cache[k]['gain_db'] for k in groups)))
    print('\n示例（专辑 / gain / X / 曲目数）:')
    for key in sorted(groups, key=lambda k: cache[k]['gain_db'])[:8]:
        print('  %-42s %+7.2f dB  X=%5d  n=%d'
              % (key.replace(chr(0), ' / ')[:42], cache[key]['gain_db'],
                 x_by_key[key], len(groups[key])))

    # 校验 avformat_info 行覆盖
    have = {r[0] for r in lib.execute('select item_pid from avformat_info')}
    lack = [pid for pids in groups.values() for pid in pids if pid not in have]
    if lack:
        raise SystemExit('有 %d 个 item 缺少 avformat_info 行，拒绝写入' % len(lack))

    if not a.apply or a.dry_run:
        print('\n(dry-run: 未写入；加 --apply 写入)')
        return

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if a.backup_dir:
        backup_root = a.backup_dir
    elif os.path.isdir(os.path.join(repo, 'artifacts')):
        backup_root = os.path.join(repo, 'artifacts', 'snapshots', 'devbk')
    else:
        backup_root = os.path.join(repo, 'out', 'devbk')
    bk = os.path.join(backup_root, time.strftime('soundcheck-%Y%m%d-%H%M%S'))
    os.makedirs(bk, exist_ok=True)
    for f in os.listdir(lp):
        if f.endswith('.itdb') or f.endswith('.cbk'):
            shutil.copy2(os.path.join(lp, f), os.path.join(bk, f))
    print('\n备份 ->', bk)

    updates = [(x_by_key[key], pid) for key, pids in groups.items() for pid in pids]
    with lib:
        lib.executemany(
            'update avformat_info set volume_normalization_energy=? where item_pid=?',
            updates)
        if not a.keep_relative_volume:
            lib.execute('update item set relative_volume=0')

    # ---------- 校验 ----------
    n_rows, n_nz, mn, mx = lib.execute(
        'select count(*), sum(case when volume_normalization_energy!=0 then 1 else 0 end),'
        ' min(volume_normalization_energy), max(volume_normalization_energy)'
        ' from avformat_info').fetchone()
    n_rv = lib.execute('select count(*) from item where relative_volume!=0').fetchone()[0]
    print('avformat_info: rows=%d nonzero=%d min=%d max=%d' % (n_rows, n_nz, mn, mx))
    print('item.relative_volume nonzero=%d' % n_rv)

    db_x = {pid: x for pid, x in lib.execute(
        'select i.pid, a.volume_normalization_energy from item i'
        ' join avformat_info a on a.item_pid=i.pid')}
    bad = []
    for key, pids in groups.items():
        for pid in pids:
            if db_x.get(pid) != x_by_key[key]:
                bad.append((pid, x_by_key[key], db_x.get(pid)))
    mixed = lib.execute('''select count(*) from (
        select coalesce(nullif(i.album_artist,''),i.artist) aa, i.album,
               count(distinct a.volume_normalization_energy) n
        from item i join avformat_info a on a.item_pid=i.pid
        group by aa, i.album having n > 1)''').fetchone()[0]
    print('X 与缓存不一致: %d 行; 专辑内 X 不一致: %d 张' % (len(bad), mixed))

    dyn = os.path.join(lp, 'Dynamic.itdb')
    if os.path.exists(dyn):
        D = sqlite3.connect(dyn)
        item = {p: g for p, g in lib.execute('select pid, genius_id from item')}
        stats = {p: g for p, g in D.execute('select item_pid, genius_id from item_stats')}
        print('item_stats.genius_id 不一致: %d 行'
              % sum(1 for p, g in item.items() if stats.get(p) != g))
        print('Dynamic integrity:', D.execute('pragma integrity_check').fetchone()[0])
        D.close()
    print('Library integrity:', lib.execute('pragma integrity_check').fetchone()[0])
    lib.close()

    fsync_file(os.path.join(lp, 'Library.itdb'))
    print('volume cache:', 'flushed' if flush_volume(a.dev) else 'not flushed (safe eject first)')
    print('完成。SoundCheck 在 iPodSettings.xml 中已为 1；安全弹出 -> 重启后测试。')


if __name__ == '__main__':
    main()
