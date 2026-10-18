#!/usr/bin/env python3
"""检查 Markdown 文档中的相对链接是否指向仓库内真实文件。

默认扫描 README.md、README.en.md 与 docs/*.md，跳过外部链接与页内锚点。

用法:
    python tools/check_links.py [路径 ...]
"""

import argparse
import glob
import os
import re
import sys

DEFAULT_FILES = ["README.md", "README.en.md"]
LINK = re.compile(r"\]\(([^)#]+)\)")


def target_files(extra):
    names = list(extra) if extra else DEFAULT_FILES + sorted(glob.glob(os.path.join("docs", "*.md")))
    return [name for name in names if os.path.isfile(name)]


def check(name):
    """返回该文件内失效的相对链接目标。"""
    base = os.path.dirname(name)
    text = open(name, encoding="utf-8").read()
    broken = []
    for match in LINK.finditer(text):
        target = match.group(1)
        if target.startswith(("http", "#", "mailto")):
            continue
        if not os.path.exists(os.path.normpath(os.path.join(base, target))):
            broken.append(target)
    return broken


def main():
    ap = argparse.ArgumentParser(description="检查 Markdown 相对链接")
    ap.add_argument("paths", nargs="*", help="待检查的文件；默认为 README 与 docs/*.md")
    args = ap.parse_args()

    names = target_files(args.paths)
    broken = []
    for name in names:
        for target in check(name):
            broken.append("%s: %s" % (name, target))
    for line in broken:
        print(line)
    if broken:
        return 1
    print("相对链接完整（%d 个文件）" % len(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
