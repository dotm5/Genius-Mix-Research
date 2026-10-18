#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 cluster_align 生成的库部署到 iPod，写入前先做完整快照备份。

用法:
    python tools/deploy_aligned.py --src <输出目录> --dev F:
    python tools/deploy_aligned.py --src <输出目录> --dev F: --dry-run

步骤:
 1. 备份设备上的 5 个 itdb 到 <backup-dir>/align-<时间戳>/
 2. 校验：genius_config.data 为 151,212 B；genius_metadata.data 为 168 B
 3. 覆盖写入并提交
"""
import argparse, os, shutil, sqlite3, struct, sys, time

FILES = ['Library.itdb', 'Genius.itdb', 'Dynamic.itdb', 'Extras.itdb', 'Locations.itdb']
ITLP = r'iPod_Control\iTunes\iTunes Library.itlp'
FACTORY_LEN = 151212
META_LEN = 168


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--dev', default='F:')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--backup-dir', default=os.path.join('out', 'devbk'),
                    help='设备 itdb 快照的存放目录')
    a = ap.parse_args()

    dst = os.path.join(a.dev, ITLP)
    if not os.path.isdir(dst):
        raise SystemExit('设备目录不存在: %s' % dst)

    # --- 校验源
    G = sqlite3.connect(os.path.join(a.src, 'Genius.itdb'))
    cfg = G.execute('select length(data), default_num_results, min_num_results from genius_config').fetchone()
    if cfg[0] != FACTORY_LEN:
        raise SystemExit('genius_config.data 长度 %d，原厂为 %d' % (cfg[0], FACTORY_LEN))
    lens = set(l for (l,) in G.execute('select length(data) from genius_metadata'))
    if lens != {META_LEN}:
        raise SystemExit('genius_metadata.data 长度 %s，应为 %d' % (lens, META_LEN))
    n_md = G.execute('select count(*) from genius_metadata').fetchone()[0]
    n_sim = G.execute('select count(*) from genius_similarities').fetchone()[0]
    print('源校验 OK: config=%d B (target=%d min=%d), metadata=%d 行 × %d B, similarities=%d 行'
          % (cfg[0], cfg[1], cfg[2], n_md, META_LEN, n_sim))

    ts = time.strftime('align-%Y%m%d-%H%M%S')
    bk = os.path.join(a.backup_dir, ts)
    os.makedirs(bk, exist_ok=True)
    for f in FILES:
        p = os.path.join(dst, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(bk, f))
    print('已备份设备 itdb ->', bk)

    if a.dry_run:
        print('--dry-run:未写入')
        return

    for f in FILES:
        s = os.path.join(a.src, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(dst, f))
            print('  写入', f)
    print('完成。请安全弹出 iPod 并重启,然后在 音乐 -> Genius Mixes 里测试。')


if __name__ == '__main__':
    main()
