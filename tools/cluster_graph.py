#!/usr/bin/env python3
"""解码 op=2 的簇邻接表，输出图的连通分量与完全分量。

读取未修改的 genius_config.data，定位 op=2 条目，统计节点数、邻居槽位、
连通分量与完全分量（团）。完全分量内的簇 id 两两相邻，跨分量互不相邻，
可直接作为聚类标签的取值池。

用法:
    python tools/cluster_graph.py --config <genius_config.data>
    python tools/cluster_graph.py --config <genius_config.data> --cliques
"""
import argparse
import struct
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FIXED_LEN = {1: 16, 3: 16, 4: 16, 5: 8, 6: 0}


def read_table(blob):
    """解析 op=2 条目，返回 {簇 id: 邻居 id 集合}。"""
    u32 = lambda i: struct.unpack_from("<I", blob, i)[0]
    u64 = lambda i: struct.unpack_from("<Q", blob, i)[0]
    pos = 4
    count = u32(0)
    for _ in range(count):
        op = u32(pos)
        pos += 4
        if op == 2:
            pos += 4
            outer = u32(pos)
            pos += 4
            table = {}
            for _i in range(outer):
                key = u64(pos)
                pos += 8
                inner = u32(pos)
                pos += 4
                table[key] = {u64(pos + 8 * j) for j in range(inner)}
                pos += inner * 8
            return table
        if op not in FIXED_LEN:
            raise SystemExit("配置里出现未知 opcode %d，偏移 %d" % (op, pos - 4))
        pos += FIXED_LEN[op]
    raise SystemExit("配置里没有 op=2 条目，请提供原厂 blob（151,212 B）")


def components(table):
    seen = set()
    out = []
    for start in table:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            node = stack.pop()
            comp.append(node)
            for nxt in table[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        out.append(sorted(comp))
    return sorted(out, key=len, reverse=True)


def is_clique(table, comp):
    members = set(comp)
    return all(all(other in table[node] for other in comp) for node in comp)


def main():
    ap = argparse.ArgumentParser(
        description="解码 op=2 簇邻接表并输出图结构")
    ap.add_argument("--config", required=True,
                    help="原厂 genius_config.data 路径（151,212 B）")
    ap.add_argument("--cliques", action="store_true",
                    help="只输出完全分量")
    args = ap.parse_args()

    blob = open(args.config, "rb").read()
    table = read_table(blob)
    comps = components(table)
    cliques = [c for c in comps if len(c) > 1 and is_clique(table, c)]
    edges = sum(len(v) for v in table.values())

    if args.cliques:
        for comp in cliques:
            print("%d\t%s" % (len(comp), ",".join(str(x) for x in comp)))
        return

    print("节点 %d" % len(table))
    print("邻居槽位 %d" % edges)
    print("连通分量 %d" % len(comps))
    print("完全分量 %d" % len(cliques))
    print("分量规模 %s" % ", ".join(str(len(c)) for c in comps[:20]))
    print("")
    print("%6s  %s" % ("规模", "簇 id"))
    for comp in cliques:
        print("%6d  %s" % (len(comp), ", ".join(str(x) for x in comp)))


if __name__ == "__main__":
    main()
