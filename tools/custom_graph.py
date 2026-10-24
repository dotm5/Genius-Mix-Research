#!/usr/bin/env python3
"""把自定义聚类写入设备数据库副本。

每首曲目的聚类标签放在 `genius_metadata.data` 的首槽。设备在候选标签是种子
标签在 `op=2` 邻接表中的邻居时接受该候选，因此标签取值是聚类方案的自由度；
标签取自表内 id，同取一个完全分量时两两相邻。

spec 文件支持两种写法：

    {"clusters": [{"name": "A", "label": 1022, "tracks": ["标题", "..."]}, ...]}

    {"edges": [["标题", "标题"], ...], "labels": [1022, 1023]}

边表写法由工具求连通分量作为聚类；未给出 label 时从完全分量中取。

用法:
    python tools/custom_graph.py --config <genius_config.data> \\
        --src <itlp 目录> --out <输出目录> --spec <spec.json>
    python tools/custom_graph.py --out <输出目录> --check
"""
import argparse
import json
import os
import shutil
import sqlite3
import struct
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_graph import read_table, components, is_clique
from genius_stats import has_genius_id, mismatches, sync

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FILES = ["Library.itdb", "Genius.itdb", "Dynamic.itdb", "Extras.itdb", "Locations.itdb"]
META_U64S = 21
LABEL_BASE = 900000001


def connect(path, writable=False):
    mode = "rwc" if writable else "ro"
    return sqlite3.connect("file:%s?mode=%s" % (os.path.abspath(path), mode),
                           uri=True, timeout=15)


def load_spec(path, library):
    spec = json.load(open(path, encoding="utf-8"))
    title_to_pid = {}
    for pid, title in library.execute("select pid, title from item"):
        title_to_pid.setdefault(title, pid)

    def to_pids(names):
        pids, missing = [], []
        for name in names:
            if name in title_to_pid:
                pids.append(title_to_pid[name])
            else:
                missing.append(name)
        return pids, missing

    if "clusters" in spec:
        out = []
        for entry in spec["clusters"]:
            pids, missing = to_pids(entry["tracks"])
            if missing:
                print("未匹配的标题：%s" % ", ".join(missing[:5]))
            out.append({"name": entry["name"], "label": entry.get("label"),
                        "tracks": pids})
        return out

    graph = defaultdict(set)
    nodes = set()
    for edge in spec.get("edges", []):
        if len(edge) < 2:
            continue
        nodes.add(edge[0])
        nodes.add(edge[1])
        graph[edge[0]].add(edge[1])
        graph[edge[1]].add(edge[0])
    seen, comps = set(), []
    for start in sorted(nodes):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            node = stack.pop()
            comp.append(node)
            for nxt in graph[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        comps.append(sorted(comp))
    labels = spec.get("labels") or []
    out = []
    for index, comp in enumerate(comps):
        pids, missing = to_pids(comp)
        if missing:
            print("未匹配的标题：%s" % ", ".join(missing[:5]))
        out.append({"name": "C%d" % index,
                    "label": labels[index] if index < len(labels) else None,
                    "tracks": pids})
    return out


def fill_labels(table, clusters):
    used = {c["label"] for c in clusters if c["label"] is not None}
    pool = []
    for comp in components(table):
        if len(comp) > 1 and is_clique(table, comp):
            pool.extend(x for x in comp if x not in used)
    for cluster in clusters:
        if cluster["label"] is None:
            if not pool:
                raise SystemExit("标签池已空，请在 spec 里直接给出 label")
            cluster["label"] = pool.pop(0)
    return clusters


def build(args):
    blob = open(args.config, "rb").read()
    table = read_table(blob)
    spec_path = args.spec
    dst = os.path.abspath(args.out)
    os.makedirs(dst, exist_ok=True)
    for name in FILES:
        src = os.path.join(args.src, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, name))

    library = connect(os.path.join(dst, "Library.itdb"), writable=True)
    genius = connect(os.path.join(dst, "Genius.itdb"), writable=True)
    dynamic = connect(os.path.join(dst, "Dynamic.itdb"), writable=True)

    clusters = fill_labels(table, load_spec(spec_path, library))
    outside = [c["label"] for c in clusters if c["label"] not in table]
    if outside:
        raise SystemExit("这些标签不在簇表内，请从完全分量取值：%s" % outside)

    print("%-14s %-10s %s" % ("聚类", "标签", "曲目"))
    for cluster in clusters:
        print("%-14s %-10s %d" % (cluster["name"], cluster["label"], len(cluster["tracks"])))
    print("")
    print("聚类邻接矩阵（可 = 标签相邻）")
    names = " ".join("%6s" % c["name"][:6] for c in clusters)
    print("%-14s %s" % ("", names))
    for left in clusters:
        row = []
        for right in clusters:
            if left is right:
                row.append("同")
            elif right["label"] in table.get(left["label"], set()):
                row.append("可")
            else:
                row.append("否")
        print("%-14s %s" % (left["name"], " ".join("%6s" % x for x in row)))
    print("")

    for pid in [r[0] for r in library.execute(
            "select pid from container where distinguished_kind=32")]:
        library.execute("delete from item_to_container where container_pid=?", (pid,))
        library.execute("delete from container_seed where container_pid=?", (pid,))
        dynamic.execute("delete from container_ui where container_pid=?", (pid,))
        library.execute("delete from container where pid=?", (pid,))
    genius.execute("delete from genius_metadata")
    genius.execute("delete from genius_similarities")

    assigned = {}
    for cluster in clusters:
        for pid in cluster["tracks"]:
            assigned[pid] = LABEL_BASE + len(assigned)
    library.execute("update item set genius_id=0")
    library.executemany("update item set genius_id=? where pid=?",
                        [(g, p) for p, g in assigned.items()])

    # 6G 的 Genius 持久标记在 Dynamic.itdb.item_stats.genius_id，必须与
    # Library.itdb.item.genius_id 完全一致；不一致时歌单会出现但为空 /
    # 提示“所选歌曲不可播放”。5G 的 item_stats 没有这一列，自动跳过。
    has_stats_col, _, _, stats_remaining = sync(library, dynamic)
    stats_mismatch = stats_remaining if has_stats_col else None

    label_of = {p: c["label"] for c in clusters for p in c["tracks"]}
    metadata = []
    for cluster in clusters:
        for pid in cluster["tracks"]:
            gid = assigned[pid]
            slots = [0] * META_U64S
            slots[0] = cluster["label"]
            # distance 约束键（field 1/2/3/20）：逐首唯一，间隔约束不误杀。
            slots[1] = slots[2] = slots[3] = slots[20] = gid
            metadata.append((gid, 2, struct.pack("<%dQ" % META_U64S, *slots)))
    genius.executemany("insert or replace into genius_metadata values (?,?,?)", metadata)

    all_pids = [p for c in clusters for p in c["tracks"]]
    similarities = []
    for pid in all_pids:
        mine = label_of[pid]
        same = [q for q in all_pids if q != pid and label_of[q] == mine]
        near = [q for q in all_pids if q != pid and label_of[q] != mine
                and label_of[q] in table.get(mine, set())]
        rest = [q for q in all_pids if q != pid and q not in same and q not in near]
        ids = [assigned[q] for q in same + near + rest]
        payload = struct.pack("<3I", 0, 4 + 8 * len(ids), len(ids))
        payload += struct.pack("<%dQ" % len(ids), *ids) if ids else b""
        similarities.append((assigned[pid], 2, payload))
    genius.executemany("insert or replace into genius_similarities values (?,?,?)",
                       similarities)

    made = []
    for index, cluster in enumerate(clusters):
        if len(cluster["tracks"]) < 2:
            continue
        pid = -(0xC0570 << 40) - index
        library.execute(
            "insert into container (pid, distinguished_kind, date_created, date_modified, "
            "name, name_order, parent_pid, media_kinds, smart_is_genius, smart_is_dynamic, "
            "smart_is_filtered, smart_limit_kind, smart_limit_value) "
            "values (?,32,0,0,?,0,0,1,1,1,1,2,25)", (pid, cluster["name"]))
        dynamic.execute(
            "insert into container_ui (container_pid, play_order, is_reversed, "
            "album_field_order, repeat_mode, shuffle_items, has_been_shuffled) "
            "values (?,1,0,52,0,0,0)", (pid,))
        for order, track in enumerate(cluster["tracks"]):
            library.execute(
                "insert into item_to_container (item_pid, container_pid, physical_order, "
                "shuffle_order) values (?,?,?,0)", (track, pid, order))
        library.execute(
            "insert into container_seed (container_pid, item_pid, seed_order) values (?,?,0)",
            (pid, cluster["tracks"][0]))
        made.append((cluster["name"], pid, len(cluster["tracks"])))

    library.commit()
    genius.commit()
    dynamic.commit()
    library.close()
    genius.close()
    dynamic.close()

    print("写入 genius_id %d 首，metadata %d 行，similarities %d 行" %
          (len(assigned), len(metadata), len(similarities)))
    if stats_mismatch is not None:
        print("同步 item_stats.genius_id，剩余不一致 %d 行" % stats_mismatch)
    for name, pid, count in made:
        print("容器 %-14s %d 首" % (name, count))
    print("输出 %s" % dst)
    print("校验：python tools/custom_graph.py --config %s --out %s --check"
          % (args.config, args.out))


def check(args):
    blob = open(args.config, "rb").read()
    table = read_table(blob)
    directory = os.path.abspath(args.out)
    library = connect(os.path.join(directory, "Library.itdb"))
    genius = connect(os.path.join(directory, "Genius.itdb"))
    dynamic = connect(os.path.join(directory, "Dynamic.itdb"))
    label_of = {}
    for gid, data in genius.execute("select genius_id, data from genius_metadata"):
        label_of[gid] = struct.unpack_from("<Q", data, 0)[0]
    gid_of = {p: g for p, g in library.execute(
        "select pid, genius_id from item where genius_id != 0")}

    outside = [g for g, v in label_of.items() if v not in table]
    print("metadata %d 行，其中标签不在簇表内 %d 行" % (len(label_of), len(outside)))
    ok = not outside
    for pid, name in library.execute(
            "select pid, name from container where distinguished_kind=32 order by name"):
        members = [r[0] for r in library.execute(
            "select item_pid from item_to_container where container_pid=?", (pid,))]
        labels = sorted({label_of.get(gid_of[m]) for m in members if m in gid_of})
        bad = [(a, b) for a in labels for b in labels
               if a != b and b not in table.get(a, set())]
        if bad:
            ok = False
        print("%-14s 成员 %-4d 标签 %s %s" %
              (name, len(members), labels, "" if not bad else "不兼容 %s" % bad[:3]))
    if has_genius_id(dynamic):
        mismatch = mismatches(library, dynamic)
        if mismatch:
            ok = False
        print("item_stats.genius_id 与 item.genius_id 不一致 %d 行" % len(mismatch))
    else:
        print("item_stats 没有 genius_id 列（5G），跳过一致性检查")
    print("")
    print("结果 %s" % ("容器内标签两两相邻且 item_stats 一致"
                       if ok else "存在不相邻的标签或 item_stats 不一致"))
    library.close()
    genius.close()
    dynamic.close()


def main():
    ap = argparse.ArgumentParser(
        description="把自定义聚类写入设备数据库副本")
    ap.add_argument("--config", required=True,
                    help="原厂 genius_config.data 路径（151,212 B）")
    ap.add_argument("--src", help="源 itlp 目录（含五个 .itdb）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--spec", help="JSON 聚类规格")
    ap.add_argument("--check", action="store_true", help="校验输出目录")
    args = ap.parse_args()

    if args.check:
        check(args)
        return
    if not args.src or not args.spec:
        raise SystemExit("写入需要 --src 与 --spec；仅校验时加 --check")
    build(args)


if __name__ == "__main__":
    main()
