#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 iPod 曲库的排序字段。

设备列表顺序由 `item.*_order` 决定，`sort_*` 是排序用的字符串。
导入工具常常把 `title_order` 写成导入序号（100、200、…），于是歌曲列表
按导入顺序显示，而不是按标题。本工具按原厂风格重算：

    A-Z（忽略开头的 The / A / An）
    → 汉字按拼音（Windows LCMapStringEx, zh-CN + SORT_CHINESE_PRC）
    → 数字
    → 其它符号

同时重算 artist_order / album_order / album_artist_order / genre_order /
composer_order，并把 album_by_artist_order 置 NULL（原厂基线即为 NULL）。
`title` / `artist` / `album` 等显示字段不改。

用法:
    python tools/fix_sort_orders.py --dev I: --dry-run
    python tools/fix_sort_orders.py --dev I: --apply
    python tools/fix_sort_orders.py --db "<itlp 目录>" --apply
"""
import argparse
import ctypes
try:
    import ctypes.wintypes as wt
except Exception:  # 非 Windows 平台只做预览/降级
    wt = None
import os
import re
import shutil
import sqlite3
import sys
import time
import unicodedata

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ITLP = os.path.join('iPod_Control', 'iTunes', 'iTunes Library.itlp')
ARTICLES = re.compile(r'^(the|a|an)\s+', re.IGNORECASE)
LCMAP_SORTKEY = 0x00000400
SORT_CHINESE_PRC = 0x00000002


def is_cjk(ch):
    return ('㐀' <= ch <= '鿿'
            or '぀' <= ch <= 'ヿ'
            or '가' <= ch <= '힯'
            or 'ᄀ' <= ch <= 'ᇿ'
            or 'ꥠ' <= ch <= '꥿'
            or 'ힰ' <= ch <= '퟿')


class WinSortKey:
    """Windows 排序键；zh-CN + SORT_CHINESE_PRC 给出汉字拼音顺序。"""

    def __init__(self, locale='zh-CN'):
        self.locale = locale
        self.cache = {}
        self.enabled = os.name == 'nt' and wt is not None
        if self.enabled:
            self.k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            self.fn = self.k32.LCMapStringEx
            self.fn.argtypes = [wt.LPCWSTR, wt.DWORD, wt.LPCWSTR,
                                ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_ssize_t]
            self.fn.restype = ctypes.c_int

    def __call__(self, s):
        if not s:
            return b''
        if s in self.cache:
            return self.cache[s]
        if not self.enabled:
            key = s.encode('utf-8')
        else:
            flags = LCMAP_SORTKEY | SORT_CHINESE_PRC
            n = self.fn(self.locale, flags, s, -1, None, 0, None, None, 0)
            if n <= 0:
                key = s.encode('utf-8')
            else:
                buf = ctypes.create_string_buffer(n)
                r = self.fn(self.locale, flags, s, -1, buf, n, None, None, 0)
                key = buf.raw[:r] if r > 0 else s.encode('utf-8')
        self.cache[s] = key
        return key


WIN = WinSortKey()


def strip_article(s):
    if not s:
        return ''
    return ARTICLES.sub('', s, count=1)


def normalize_latin(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    out = []
    for c in s:
        if c.isalnum() or is_cjk(c) or c.isspace():
            out.append(c)
        elif c in "'’`":
            continue
        else:
            out.append(' ')
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def sort_key(s, keep_articles=False):
    s = s or ''
    if not keep_articles:
        s = strip_article(s)
    norm = normalize_latin(s)
    # 分类看原文首字符：NFKD 会把 Hangul 音节拆成 Jamo，不能拿来判类别。
    first = next((c for c in s if c.isalnum() or is_cjk(c)), '')
    if not first:
        return (3, '')
    if is_cjk(first):
        return (1, WIN(s))
    if first.isalpha():
        return (0, norm)
    if first.isdigit():
        return (2, norm)
    return (3, norm)


def rank_map(values, key_func):
    uniq = sorted(set(values), key=lambda v: (key_func(v), v if v is not None else ''))
    return {v: 100 * (i + 1) for i, v in enumerate(uniq)}


def composer_key(v):
    # 原厂基线里空作曲家居于最后。
    return (1, '') if not v else (0, sort_key(v))


def build(lp, keep_articles=False):
    db = sqlite3.connect(os.path.join(lp, 'Library.itdb'))
    genres = {gid: (g or '') for gid, g in db.execute(
        'select id, genre from genre_map')}
    rows = db.execute('''select pid,title,artist,album,album_artist,composer,genre_id
                         from item''').fetchall()
    def maybe_strip(v):
        v = v or ''
        return v if keep_articles else strip_article(v)

    data = []
    for pid, title, artist, album, album_artist, composer, genre_id in rows:
        st = maybe_strip(title)
        sa = maybe_strip(artist)
        sal = maybe_strip(album)
        saa = maybe_strip(album_artist or artist)
        sc = maybe_strip(composer) if composer else None
        sg = genres.get(genre_id, '') or ''
        data.append({'pid': pid, 'title': title or '', 'artist': artist or '',
                     'album': album or '', 'sort_title': st, 'sort_artist': sa,
                     'sort_album': sal, 'sort_album_artist': saa,
                     'sort_composer': sc, 'sort_genre': sg})

    key = lambda v: sort_key(v, keep_articles)
    title_rank = rank_map([d['sort_title'] for d in data], key)
    artist_rank = rank_map([d['sort_artist'] for d in data], key)
    album_rank = rank_map([d['sort_album'] for d in data], key)
    album_artist_rank = rank_map([d['sort_album_artist'] for d in data], key)
    genre_rank = rank_map([d['sort_genre'] for d in data], key)
    composer_rank = rank_map([d['sort_composer'] or '' for d in data], composer_key)

    updates = []
    for d in data:
        updates.append((
            d['sort_title'], d['sort_artist'], d['sort_album'],
            d['sort_album_artist'], d['sort_composer'],
            title_rank[d['sort_title']], artist_rank[d['sort_artist']],
            album_rank[d['sort_album']], album_artist_rank[d['sort_album_artist']],
            genre_rank[d['sort_genre']],
            composer_rank[d['sort_composer'] or ''],
            d['pid']))

    # 依赖表：Artists / Albums / Genres 菜单直接读这些表。
    table_updates = []

    ta_rows = db.execute('select pid,name from track_artist').fetchall()
    ta_rank = rank_map([strip_article(n or '') for _, n in ta_rows], key)
    for pid, name in ta_rows:
        sn = strip_article(name or '')
        table_updates.append((
            'update track_artist set sort_name=?, name_order=? where pid=?',
            (sn, ta_rank[sn], pid)))

    ar_rows = db.execute('select pid,name from artist').fetchall()
    ar_rank = rank_map([strip_article(n or '') for _, n in ar_rows], key)
    ar_order = {}
    for pid, name in ar_rows:
        sn = strip_article(name or '')
        ar_order[pid] = ar_rank[sn]
        table_updates.append((
            'update artist set sort_name=?, name_order=? where pid=?',
            (sn, ar_rank[sn], pid)))

    al_rows = db.execute('select pid,name,artist_pid from album').fetchall()
    al_rank = rank_map([strip_article(n or '') for _, n, _ in al_rows], key)
    for pid, name, artist_pid in al_rows:
        sn = strip_article(name or '')
        table_updates.append((
            'update album set sort_name=?, name_order=?, sort_order=?, '
            'artist_order=? where pid=?',
            (sn, al_rank[sn], al_rank[sn], ar_order.get(artist_pid, 0), pid)))

    gm_rows = db.execute('select id,genre from genre_map').fetchall()
    gm_rank = rank_map([g or '' for _, g in gm_rows], key)
    for gid, genre in gm_rows:
        table_updates.append((
            'update genre_map set genre_order=? where id=?',
            (gm_rank[genre or ''], gid)))

    return db, data, updates, (title_rank, artist_rank, album_rank,
                               album_artist_rank, genre_rank, composer_rank), table_updates


def preview(data, ranks, limit=25):
    title_rank, artist_rank, album_rank, album_artist_rank, genre_rank, composer_rank = ranks
    print('\n前 %d 首（按 title_order）:' % limit)
    for i, d in enumerate(sorted(data, key=lambda x: (title_rank[x['sort_title']], x['pid']))[:limit], 1):
        print('  %3d  %-40s  %s' % (i, d['sort_title'][:40], d['title'][:60]))
    print('\n按分类:')
    cats = {}
    for d in data:
        k = sort_key(d['sort_title'])
        name = {0: 'Latin', 1: 'CJK/pinyin', 2: 'digit', 3: 'other'}[k[0]]
        cats[name] = cats.get(name, 0) + 1
    for k in ('Latin', 'CJK/pinyin', 'digit', 'other'):
        if cats.get(k):
            print('  %-12s %d' % (k, cats[k]))
    print('\n艺术家顺序（前 15）:')
    for v in sorted(set(d['sort_artist'] for d in data),
                    key=lambda v: (artist_rank[v], v))[:15]:
        print('  %3d  %s' % (artist_rank[v] // 100, v))
    print('\n专辑顺序（前 15）:')
    for v in sorted(set(d['sort_album'] for d in data),
                    key=lambda v: (album_rank[v], v))[:15]:
        print('  %3d  %s' % (album_rank[v] // 100, v))
    print('\n流派顺序:')
    for v in sorted(set(d['sort_genre'] for d in data),
                    key=lambda v: (genre_rank[v], v)):
        print('  %3d  %s' % (genre_rank[v] // 100, v))


def apply(lp, updates, table_updates, backup_root=None):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backup_root is None:
        if os.path.isdir(os.path.join(repo, 'artifacts')):
            backup_root = os.path.join(repo, 'artifacts', 'snapshots', 'devbk')
        else:
            backup_root = os.path.join(repo, 'out', 'devbk')
    bk = os.path.join(backup_root, time.strftime('sortfix-%Y%m%d-%H%M%S'))
    os.makedirs(bk, exist_ok=True)
    for f in os.listdir(lp):
        if f.endswith('.itdb') or f.endswith('.cbk'):
            shutil.copy2(os.path.join(lp, f), os.path.join(bk, f))
    print('\n备份 ->', bk)

    db = sqlite3.connect(os.path.join(lp, 'Library.itdb'))
    with db:
        db.executemany('''update item set
            sort_title=?, sort_artist=?, sort_album=?, sort_album_artist=?,
            sort_composer=?, title_order=?, artist_order=?, album_order=?,
            album_artist_order=?, genre_order=?, composer_order=?,
            album_by_artist_order=NULL where pid=?''', updates)
        for sql, params in table_updates:
            db.execute(sql, params)
    print('integrity_check:', db.execute('pragma integrity_check').fetchone()[0])
    try:
        from genius_stats import fsync_file, flush_volume
        fsync_file(os.path.join(lp, 'Library.itdb'))
        print('volume cache:', 'flushed' if flush_volume(
            os.path.splitdrive(lp)[0] or '') else 'not flushed (safe eject first)')
    except Exception as e:
        print('flush helper unavailable:', e)
    db.close()
    print('完成。安全弹出 -> 重新插拔/重启 iPod 后查看 音乐 -> 歌曲。')


def main():
    ap = argparse.ArgumentParser(description='重建 iPod 排序字段')
    ap.add_argument('--dev', default='I:', help='设备盘符，默认 I:')
    ap.add_argument('--db', help='iTunes Library.itlp 目录（优先于 --dev）')
    ap.add_argument('--apply', action='store_true', help='写入设备')
    ap.add_argument('--dry-run', action='store_true', help='只打印预览')
    ap.add_argument('--keep-articles', action='store_true',
                    help='不省略标题/艺术家/专辑开头的 The/A/An')
    ap.add_argument('--backup-dir', default=None,
                    help='设备库快照目录（默认 artifacts/snapshots/devbk 或 out/devbk）')
    a = ap.parse_args()

    if a.db:
        lp = a.db
    else:
        dev = a.dev
        if re.match(r'^[A-Za-z]:$', dev):
            dev += os.sep
        lp = os.path.join(dev, ITLP)
    if not os.path.isdir(lp):
        raise SystemExit('设备目录不存在: %s' % lp)

    db, data, updates, ranks, table_updates = build(lp, a.keep_articles)
    preview(data, ranks)
    db.close()
    if a.apply and not a.dry_run:
        apply(lp, updates, table_updates, a.backup_dir)
    else:
        print('\n(dry-run: 未写入；加 --apply 写入)')


if __name__ == '__main__':
    main()
