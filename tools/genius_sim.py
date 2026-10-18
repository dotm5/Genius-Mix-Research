#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iPod nano Genius 生成流水线的离线模拟器，按固件语义复现：

    长度按 len(data) >> 3 解析 metadata；
    种子抽样用 LCG，只取第 0 次成功抽到的 primary；
    首曲消费后按 similarities 顺序扩展候选队列；
    每轮逐个候选求值约束链（distance / compatible_genre / skip_count / random_jitter）；
    全部否决时放松一次约束并继续扩展，直到达到 target 或循环条件结束。

用法:
    python tools/genius_sim.py --db <目录> [--seed-mode mix|track] [--seed-gid N] [--rng S] [--json]

<目录> 下需有 Library.itdb / Genius.itdb / Dynamic.itdb（或给出单文件路径）。
"""
import argparse, os, sqlite3, struct, sys, json, random

# ---------------------------------------------------------------- 数据层

def _open(p):
    if not os.path.exists(p):
        return None
    c = sqlite3.connect(p)
    return c

def load_db(d):
    if os.path.isfile(d):
        d = os.path.dirname(d)
    L = _open(os.path.join(d, 'Library.itdb'))
    G = _open(os.path.join(d, 'Genius.itdb'))
    D = _open(os.path.join(d, 'Dynamic.itdb'))
    if G is None:
        raise SystemExit('未找到 Genius.itdb：%s' % d)
    db = {}
    db['items'] = {pid: gid for pid, gid in G and
                   (L.execute('select pid, genius_id from item') if L else [])}
    db['genius_of'] = dict(db['items'])
    db['meta'] = {}
    for g, data in G.execute('select genius_id, data from genius_metadata'):
        n = len(data) // 8
        db['meta'][g] = {'gid': g, 'u64': list(struct.unpack('<%dQ' % n, data)) if n else [],
                         'count': n, 'sims': None}
    db['sims'] = {}
    for g, data in G.execute('select genius_id, data from genius_similarities'):
        db['sims'][g] = parse_sims(data)
    db['config'] = None
    r = G.execute('select id, version, default_num_results, min_num_results, data from genius_config').fetchone()
    if r:
        db['config'] = {'id': r[0], 'version': r[1], 'target': r[2], 'minimum': r[3], 'data': r[4]}
    db['containers'] = {}
    if L:
        for pid, name, kind, sg, sd, sf, slk, crit in L.execute(
                'select pid,name,distinguished_kind,smart_is_genius,smart_is_dynamic,'
                'smart_is_filtered,smart_limit_kind,smart_criteria from container'):
            db['containers'][pid] = {'name': name, 'kind': kind, 'is_genius': sg,
                                     'is_dynamic': sd, 'is_filtered': sf, 'limit_kind': slk,
                                     'criteria': crit}
        db['members'] = {}
        for pid, cpid, po in L.execute(
                'select item_pid, container_pid, physical_order from item_to_container'):
            db['members'].setdefault(cpid, []).append((po, pid))
        for cpid in db['members']:
            db['members'][cpid].sort()
            db['members'][cpid] = [pid for _, pid in db['members'][cpid]]
    db['stats'] = {}
    if D:
        try:
            for pid, dp, pcu, ds, scu in D.execute(
                    'select item_pid, date_played, play_count_user, date_skipped, '
                    'skip_count_user from item_stats'):
                db['stats'][pid] = (dp or 0, pcu or 0, ds or 0, scu or 0)
        except Exception:
            pass
    return db

def parse_sims(data):
    """genius_similarities.data：u32 flag、u32 size、u32 count、count×u64 候选 id"""
    if len(data) < 12:
        return []
    flag, size, cnt = struct.unpack_from('<3I', data, 0)
    if flag != 0:
        try:
            import zlib
            raw = zlib.decompress(data[8:])
            cnt = struct.unpack_from('<I', raw, 0)[0]
            return list(struct.unpack_from('<%dQ' % cnt, raw, 4))
        except Exception:
            return []
    cnt = min(cnt, (len(data) - 12) // 8)
    return list(struct.unpack_from('<%dQ' % cnt, data, 12))

# ------------------------------------------------------- genius_config 解析

class Cfg:
    def __init__(s, blob):
        s.blob = blob
        s.entries = []
        s.tail_u64 = 0
        s.tail_u32 = 50
        s._parse()

    def _u32(s, off):
        return struct.unpack_from('<I', s.blob, off)[0], off + 4

    def _u64(s, off):
        return struct.unpack_from('<Q', s.blob, off)[0], off + 8

    def _parse(s):
        n, off = s._u32(0)
        for _ in range(n):
            op, off = s._u32(off)
            e = {'op': op}
            if op in (1, 3, 4):
                e['p'] = []
                for _i in range(4):
                    v, off = s._u32(off)
                    e['p'].append(v)
            elif op == 5:
                e['p'] = []
                for _i in range(2):
                    v, off = s._u32(off)
                    e['p'].append(v)
            elif op == 2:
                f, off = s._u32(off)
                cnt, off = s._u32(off)
                tab = {}
                for _i in range(cnt):
                    k, off = s._u64(off)
                    inner, off = s._u32(off)
                    ids = set()
                    for _j in range(inner):
                        v, off = s._u64(off)
                        ids.add(v)
                    tab[k] = ids
                e['field'] = f
                e['table'] = tab
            elif op == 6:
                pass
            else:
                raise SystemExit('未知 opcode %d（偏移 %d）' % (op, off))
            s.entries.append(e)
        if len(s.blob) - off >= 8:
            s.tail_u64, off = s._u64(off)
            if len(s.blob) - off >= 4:
                s.tail_u32, off = s._u32(off)
        s.mode_and = bool(s.tail_u64 & 1)
        s.max_rounds = (s.tail_u64 >> 32) & 0xFFFFFFFF

# ------------------------------------------------------------ 约束对象

class Constraint:
    name = '?'
    def __init__(s, e, pl):
        s.e = e
        s.pl = pl

    def create_state(s, pl):
        return {}

    def in_effect(s, st):
        return True

    def check(s, ctx, st, meta):
        """返回 0=通过 1/2=否决"""
        return 0

    def consume(s, st, meta):
        """vfn[8]: 消费一个 metadata"""
        return

    def on_accept(s, st):
        """vfn[9]: 接受一首后更新 state"""
        return

    def relax(s, st):
        """vfn[7]: 每轮放松"""
        return 0


class Distance(Constraint):
    """op=1/3 'distance'  —— 同 metadata.u64[field] 的最小间隔"""
    name = 'distance'
    def __init__(s, e, pl, rng):
        Constraint.__init__(s, e, pl)
        s.field, s.floor, s.dist, s.jit = e['p']

    def create_state(s, pl):
        st = {'map': {}, 'distance': s.dist}
        return st

    def in_effect(s, st):
        return True

    def key(s, meta):
        i = s.field
        return meta['u64'][i] if i < meta['count'] else 0

    def check(s, ctx, st, meta):
        k = s.key(meta)
        if k not in st['map']:
            return 0
        return 1 if st['map'][k] < st['distance'] else 0

    def consume(s, st, meta):
        k = s.key(meta)
        for kk in list(st['map'].keys()):
            st['map'][kk] = 2 if kk == k else st['map'][kk] + 1
        if k not in st['map']:
            st['map'][k] = 1

    def on_accept(s, st):
        st['distance'] = s.dist + (s.pl.rng.randint(1, s.jit) if s.jit else 0)

    def relax(s, st):
        if s.floor >= st['distance']:
            return 0
        st['distance'] = s.floor if s.floor + 1 >= st['distance'] else st['distance'] - 1
        return 1


class CompatibleGenre(Constraint):
    """op=2 'compatible_genre' —— 候选的簇必须在种子簇的邻居集合里"""
    name = 'compatible_genre'
    def __init__(s, e, pl):
        Constraint.__init__(s, e, pl)
        s.field = e['field']
        s.table = e['table']

    def create_state(s, pl):
        seed = pl.meta
        k = seed['u64'][s.field] if seed and s.field < seed['count'] else 0
        nb = s.table.get(k) if k else None
        if not nb:
            nb = set()
        return {'allowed': set(nb)}

    def in_effect(s, st):
        return True

    def check(s, ctx, st, meta):
        allowed = st.get('allowed')
        if not allowed:
            return 2                      # 表缺失/空 -> 否决
        k = meta['u64'][s.field] if s.field < meta['count'] else 0
        return 0 if k in allowed else 2


class SkipCount(Constraint):
    """op=4 'skip_count'"""
    name = 'skip_count'
    def __init__(s, e, pl):
        Constraint.__init__(s, e, pl)
        s.min_skips, s.window, s.play_gap, s.pct = e['p']

    def create_state(s, pl):
        return {}

    def check(s, ctx, st, meta):
        dp, pc, ds, sc = ctx.stats.get(meta['gid'], (0, 0, 0, 0))
        if sc == 0:
            return 0
        if not (s.min_skips < sc):
            return 0
        if s.play_gap != 0 and not (s.play_gap > (dp - ds)):
            return 0
        if not (s.window > (ctx.now - ds)):
            return 0
        if not (100 * sc > s.pct * (sc + pc)):
            return 0
        return 1


class RandomJitter(Constraint):
    """op=5 'random_jitter'"""
    name = 'random_jitter'
    def __init__(s, e, pl):
        Constraint.__init__(s, e, pl)
        s.t0, s.t1 = e['p']

    def create_state(s, pl):
        return {'seen': 0}

    def in_effect(s, st):
        return st.get('seen', 0) <= 1

    def check(s, ctx, st, meta):
        thr = s.t1 if st.get('seen') else s.t0
        return 1 if ctx.rng.randint(1, 100) > thr else 0

    def relax(s, st):
        if st.get('seen'):
            return 0
        st['seen'] = 1
        return 1


def build_constraints(cfg, pl):
    out = []
    for e in cfg.entries:
        op = e['op']
        if op in (1, 3):
            out.append(Distance(e, pl, pl.rng))
        elif op == 2:
            out.append(CompatibleGenre(e, pl))
        elif op == 4:
            out.append(SkipCount(e, pl))
        elif op == 5:
            out.append(RandomJitter(e, pl))
    return out

# --------------------------------------------------------------- 生成核心

class Ctx:
    def __init__(s, db, rng, now=0):
        s.db = db
        s.rng = rng
        s.now = now
        s.stats = db['stats']
        s.cfg = Cfg(db['config']['data']) if db['config'] else None
        s.meta_cache = {}

    def meta_of(s, gid):
        if gid in s.meta_cache:
            return s.meta_cache[gid]
        m = s.db['meta'].get(gid)
        s.meta_cache[gid] = m
        return m

    def sims_of(s, gid):
        return s.db['sims'].get(gid, [])


class Playlist:
    def __init__(s, ctx, meta, flag=True):
        s.ctx = ctx
        s.rng = ctx.rng
        s.meta = meta
        s.candidates = []      # pl+7
        s.seen = set()
        s.used = set()         # pl+18 去重集合
        s.out = []             # pl+15
        s.round = 0            # pl+6
        s.bfs = flag           # pl+24 (offset 96)
        s.extras = False       # pl+98
        s.constraints = build_constraints(ctx.cfg, s) if ctx.cfg else []
        s.states = [c.create_state(s) for c in s.constraints]
        s.queue = []           # pl+12
        s.qi = 0
        s.oi = 0               # pl+15 展开游标

    # ---- 共用核心入口
    def consume(s, meta):
        s.round += 1
        for c, st in zip(s.constraints, s.states):
            c.consume(st, meta)
        s.out.append(meta['gid'])

    # ---- 候选扩展：来自 genius_similarities
    #      pl+18 = 已用集合（去重），只有新 id 才入候选
    def expand(s, meta, a3=0, a4=0):
        sims = s.ctx.sims_of(meta['gid'])
        n = len(sims)
        if a4:
            lo = a3 * a4
            hi = lo + a4
            if hi >= n:
                hi = n
        else:
            lo, hi = 0, n
        got = False
        for i in range(lo, hi):
            sid = sims[i]
            if sid in s.used:
                continue
            s.used.add(sid)
            s.candidates.append(sid)
            s.queue.append(sid)
            got = True
        return got

    # ---- 队列构建（第一段：pl+12 队列；第二段：pl+15 输出列表）
    def pump(s):
        got = False
        while s.qi < len(s.queue):
            gid = s.queue[s.qi]
            s.qi += 1
            m = s.ctx.meta_of(gid)
            if m and s.expand(m):
                got = True
        while s.oi < len(s.out):
            gid = s.out[s.oi]
            s.oi += 1
            m = s.ctx.meta_of(gid)
            if m and s.expand(m):
                got = True
        return got

    # ---- 约束链求值
    def eval_chain(s, meta):
        for c, st in zip(s.constraints, s.states):
            if not c.in_effect(st):
                continue
            r = c.check(s.ctx, st, meta)
            if r:
                return r
        return 0

    # ---- 下一首
    def next_track(s):
        if s.round == 0x7FFFFFFF:
            return None
        if s.round == 0:
            s.consume(s.meta)
            if s.bfs:
                s.pump()
            return s.meta['gid']
        v5 = None
        guard = 0
        while True:
            guard += 1
            if guard > 5000:
                break
            for i, gid in enumerate(s.candidates):
                if gid in s.seen:
                    continue
                m = s.ctx.meta_of(gid)
                if m is None:
                    s.seen.add(gid)
                    continue
                r = s.eval_chain(m)
                if r == 0:
                    v5 = m
                    s.consume(m)
                    if not s.extras:
                        s.seen.add(gid)
                    for c, st in zip(s.constraints, s.states):
                        c.on_accept(st)
                    break
                if r == 2:
                    s.seen.add(gid)
            if v5 is not None:
                break
            if s.bfs:
                more = s.pump()
            else:
                more = False
            relaxed = 0
            for c, st in zip(s.constraints, s.states):
                if relaxed == 0 and c.in_effect(st):
                    relaxed = c.relax(st)
            cont = (relaxed & 1) if s.ctx.cfg.mode_and else (relaxed | (1 if more else 0))
            if not cont:
                break
        return v5['gid'] if v5 else None


def generate(db, seed_gid, rng_seed=1, now=0, is_mix=True, target=None, minimum=None,
             max_rounds=20, verbose=False):
    rng = random.Random(rng_seed)
    ctx = Ctx(db, rng, now)
    if not ctx.cfg:
        return {'error': '缺少 genius_config'}
    meta = ctx.meta_of(seed_gid)
    if not meta or meta['count'] == 0:
        return {'error': '种子缺少 metadata', 'seed': seed_gid}
    pl = Playlist(ctx, meta)
    tgt = target if target is not None else (10 if is_mix else ctx.cfg and db['config']['target'])
    mn = minimum if minimum is not None else (2 if is_mix else db['config']['minimum'])
    tracks = []
    for i in range(max_rounds + 2):
        g = pl.next_track()
        if g is None:
            break
        tracks.append(g)
        if len(pl.out) == tgt:
            break
        if i > max_rounds:
            break
    ok = len(pl.out) >= mn
    return {'seed': seed_gid, 'target': tgt, 'minimum': mn,
            'count': len(pl.out), 'usable': ok, 'tracks': pl.out,
            'constraints': [c.name for c in pl.constraints]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--seed-mode', default='mix', choices=['mix', 'track'])
    ap.add_argument('--seed-gid', type=int, default=None)
    ap.add_argument('--container', type=int, default=None)
    ap.add_argument('--rng', type=int, default=1)
    ap.add_argument('--now', type=int, default=0)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--all-mixes', action='store_true')
    a = ap.parse_args()

    db = load_db(a.db)
    res = []
    if a.all_mixes or a.seed_mode == 'mix':
        conts = [(pid, c) for pid, c in db['containers'].items()
                 if c['kind'] == 32 and db['members'].get(pid)]
        if a.container is not None:
            conts = [(pid, c) for pid, c in conts if pid == a.container]
        for pid, c in sorted(conts, key=lambda kv: kv[1]['name'] or ''):
            members = db['members'][pid]
            gids = [db['genius_of'].get(p, 0) for p in members]
            gids = [g for g in gids if g]
            # 种子抽样选 primary（只用第 0 次抽样）
            rng = random.Random(a.rng)
            k = min(len(gids), 100)
            lst = list(gids)
            primary = None
            for j in range(k):
                if len(lst) > 1:
                    idx = rng.randint(0, len(lst) - 1)
                else:
                    idx = 0
                pick = lst.pop(idx)
                if db['meta'].get(pick):
                    if j == 0:
                        primary = pick
                    break
            if primary is None:
                res.append({'container': c['name'], 'pid': pid, 'members': len(gids),
                            'primary': None, 'usable': False, 'count': 0, 'tracks': []})
                continue
            r = generate(db, primary, a.rng, a.now, is_mix=True)
            r['container'] = c['name']
            r['pid'] = pid
            r['members'] = len(gids)
            r['primary'] = primary
            res.append(r)
    else:
        gids = [g for g in (a.seed_gid,) if g] or list(db['meta'].keys())
        for g in gids[:1]:
            res.append(generate(db, g, a.rng, a.now, is_mix=False))

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    for r in res:
        if 'error' in r:
            print('%-12s ERR %s' % (r.get('container', r.get('seed')), r['error']))
            continue
        print('%-12s members=%-3s primary=%-11s -> %2d/%-2d usable=%-5s %s' % (
            r.get('container', ''), r.get('members', ''), r.get('primary', ''),
            r['count'], r['minimum'], r['usable'],
            '' if not r['tracks'] else str(r['tracks'][:6]) + '...'))


if __name__ == '__main__':
    main()
