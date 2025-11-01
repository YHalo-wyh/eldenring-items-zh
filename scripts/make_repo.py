#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-click generator: fetch BWIKI item pages (MediaWiki API) and build a HowToCook-style repo.
Requires: requests (and optionally beautifulsoup4).
By default, generates a few sample items.
"""
import os, sys, argparse, json, pathlib, re, html
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("This script requires 'requests'. Try: pip install requests")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup  # optional, but nicer
except Exception:
    BeautifulSoup = None

BASE = "https://wiki.biligame.com/eldenring"
API  = f"{BASE}/api.php"

# Default sample items and their target subfolders
SAMPLES = [
    ("消耗品", "consumables", "红露滴圣杯瓶", "crimson-tears.md"),
    ("消耗品", "consumables", "蓝露滴圣杯瓶", "cerulean-tears.md"),
    ("圣印",   "weapons/seals", "巨人圣印记", "giants-seal.md"),
    ("关键道具", "key-items", "黄金种子", "golden-seed.md"),
    ("护符",    "talismans", "黄金树的恩惠", "erdtree-favor.md"),
]

def ensure_dirs(root: pathlib.Path):
    for p in [
        root/"items"/"consumables",
        root/"items"/"key-items",
        root/"items"/"weapons"/"seals",
        root/"items"/"talismans",
        root/"scripts",
    ]:
        p.mkdir(parents=True, exist_ok=True)

def write_root_files(root: pathlib.Path):
    readme = f"""# 艾尔登法环·物品手册（中文 · BWIKI 来源）

> 仓库结构模仿“程序员做饭指南”：**首页→分类目录→物品页面（Markdown）**。

- **总目录（BWIKI）**：<https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88>
- 分类：
  - [消耗品](items/consumables/README.md)
  - [关键道具](items/key-items/README.md)
  - [武器 / 圣印](items/weapons/seals/README.md)
  - [护符](items/talismans/README.md)

## 生成
- 运行 `python scripts/make_repo.py` 生成示例条目；
- 运行 `python scripts/gen_index.py` 自动刷新各级目录 README 列表；
- 提交到 GitHub 后，目录结构即可像“程序员做饭指南”一样浏览。
"""
    (root/"README.md").write_text(readme, encoding="utf-8")

    license_sources = """# 来源与许可

- 主要来源：**BWIKI · 艾尔登法环 WIKI**
  - 物品总目录：<https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88>
- 许可与使用：
  - CC BY-NC-SA 4.0（署名—非商业—相同方式共享）：<https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans>
  - 本仓库仅保存**引用链接与概述性文字**，不打包分发原图；游戏素材版权归 FromSoftware/Bandai Namco。
"""
    (root/"LICENSE-SOURCES.md").write_text(license_sources, encoding="utf-8")

def api_parse_html(title: str) -> str:
    """Return rendered HTML of a BWIKI page using MediaWiki 'action=parse'."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "text|images|links",
        "format": "json"
    }
    r = requests.get(API, params=params, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "parse" not in j or "text" not in j["parse"]:
        return ""
    return j["parse"]["text"]["*"]

def extract_first_image_url(html_text: str) -> str:
    # Prefer a visible image (icon). If BS4 is available, parse DOM; else regex fallback.
    if not html_text:
        return ""
    if BeautifulSoup:
        soup = BeautifulSoup(html_text, "html.parser")
        img = soup.select_one("img")
        if img and img.get("src"):
            src = img["src"]
            if src.startswith("//"):
                src = "https:" + src
            # Upgrade thumbnail size to 128px where possible
            src = re.sub(r"/(\d+)px-", "/128px-", src)
            return src
    # Regex fallback
    m = re.search(r'<img[^>]+src="([^"]+)"', html_text)
    if m:
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        src = re.sub(r"/(\d+)px-", "/128px-", src)
        return src
    return ""

def extract_summary(html_text: str) -> str:
    """Try to pick a concise summary. Prefer section after '道具效用', else first paragraph."""
    if not html_text:
        return ""
    text = re.sub("<[^>]+>", "", html_text)  # strip tags (roughly)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Look for '道具效用'
    m = re.search(r"道具效用[:：]?([^。\n]{4,120})", text)
    if m:
        return m.group(1).strip()[:140]
    # Else first sentence
    m = re.search(r"([\S\s]{10,160}?)。", text)
    if m:
        return m.group(1).strip()
    return text[:140]

def write_category_readmes(root: pathlib.Path, items_written):
    # items_written: list of (folder, title, filename)
    by_folder = {}
    for folder, title, filename in items_written:
        by_folder.setdefault(folder, []).append((title, filename))
    for folder, lst in by_folder.items():
        p = root/"items"/folder
        if folder == "weapons/seals":
            header = "# 圣印（Seals）"
        elif folder == "consumables":
            header = "# 消耗品（Consumables）"
        elif folder == "key-items":
            header = "# 关键道具（Key Items）"
        elif folder == "talismans":
            header = "# 护符（Talismans）"
        else:
            header = f"# {folder}"
        lines = [header, ""]
        for title, filename in sorted(lst):
            lines.append(f"- [{title}]({filename})")
        (p/"README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_item_md(root: pathlib.Path, folder: str, title: str, filename: str, img: str, summary: str):
    p = root/"items"/folder/filename
    url = f"{BASE}/{quote(title, safe='')}"
    type_map = {
        "consumables": "消耗品",
        "weapons/seals": "武器 / 圣印",
        "key-items": "关键道具",
        "talismans": "护符",
    }
    type_label = type_map.get(folder, "物品")
    md = f"""# {title}
![icon]({img})

- 类型：{type_label}  
- 概述：{summary}  
- 参考：BWIKI —— {url}
"""
    p.write_text(md, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="输出根目录（仓库根）")
    ap.add_argument("--items", default="", help="以逗号分隔的条目名；留空则生成内置示例")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    ensure_dirs(root)
    write_root_files(root)

    targets = []
    if args.items.strip():
        # custom items go to items/misc
        (root/"items"/"misc").mkdir(parents=True, exist_ok=True)
        for name in [x.strip() for x in args.items.split(",") if x.strip()]:
            filename = f"{name}.md"
            folder = "misc"
            targets.append((folder, filename, name, filename))
    else:
        for cname, folder, name, filename in SAMPLES:
            targets.append((folder, filename, name, filename))

    written = []
    for folder, filename, name, _ in targets:
        try:
            html = api_parse_html(name)
        except Exception as e:
            html = ""
        img = extract_first_image_url(html) or ""
        summary = extract_summary(html) or "（待补充）"
        write_item_md(root, folder, name, filename, img, summary)
        written.append((folder, name, filename))

    write_category_readmes(root, written)

    # drop helper script too (self-copy)
    scripts_dir = root/"scripts"
    scripts_dir.mkdir(exist_ok=True)
    # write a copy of this script into the output root for portability
    self_code = pathlib.Path(__file__).read_text(encoding="utf-8")
    (scripts_dir/"make_repo.py").write_text(self_code, encoding="utf-8")

    print(f"Done. Wrote {len(written)} items under {root}")

if __name__ == "__main__":
    main()
