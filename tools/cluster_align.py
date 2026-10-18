#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 591 项簇邻接表（op=2 compatible_genre）与本地曲库对齐，使原厂
genius_config.data 无需修改即可运行。

op=2 要求候选的 genius_metadata[0] 落在邻接表[种子 genius_metadata[0]] 内。
591 个节点中含多个完全图分量：把同一家族的流派分配到同一分量的不同簇，
族内两两相邻、族间不相邻，流派过滤由 op=2 完成。

metadata 写为 21 个 u64（168B），满足原厂配置第 7 条 op=1 field=20。
结果输出到 --out 目录，源库保持不变。细节见 docs/ALIGN.md。

用法:
    python tools/cluster_align.py --src <DB目录> --out <DB目录>
        [--keys unique|coarse|mixed] [--sims library|family|container]
"""
import argparse, os, shutil, sqlite3, struct, sys

# ---- 家族定义(可改):流派名 -> 家族
FAMILY = {
    'Dance': 'A', 'Electronic': 'A', 'House': 'A', '电子游戏': 'A', '舞曲': 'A',
    '电子音乐': 'A', 'House 作品': 'A',
    'Pop': 'B', 'J-Pop': 'B', 'K-Pop': 'B', 'Anime': 'B', '动画': 'B',
    '国际流行': 'B', 'Classical Crossover': 'B',
}
# 家族 -> 使用的完全分量里的簇(每个流派一个)
FAMILY_CLUSTERS = {
    'A': [1022, 1023, 1024, 1025, 1026, 1027],   # comp(1022) 大小 62,完全图
    'B': [1068, 1069, 1070, 1071, 1072, 1073],   # comp(18)   大小 27,完全图
    'C': [1044, 1047, 1049, 1051],               # comp(1044) 大小 12,完全图(兜底)
}

# 原厂 genius_config 第 7 条为 op=1 field=20，要求 genius_metadata.data 至少
# 21 个 u64（168B）。32B blob 让 field=20 越界取键 0，约束退化为持续否决。
META_U64S = 21
KEY20_BASE = 4000


def decode_cluster_table(blob):
    """解析 genius_config.data 里 op=2 的邻接表 -> {cluster: set(neighbors)}"""
    off = 0
    n = struct.unpack_from('<I', blob, off)[0]; off += 4
    for _ in range(n):
        op = struct.unpack_from('<I', blob, off)[0]; off += 4
        if op in (1, 3, 4):
            off += 16
        elif op == 5:
            off += 8
        elif op == 6:
            pass
        elif op == 2:
            off += 4
            cnt = struct.unpack_from('<I', blob, off)[0]; off += 4
            tab = {}
            for _i in range(cnt):
                k = struct.unpack_from('<Q', blob, off)[0]; off += 8
                inner = struct.unpack_from('<I', blob, off)[0]; off += 4
                ids = set()
                for _j in range(inner):
                    ids.add(struct.unpack_from('<Q', blob, off)[0]); off += 8
                tab[k] = ids
            return tab
        else:
            raise SystemExit('unknown opcode %d' % op)
    return {}


def load(src):
    L = sqlite3.connect(os.path.join(src, 'Library.itdb'))
    G = sqlite3.connect(os.path.join(src, 'Genius.itdb'))
    D = sqlite3.connect(os.path.join(src, 'Dynamic.itdb'))
    items = L.execute('select pid, genius_id, genre_id, title from item order by pid').fetchall()
    genres = dict(L.execute('select id, genre from genre_map'))
    containers = {pid: (name, kind) for pid, name, kind in
                  L.execute('select pid, name, distinguished_kind from container')}
    members = {}
    for pid, cpid, po in L.execute(
            'select item_pid, container_pid, physical_order from item_to_container'):
        members.setdefault(cpid, []).append((po, pid))
    for k in members:
        members[k] = [p for _, p in sorted(members[k])]
    cfg = G.execute('select data from genius_config').fetchone()
    return dict(L=L, G=G, D=D, items=items, genres=genres, containers=containers,
                members=members, config=cfg[0] if cfg else None)


def build(src, out, key_mode='mixed', sims_mode='library', verbose=True, ignore_prefix=None):
    if os.path.abspath(src) != os.path.abspath(out):
        os.makedirs(out, exist_ok=True)
        for f in ('Library.itdb', 'Genius.itdb', 'Dynamic.itdb', 'Extras.itdb', 'Locations.itdb'):
            p = os.path.join(src, f)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(out, f))
    db = load(out)
    blob = db['config']
    if not blob or len(blob) < 1000:
        raise SystemExit('genius_config.data 长度 %s，请提供原厂 blob（151,212 B）'
                         % (blob and len(blob)))
    table = decode_cluster_table(blob)
    if verbose:
        print('簇邻接表:%d 个节点' % len(table))

    # ---- 1. 流派 -> 簇
    genre_of_item = {}
    for pid, gid, genre_id, title in db['items']:
        genre_of_item[pid] = db['genres'].get(genre_id, '?')
    # 统计每个流派
    fam_used = {'A': 0, 'B': 0, 'C': 0}
    genre_cluster = {}
    allgenres = sorted(set(genre_of_item.values()))
    for g in allgenres:
        fam = FAMILY.get(g)
        if fam is None:
            fam = 'C'
        idx = fam_used[fam]
        pool = FAMILY_CLUSTERS[fam]
        genre_cluster[g] = pool[idx % len(pool)]
        fam_used[fam] += 1
    if verbose:
        for g in allgenres:
            print('  流派 %-16s -> 家族 %s  簇 %d' % (g, FAMILY.get(g, 'C'), genre_cluster[g]))

    # ---- 2. genius_metadata（21 个 u64 = 168 B，满足原厂 config 的 field=20）
    md_rows = []
    for i, (pid, gid, genre_id, title) in enumerate(db['items']):
        if not gid:
            continue
        cl = genre_cluster[genre_of_item[pid]]
        if key_mode == 'unique':
            k1, k2, k3 = 1000 + i, 2000 + i, 3000 + i
        elif key_mode == 'coarse':
            k1, k2, k3 = 1000 + (i % 8), 2000 + (i % 12), 3000 + (i % 16)
        else:  # mixed
            k1, k2, k3 = 1000 + (i % 24), 2000 + (i % 48), 3000 + i
        slots = [0] * META_U64S
        slots[0], slots[1], slots[2], slots[3] = cl, k1, k2, k3
        slots[20] = KEY20_BASE + (i % 40)
        md_rows.append((gid, 2, struct.pack('<%dQ' % META_U64S, *slots)))
    db['G'].executemany('insert or replace into genius_metadata values (?,?,?)', md_rows)

    # ---- 3. genius_similarities
    gid_of = {pid: gid for pid, gid, _, _ in db['items'] if gid}
    # 每个 item 所属的 kind32 容器(可能多个)
    pid_containers = {}
    for cpid, ps in db['members'].items():
        nm, kind = db['containers'].get(cpid, (None, None))
        if kind != 32:
            continue
        if ignore_prefix and nm and nm.startswith(ignore_prefix):
            continue
        for p in ps:
            pid_containers.setdefault(p, set()).add(cpid)
    # 家族(按容器名)
    pid_family = {}
    for p, cs in pid_containers.items():
        fams = set()
        for c in cs:
            nm = db['containers'].get(c, (None, None))[0]
            fams.add(FAMILY.get(nm, 'C'))
        pid_family[p] = fams or {'C'}

    all_pids = [pid for pid, gid, _, _ in db['items'] if gid]
    sim_rows = []
    for i, (pid, gid, genre_id, title) in enumerate(db['items']):
        if not gid:
            continue
        myg = genre_of_item[pid]
        myfam = pid_family.get(pid, {'C'})
        order = []
        if sims_mode == 'container':
            cand = []
            seen_c = set()
            for cpid in pid_containers.get(pid, ()):
                for q in db['members'].get(cpid, []):
                    if q != pid and q not in seen_c:
                        seen_c.add(q)
                        cand.append(q)
            order = cand
        else:
            same, fam, rest = [], [], []
            for q in all_pids:
                if q == pid:
                    continue
                qg = genre_of_item[q]
                if sims_mode == 'library':
                    (same if qg == myg else
                     fam if (pid_family.get(q, {'C'}) & myfam) else rest).append(q)
                else:  # family
                    if pid_family.get(q, {'C'}) & myfam:
                        (same if qg == myg else fam).append(q)
            order = same + fam + rest
        ids = [gid_of[p] for p in order if p in gid_of]
        data = struct.pack('<3I', 0, 4 + 8 * len(ids), len(ids)) + \
               (struct.pack('<%dQ' % len(ids), *ids) if ids else b'')
        sim_rows.append((gid, 2, data))
    db['G'].executemany('insert or replace into genius_similarities values (?,?,?)', sim_rows)

    # ---- 4. genius_config 保持原厂;确保列值
    db['G'].execute('update genius_config set version=2, default_num_results=25, min_num_results=10')
    db['G'].commit()
    db['L'].commit()
    if verbose:
        print('已写入:%d metadata 行,%d similarities 行,原厂 config %d B'
              % (len(md_rows), len(sim_rows), len(blob)))
    return dict(meta=len(md_rows), sims=len(sim_rows), table=len(table))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--keys', default='mixed', choices=['unique', 'coarse', 'mixed'])
    ap.add_argument('--sims', default='library', choices=['library', 'family', 'container'])
    ap.add_argument('--ignore-prefix', default=None,
                    help='生成 similarities 时忽略名称以此开头的 Kind-32 容器')
    a = ap.parse_args()
    build(a.src, a.out, a.keys, a.sims, ignore_prefix=a.ignore_prefix)


if __name__ == '__main__':
    main()
