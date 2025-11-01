#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 BWIKI（biligame）抓取【中文物品页面】，把图片与“物品说明”正文写到 Markdown。
需要: requests, beautifulsoup4
用法:
  pip install requests beautifulsoup4
  python scripts/make_repo_cn.py
  # 或自定义条目:
  python scripts/make_repo_cn.py --items 红露滴圣杯瓶,蓝露滴圣杯瓶,黄金种子
"""
import argparse, pathlib, re, html, json
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.biligame.com/eldenring"
API  = f"{BASE}/api.php"

SAMPLES = [
    ("consumables", "红露滴圣杯瓶", "crimson-tears.md"),
    ("consumables", "蓝露滴圣杯瓶", "cerulean-tears.md"),
    ("weapons/seals", "巨人圣印记", "giants-seal.md"),
    ("key-items", "黄金种子", "golden-seed.md"),
    ("talismans", "黄金树的恩惠", "erdtree-favor.md"),
]

TYPE_LABEL = {
    "consumables": "消耗品",
    "weapons/seals": "武器 / 圣印",
    "key-items": "关键道具",
    "talismans": "护符",
}

def mw_parse_html(title: str) -> str:
    """用 MediaWiki Action API 取渲染后的 HTML。"""
    # 参考：MediaWiki Action API 'action=parse'（各站一般都启用）。 见官方文档。  # noqa
    # https://www.mediawiki.org/wiki/API/zh
    r = requests.get(API, params={
        "action": "parse",
        "page": title,
        "prop": "text|images",
        "format": "json",
    }, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get("parse", {}).get("text", {}).get("*", "")

def extract_image_and_desc(html_text: str, title: str):
    """从 HTML 中尽量抽取 第一张图 + 物品说明段落（多策略，尽量接近你截图里的文案）。"""
    img_url, desc = "", ""
    if not html_text:
        return img_url, desc

    soup = BeautifulSoup(html_text, "html.parser")
    # 1) 图：页面第一张 img（常为物品图标/立绘）
    img = soup.select_one(".mw-parser-output img") or soup.select_one("img")
    if img and img.get("src"):
        src = img["src"]
        img_url = ("https:" + src) if src.startswith("//") else src
        # 把缩略图统一成 128px（若有）
        img_url = re.sub(r"/(\d+)px-", "/128px-", img_url)

    # 2) 说明：优先抓正文开头的段落，直到遇到下一个小标题/模板
    main = soup.select_one(".mw-parser-output") or soup
    paragraphs = []
    for node in main.children:
        name = getattr(node, "name", None)
        if name in ("h2", "h3"):  # 到下一个段落标题就停
            break
        if name == "p":
            txt = node.get_text(strip=True)
            if txt:
                paragraphs.append(txt)
        # 有的页用 <blockquote> 放“物品说明”
        if name == "blockquote":
            bt = node.get_text("\n", strip=True)
            if bt:
                paragraphs.append(bt)
        # 凑够 3 段就停，避免把整页都塞进去
        if len(paragraphs) >= 3:
            break

    if paragraphs:
        desc = "\n\n".join(paragraphs)

    # 3) 回退：如果该单页结构怪导致 desc 为空，则去「物品文本」总表里按标题搜索下一段文字
    if not desc:
        text_html = mw_parse_html("物品文本")
        tsoup = BeautifulSoup(text_html, "html.parser")
        link = tsoup.find("a", string=title)
        if link:
            # 找到链接后的若干段落/列表
            for sib in link.parent.next_siblings:
                if getattr(sib, "name", "") in ("h2", "h3", "ul", "ol"):
                    # 抓到第一个 ul/ol 的文本或下一个小标题前的文本
                    txt = sib.get_text("\n", strip=True)
                    if txt:
                        desc = txt
                        break

    # 清洗与裁剪
    desc = re.sub(r"\s+\n", "\n", desc or "").strip()
    return img_url, desc

def write_md(root: pathlib.Path, folder: str, title: str, filename: str, img: str, desc: str):
    url = f"{BASE}/{quote(title, safe='')}"
    tlabel = TYPE_LABEL.get(folder, "物品")
    # 把正文用引用块包住，接近你发的截图排版
    body = f"""# {title}
![icon]({img})

- 类型：{tlabel}  
- 数据源：{url}

> {desc.replace('\n', '\n> ')}
"""
    out = root / "items" / folder
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_text(body, encoding="utf-8")

def ensure_root(root: pathlib.Path):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "items").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# 艾尔登法环·物品手册（中文 · BWIKI 来源）\n\n"
        "- **总目录（BWIKI）**：<https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88>\n"
        "- 分类：\n"
        "  - [消耗品](items/consumables/README.md)\n"
        "  - [关键道具](items/key-items/README.md)\n"
        "  - [武器 / 圣印](items/weapons/seals/README.md)\n"
        "  - [护符](items/talismans/README.md)\n",
        encoding="utf-8",
    )

def write_category_readmes(root: pathlib.Path, written):
    by = {}
    for folder, title, filename in written:
        by.setdefault(folder, []).append((title, filename))
    for folder, lst in by.items():
        header = {
            "consumables": "# 消耗品（Consumables）",
            "key-items": "# 关键道具（Key Items）",
            "weapons/seals": "# 圣印（Seals）",
            "talismans": "# 护符（Talismans）",
        }.get(folder, f"# {folder}")
        lines = [header, ""]
        for t, f in sorted(lst):
            lines.append(f"- [{t}]({f})")
        (root/"items"/folder/"README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="输出根目录（仓库根）")
    ap.add_argument("--items", default="", help="逗号分隔物品名；留空则用内置样例")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    ensure_root(root)

    targets = []
    if args.items.strip():
        for name in [x.strip() for x in args.items.split(",") if x.strip()]:
            targets.append(("misc", name, f"{quote(name, safe='')}.md"))
    else:
        targets = SAMPLES[:]

    written = []
    for folder, title, filename in targets:
        html_text = mw_parse_html(title)
        img, desc = extract_image_and_desc(html_text, title)
        if not desc:
            desc = "（未从页面解析到正文，稍后完善）"
        write_md(root, folder, title, filename, img, desc)
        written.append((folder, title, filename))

    write_category_readmes(root, written)
    print(f"OK: 生成 {len(written)} 个条目")

if __name__ == "__main__":
    main()
