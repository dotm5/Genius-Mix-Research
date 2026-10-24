#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_album_replaygain.py —— 按「专辑」计算 ITU-R BS.1770 / -18 LUFS ReplayGain,
并把统一增益写入设备真正读取的 Library.itdb:item.relative_volume。

流程(逐专辑):
  1. 按 (album_artist|artist, album) 分组全部曲目;
  2. ffmpeg 把整张专辑 concat 成一条流做 replaygain 扫描(EBU R128 / ReplayGain 2.0,
     参考 -18 LUFS);
  3. gain_dB -> relative_volume = clamp(round(1000*(10^(g/20)-1)), -1000, +1000);
     默认做峰值保护(若 factor*album_peak > 1,则 factor = 1/peak);
  4. 把该专辑所有曲目的 item.relative_volume 写成同一个值;
  5. 结果增量写入 out/replaygain_albums.json,可断点续跑。
     缓存条目带「文件名 + 大小」指纹；曲目集合变化时自动重扫，
     旧缓存（无指纹）退化为「曲目数一致」判定。

用法:
  python tools/apply_album_replaygain.py --dev F: --dry-run            # 只扫描+打印
  python tools/apply_album_replaygain.py --dev F: --apply              # 扫描+写库
  python tools/apply_album_replaygain.py --dev F: --apply --resume     # 复用已算结果

只生成缓存、不写 relative_volume:
  python tools/apply_album_replaygain.py --dev F: --dry-run --resume
随后用 tools/apply_soundcheck_from_replaygain.py 写 volume_normalization_energy。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ITLP = os.path.join('iPod_Control', 'iTunes', 'iTunes Library.itlp')
MUSIC = os.path.join('iPod_Control', 'Music')
CACHE = os.path.join('out', 'replaygain_albums.json')


def volume_root(guid):
    return '\\\\?\\Volume{' + guid.strip('{}') + '}\\'


def run(cmd, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout)


def paths_sig(paths):
    """专辑文件集合的轻量指纹：文件名 + 大小。

    缓存条目带指纹后，同名专辑被替换/增减曲目时会自动重扫；旧缓存没有
    指纹，只能退化为「曲目数一致」判定。
    """
    h = hashlib.sha1()
    for p in sorted(paths):
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        h.update(os.path.basename(p).encode('utf-8', 'surrogateescape'))
        h.update(bytes([0]))
        h.update(str(size).encode('ascii'))
        h.update(bytes([10]))
    return h.hexdigest()


def cache_entry_valid(ent, paths):
    if not ent or ent.get('tracks') != len(paths):
        return False
    sig = ent.get('sig')
    return True if sig is None else sig == paths_sig(paths)


def load_cache():
    try:
        return json.load(open(CACHE, encoding='utf-8'))
    except Exception:
        return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + '.tmp'
    json.dump(c, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)


def scan_concat_demux(paths):
    tmp = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8')
    try:
        for p in paths:
            tmp.write("file '%s'\n" % p.replace('\\', '/'))
        tmp.close()
        r = run(['ffmpeg', '-hide_banner', '-nostats', '-v', 'info',
                 '-f', 'concat', '-safe', '0', '-i', tmp.name,
                 '-map', '0:a:0', '-af', 'replaygain', '-f', 'null', '-'], timeout=900)
        log = (r.stderr or '') + (r.stdout or '')
        g = re.search(r'track_gain\s*=\s*([-0-9.]+)\s*dB', log)
        p = re.search(r'track_peak\s*=\s*([0-9.]+)', log)
        if g:
            return float(g.group(1)), (float(p.group(1)) if p else None), 'concat-demux'
        return None, None, log[-500:]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def scan_concat_filter(paths):
    """混合格式回退:每路重采样后 concat,再跑 replaygain"""
    n = len(paths)
    if n == 1:
        cmd = ['ffmpeg', '-hide_banner', '-nostats', '-v', 'info', '-i', paths[0],
               '-af', 'replaygain', '-f', 'null', '-']
    else:
        cmd = ['ffmpeg', '-hide_banner', '-nostats', '-v', 'info']
        for p in paths:
            cmd += ['-i', p]
        parts = []
        for i in range(n):
            parts.append('[%d:a]aresample=48000,aformat=sample_fmts=fltp:'
                         'channel_layouts=stereo[a%d]' % (i, i))
        graph = ';'.join(parts)
        graph += ';' + ''.join('[a%d]' % i for i in range(n))
        graph += 'concat=n=%d:v=0:a=1,replaygain[out]' % n
        cmd += ['-filter_complex', graph, '-map', '[out]', '-f', 'null', '-']
    r = run(cmd, timeout=1200)
    log = (r.stderr or '') + (r.stdout or '')
    g = re.search(r'track_gain\s*=\s*([-0-9.]+)\s*dB', log)
    p = re.search(r'track_peak\s*=\s*([0-9.]+)', log)
    if g:
        return float(g.group(1)), (float(p.group(1)) if p else None), 'concat-filter'
    return None, None, log[-500:]


def scan_album(paths):
    g, p, how = scan_concat_demux(paths)
    if g is None:
        g, p, how = scan_concat_filter(paths)
    return g, p, how


def rv_from_gain(gain, peak, clip_safe=True):
    factor = 10.0 ** (gain / 20.0)
    capped = False
    if clip_safe and peak and peak > 0 and factor * peak > 1.0:
        factor = 1.0 / peak
        capped = True
    rv = int(round(1000.0 * (factor - 1.0)))
    if rv > 1000:
        rv = 1000
    if rv < -1000:
        rv = -1000
    return rv, capped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dev', default='F:')
    ap.add_argument('--volume', metavar='GUID')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--resume', action='store_true', help='复用 out/replaygain_albums.json')
    ap.add_argument('--no-clip-safe', action='store_true')
    ap.add_argument('--jobs', type=int, default=8, help='并行扫描的专辑数(默认 8)')
    ap.add_argument('--backup-dir', default=None,
                    help='写库前 itdb 快照目录（默认 artifacts/snapshots/devbk 或 out/devbk）')
    a = ap.parse_args()

    dev = volume_root(a.volume) if a.volume else a.dev
    if re.match(r'^[A-Za-z]:$', dev):
        dev += os.sep
    lp = os.path.join(dev, ITLP)
    if not os.path.isdir(lp):
        raise SystemExit('device not found: %s' % lp)

    lib = sqlite3.connect(os.path.join(lp, 'Library.itdb'))
    loc = sqlite3.connect(os.path.join(lp, 'Locations.itdb'))

    items = lib.execute("""select pid,title,album,
                                  coalesce(nullif(album_artist,''),artist) aa,
                                  disc_number,track_number
                           from item order by aa,album,disc_number,track_number""").fetchall()
    groups = {}
    for pid, title, album, aa, disc, tn in items:
        key = (aa or '') + '\x00' + (album or '')
        groups.setdefault(key, []).append((pid, title, disc, tn))

    cache = load_cache() if a.resume else {}
    print('device=%r  items=%d  albums=%d  cache=%d  jobs=%d'
          % (dev, len(items), len(groups), len(cache), a.jobs))

    # 1) 解析每张专辑的文件路径
    tasks = []
    for key, rows in sorted(groups.items()):
        aa, album = key.split('\x00', 1)
        paths, pids = [], []
        for pid, title, disc, tn in rows:
            r = loc.execute('select location from location where item_pid=?', (pid,)).fetchone()
            if not r:
                continue
            p = os.path.join(dev, MUSIC, r[0].replace('/', os.sep))
            if os.path.exists(p):
                paths.append(p)
                pids.append(pid)
        if not paths:
            print('SKIP (no files): %r' % album)
            continue
        tasks.append({'key': key, 'aa': aa, 'album': album,
                      'paths': paths, 'pids': pids})

    # 2) 并行扫描缺失/失效的专辑
    valid = [t for t in tasks if cache_entry_valid(cache.get(t['key']), t['paths'])]
    invalid = [t for t in tasks
               if t['key'] in cache and not cache_entry_valid(cache[t['key']], t['paths'])]
    for t in invalid:
        cache.pop(t['key'], None)
    if invalid:
        save_cache(cache)
        print('invalidated %d cache entries (track set changed)' % len(invalid))
    todo = [t for t in tasks if t['key'] not in cache]
    print('scanning %d albums (%d cached valid) with %d workers ...'
          % (len(todo), len(valid), a.jobs))

    def scan_task(t):
        t0 = time.time()
        g, peak, how = scan_album(t['paths'])
        if g is None:
            return t['key'], None, how, 0
        rv, capped = rv_from_gain(g, peak, not a.no_clip_safe)
        return t['key'], {
            'album': t['album'], 'aa': t['aa'], 'tracks': len(t['paths']),
            'gain_db': g, 'peak': peak, 'rv': rv, 'capped': capped,
            'how': how, 'sig': paths_sig(t['paths']),
            'secs': round(time.time() - t0, 1)}, None, len(t['paths'])

    if todo:
        import concurrent.futures as cf
        done_n = 0
        with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            futs = {ex.submit(scan_task, t): t for t in todo}
            for fut in cf.as_completed(futs):
                key, ent, err, ntr = fut.result()
                done_n += 1
                t = futs[fut]
                if ent is None:
                    print('[%3d/%3d] FAIL %-42s %s'
                          % (done_n, len(todo), t['album'][:42], (err or '')[-200:]))
                    continue
                cache[key] = ent
                save_cache(cache)
                print('[%3d/%3d] %-42s n=%-2d gain=%+7.2f dB peak=%.4f -> rv=%+5d%s (%.1fs)'
                      % (done_n, len(todo), t['album'][:42], ent['tracks'],
                         ent['gain_db'], ent['peak'] or 0, ent['rv'],
                         ' [peak-capped]' if ent.get('capped') else '',
                         ent.get('secs', 0)))

    # 3) 汇总结果
    results = []
    for t in tasks:
        ent = cache.get(t['key'])
        if ent:
            results.append((t['key'], ent, t['pids']))
        else:
            print('MISSING result (scan failed): %r' % t['album'])

    if not a.apply or a.dry_run:
        print('\n(dry-run: database not modified; add --apply)')
        return 0

    # 备份数据库
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if a.backup_dir:
        backup_root = a.backup_dir
    elif os.path.isdir(os.path.join(repo, 'artifacts')):
        backup_root = os.path.join(repo, 'artifacts', 'snapshots', 'devbk')
    else:
        backup_root = os.path.join(repo, 'out', 'devbk')
    bk = os.path.join(backup_root, time.strftime('albumrg-%Y%m%d-%H%M%S'))
    os.makedirs(bk, exist_ok=True)
    for f in ('Library.itdb', 'Dynamic.itdb', 'Extras.itdb', 'Genius.itdb',
              'Locations.itdb', 'Locations.itdb.cbk'):
        p = os.path.join(lp, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(bk, f))
    print('\nbackup ->', bk)

    print('\nwriting relative_volume ...')
    total = 0
    for key, ent, pids in results:
        if not pids:
            continue
        lib.executemany('update item set relative_volume=? where pid=?',
                        [(ent['rv'], pid) for pid in pids])
        total += len(pids)
    lib.commit()
    print('updated %d items across %d albums' % (total, len(results)))
    print('integrity_check:', lib.execute('pragma integrity_check').fetchone()[0])

    # 分布摘要
    dist = lib.execute('select relative_volume,count(*) from item group by relative_volume '
                       'order by 2 desc').fetchall()
    print('relative_volume distribution:', dist[:12], '...' if len(dist) > 12 else '')
    mixed = lib.execute("""select count(*) from (
        select coalesce(nullif(album_artist,''),artist) aa, album,
               count(distinct relative_volume) n
        from item group by aa, album having n > 1)""").fetchone()[0]
    print('albums with mixed relative_volume:', mixed)
    print('note: safely eject and reconnect the iPod so it reloads the database.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
