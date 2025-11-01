#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_samples_all_categories.py
每个大类（武器/防具/护符/物品/法术/战灰）抓取 N 个样例，下载图片，渲染成“逐行+表格”风格的 MD。
用法：
    python scripts/fetch_samples_all_categories.py --per 3 --delay 0.8 --clean
"""

import argparse
import time
import pathlib

from lib_cn import (
    get_html, pick_first_n_items_unique, write_md_by_data, append_index,
    parse_weapon, parse_armor, parse_talisman, parse_item, parse_spell, parse_ash,
    wipe_repo_except, ensure_dir, safe_filename, display_name
)

INDEX = {
    "weapons":   "https://wiki.biligame.com/eldenring/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88",
    "armors":    "https://wiki.biligame.com/eldenring/%E9%98%B2%E5%85%B7%E4%B8%80%E8%A7%88",
    "talismans": "https://wiki.biligame.com/eldenring/%E6%8A%A4%E7%AC%A6%E4%B8%80%E8%A7%88",
    "items":     "https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88",
    "spells":    "https://wiki.biligame.com/eldenring/%E6%B3%95%E6%9C%AF%E4%B8%80%E8%A7%88",
    "ashes":     "https://wiki.biligame.com/eldenring/%E6%88%98%E7%81%B0%E4%B8%80%E8%A7%88",
}

PARSERS = {
    "weapons": parse_weapon,
    "armors": parse_armor,
    "talismans": parse_talisman,
    "items": parse_item,
    "spells": parse_spell,
    "ashes": parse_ash,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=3, help="每类抓取样例个数")
    ap.add_argument("--delay", type=float, default=0.8, help="抓取间隔（秒）")
    ap.add_argument("--clean", action="store_true", help="开跑前清空仓库（保留 .github 和 scripts）")
    args = ap.parse_args()

    if args.clean:
        wipe_repo_except([".github", "scripts"])

    # 根 README
    pathlib.Path("README.md").write_text(
        "# 艾尔登法环 · 物品手册（多分类样例）\n\n"
        "- [武器](items/weapons/README.md)\n"
        "- [防具](items/armors/README.md)\n"
        "- [护符](items/talismans/README.md)\n"
        "- [物品](items/items/README.md)\n"
        "- [法术](items/spells/README.md)\n"
        "- [战灰](items/ashes/README.md)\n",
        encoding="utf-8"
    )

    for cat, index_url in INDEX.items():
        triples = pick_first_n_items_unique(index_url, args.per)
        made = []
        for name, url, _title in triples:
            try:
                html = get_html(url)
                data = PARSERS[cat](html)
                # 少数页面 title 里没有 h1 时，用目录名兜底
                data["name"] = data.get("name") or name
                write_md_by_data(data, source_url=url)
                rel = pathlib.Path("items") / cat / f"{safe_filename(data['name'])}.md"
                made.append((data["name"], rel))
            except Exception as e:
                print(f"[warn] 解析失败 {cat}: {name} -> {url} -> {e}")
            time.sleep(args.delay)

        append_index(cat, [(t, pathlib.Path(p)) for t, p in made])

    # 低调署名
    pathlib.Path("ATTRIBUTION.md").write_text(
        "# Attribution\n\n本仓库 Demo 的条目内容来自公开百科页面，使用 CC BY-NC-SA 4.0 协议条款下的可共享内容；"
        "仅供研究与学习，保留署名并相同方式共享。\n",
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()
