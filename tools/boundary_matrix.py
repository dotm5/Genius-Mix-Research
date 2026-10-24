#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在目标数据库副本中合成一组边界 Kind-32 容器，每个容器只改变一个结构参数：

    B1  1 个成员         B2  100 个成员（抽样上限 k = min(n,100)）
    B3  189 个成员       B4  0 个成员（空容器）
    B5  50 个成员 + 10 行重复（item_to_container 重复行）

不加 --apply 时按 dry-run 打印选曲结果；--clean 按名称移除测试容器。
规格与实测结果见 docs/BOUNDARY.md。

用法:
    python tools/boundary_matrix.py --dir <itlp 副本目录> [--apply] [--clean] [--spec 规格.json]
"""
import argparse, os, shutil, sqlite3, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from genius_stats import sync

BOUNDARIES = [
    ('B1',       1, 0),   # (name, member_count, duplicate_extra)
    ('B2',     100, 0),
    ('B3',     189, 0),
    ('B4',       0, 0),
    ('B5',      50, 10),  # 50 个不同成员,再重复插入 10 行
]

FAMILY_A = {'Dance', 'Electronic', 'House', '电子游戏', '舞曲', '电子音乐', 'House 作品'}
FAMILY_B = {'Pop', 'J-Pop', 'K-Pop', 'Anime', '动画', '国际流行', 'Classical Crossover'}


def pick_tracks(L, spec):
    """spec: 'any' | 'genre:Name' | 'family:A' | 'family:B'  -> 返回 item.pid 列表"""
    if spec == 'any':
        return [r[0] for r in L.execute(
            'select pid from item where genius_id != 0 order by pid')]
    gm = dict(L.execute('select id, genre from genre_map'))
    rows = L.execute('select pid, genre_id from item where genius_id != 0 order by pid').fetchall()
    if spec.startswith('genre:'):
        want = spec.split(':', 1)[1]
        return [p for p, g in rows if gm.get(g) == want]
    if spec.startswith('family:'):
        fam = FAMILY_A if spec.endswith('A') else FAMILY_B
        return [p for p, g in rows if gm.get(g) in fam]
    raise SystemExit('未知规格：%s' % spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--clean', action='store_true')
    ap.add_argument('--spec', default=None, help='JSON file: [{name,n,dup,sel}]')
    a = ap.parse_args()

    Lp = os.path.join(a.dir, 'Library.itdb')
    Dp = os.path.join(a.dir, 'Dynamic.itdb')
    L = sqlite3.connect(Lp)
    D = sqlite3.connect(Dp)

    if a.clean:
        if a.spec:
            import json
            names = [e['name'] for e in json.load(open(a.spec, encoding='utf-8'))]
        else:
            names = [n for n, _, _ in BOUNDARIES]
        names = names + ['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'D1', 'D2', 'D3']
        names = list(dict.fromkeys(names))
        q = ','.join('?' * len(names))
        pids = [r[0] for r in L.execute(
            'select pid from container where distinguished_kind=32 and name in (%s)' % q, names)]
        for pid in pids:
            L.execute('delete from container where pid=?', (pid,))
            L.execute('delete from item_to_container where container_pid=?', (pid,))
            L.execute('delete from container_seed where container_pid=?', (pid,))
            D.execute('delete from container_ui where container_pid=?', (pid,))
        L.commit(); D.commit()
        stats_line = sync(L, D)
        D.commit()
        if stats_line[0]:
            print('item_stats.genius_id 同步:%d 行,剩余不一致 %d'
                  % (stats_line[1], stats_line[3]))
        print('移除 %d 个边界容器' % len(pids))
        if not a.apply:
            return

    # 模板:现有 kind32 容器
    tpl = L.execute('select * from container where distinguished_kind=32 limit 1').fetchone()
    cols = [d[0] for d in L.execute('select * from container limit 0').description]
    tplrow = dict(zip(cols, tpl))

    items = [r[0] for r in L.execute(
        'select i.pid from item i join item_to_container t on t.item_pid=i.pid '
        'where i.genius_id != 0 group by i.pid order by min(t.container_pid), t.physical_order')]
    items = [r[0] for r in L.execute('select pid from item where genius_id != 0 order by pid')]
    print('库内 genius_id 非零曲目：%d' % len(items))

    plan = []
    base_pid = -7000000000000000000
    if a.spec:
        import json
        entries = json.load(open(a.spec, encoding='utf-8'))
    else:
        entries = [{'name': n, 'n': c, 'dup': d, 'sel': 'any'} for n, c, d in BOUNDARIES]
    for i, e in enumerate(entries):
        plan.append((e['name'], e.get('n', 0), e.get('dup', 0),
                     base_pid - i, e.get('sel', 'any')))

    if not a.apply:
        for name, cnt, dup, pid, sel in plan:
            n = len(pick_tracks(L, sel))
            print('  %-3s sel=%-16s 需=%-4d 有=%-4d 重复=%d' % (name, sel, cnt, n, dup))
        print('（dry-run）')
        return

    for i, (name, cnt, dup, pid, sel) in enumerate(plan):
        L.execute('delete from container where pid=?', (pid,))
        L.execute('delete from item_to_container where container_pid=?', (pid,))
        L.execute('delete from container_seed where container_pid=?', (pid,))
        D.execute('delete from container_ui where container_pid=?', (pid,))
        row = dict(tplrow)
        row.update({'pid': pid, 'name': name, 'name_order': 2000 + i,
                    'distinguished_kind': 32, 'smart_is_genius': 1,
                    'smart_is_dynamic': 1, 'smart_is_filtered': 1,
                    'smart_limit_kind': 2, 'smart_is_folder': 0, 'parent_pid': 0})
        L.execute('insert into container (%s) values (%s)' % (
            ','.join(cols), ','.join('?' * len(cols))), [row[c] for c in cols])
        sel_items = pick_tracks(L, sel)[:cnt]
        for j, p in enumerate(sel_items):
            L.execute('insert into item_to_container (item_pid,container_pid,physical_order,shuffle_order) '
                      'values (?,?,?,0)', (p, pid, j))
        for j in range(dup):
            if not sel_items:
                break
            L.execute('insert into item_to_container (item_pid,container_pid,physical_order,shuffle_order) '
                      'values (?,?,?,0)', (sel_items[j % len(sel_items)], pid, len(sel_items) + j))
        if sel_items:
            L.execute('insert or replace into container_seed (container_pid,item_pid,seed_order) values (?,?,0)',
                      (pid, sel_items[0]))
        D.execute('insert or replace into container_ui (container_pid,play_order,is_reversed) values (?,?,0)',
                  (pid, 0))
        got = L.execute('select count(*) from item_to_container where container_pid=?', (pid,)).fetchone()[0]
        print('  新增 %-3s sel=%-16s 成员=%d（要求 %d + 重复 %d）'
              % (name, sel, got, cnt, dup))

    L.commit(); D.commit()
    stats_line = sync(L, D)
    D.commit()
    if stats_line[0]:
        print('item_stats.genius_id 同步:%d 行,剩余不一致 %d'
              % (stats_line[1], stats_line[3]))
    print('完成')


if __name__ == '__main__':
    main()
