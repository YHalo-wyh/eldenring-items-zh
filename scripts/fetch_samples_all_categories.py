#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取 BWIKI『艾尔登法环』各大类各 3 个样例 → 生成“逐行 + 表格”的 Markdown 与本地图片。
- 兼容武器、防具、护符、物品、法术、战灰 6 大类常见版式
- 过滤重复标题
- 去除“孤儿数值行”（例如 FP 和 重量被右侧浮动出来的 3（-/-）、3.5 之类）
- 表格保证两列左对齐
- 低调尾注保留 CC BY-NC-SA 4.0 署名与原始链接（必须）

依赖：
  pip install requests beautifulsoup4
"""

import os
import re
import sys
import time
import json
import shutil
import pathlib
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/2.5"}
DELAY = float(os.getenv("ER_FETCH_DELAY", "0.7"))   # 默认 0.7s，可被 Actions 传参覆盖
PER_CAT = int(os.getenv("ER_FETCH_PER", "3"))       # 每类抓取条数
TIMEOUT = 25.0

# 各分类目录页
INDEX = {
    "weapons": "https://wiki.biligame.com/eldenring/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88",
    "armors":  "https://wiki.biligame.com/eldenring/%E9%98%B2%E5%85%B7%E4%B8%80%E8%A7%88",
    "talismans": "https://wiki.biligame.com/eldenring/%E6%8A%A4%E7%AC%A6%E4%B8%80%E8%A7%88",
    "items":   "https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88",
    "spells":  "https://wiki.biligame.com/eldenring/%E6%B3%95%E6%9C%AF%E4%B8%80%E8%A7%88",
    "ashes":   "https://wiki.biligame.com/eldenring/%E6%88%98%E7%81%B0%E4%B8%80%E8%A7%88",
}

# 过滤无效 / 非条目
BAD_PREFIXES = ("特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:", "帮助:", "Help:")
BAD_TITLES = set(["首页","武器一览","防具一览","护符一览","物品一览","法术一览","战灰一览"])

# ---------- 基础工具 ----------
def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def soup_of(url: str) -> BeautifulSoup:
    return BeautifulSoup(get_html(url), "html.parser")

def text_with_newlines(tag) -> str:
    if tag is None:
        return ""
    for br in tag.find_all("br"):
        br.replace_with("\n")
    txt = tag.get_text("\n", strip=True)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt

def parse_title_from_href(href: str) -> str:
    if "/index.php" in href:
        return unquote(parse_qs(urlparse(href).query).get("title", [""])[0])
    return unquote(href.split("/eldenring/", 1)[1])

def is_item_link(href: str) -> bool:
    if not href or not href.startswith("/eldenring/"):
        return False
    title = parse_title_from_href(href)
    if any(title.startswith(p) for p in BAD_PREFIXES):
        return False
    if title in BAD_TITLES:
        return False
    return bool(title.strip())

def pick_first_unique(index_url: str, n: int) -> list[tuple[str,str,str]]:
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    found, seen = [], set()
    for a in content.find_all("a", href=True):
        href = a["href"]
        if not is_item_link(href):
            continue
        title = parse_title_from_href(href).strip()
        name = a.get_text(strip=True) or title
        if not name or name in seen:
            continue
        seen.add(name)
        found.append((name, urljoin(base, href), title))
        if len(found) >= n:
            break
    return found

# ---------- 解析公共 ----------
def pair_by_sequence(strings) -> dict:
    out, it = {}, iter(strings)
    for k in it:
        v = next(it, "")
        k, v = str(k).strip(), str(v).strip()
        if k:
            out[k] = v
    return out

def ensure_dict(d: dict, name="") -> dict:
    defaults = {
        "name": name, "image": "", "source": "",
        "header_lines": [],      # 逐行部分
        "kv_tables": {},         # 若干张两列表格：{标题: {k:v}}
        "sections": {},          # 其他段落文本：{小标题: 文本}
    }
    x = dict(defaults); x.update(d or {})
    if "header_lines" not in x: x["header_lines"] = []
    if "kv_tables" not in x: x["kv_tables"] = {}
    if "sections" not in x: x["sections"] = {}
    return x

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def remove_orphan_numbers(lines: list[str], *numbers: str) -> list[str]:
    # 把右浮动的数值（例如“3（-/-）”“3.5”）若意外混入行，则剔除
    norm = lambda t: re.sub(r"\s+", "", t or "")
    garbage = {norm(x) for x in numbers if x}
    return [ln for ln in lines if norm(ln) not in garbage]

def extract_image_from_table(table) -> str:
    # 优先 .img-equip；否则取首个 <img>
    img = table.select_one("img.img-equip") or table.select_one("img")
    if img and img.get("src"):
        src = img["src"]
        return ("https:" + src) if src.startswith("//") else src
    return ""

def download_image(url: str, out_dir: pathlib.Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not url:
        return ""
    u = ("https:" + url) if url.startswith("//") else url
    cands = [u]
    m = re.search(r"/(\d+)px-", u)
    if m:
        tag = m.group(1)
        for sz in ("160","120","80"):
            if sz != tag:
                cands.append(u.replace(f"/{tag}px-", f"/{sz}px-"))
    for uu in cands:
        try:
            r = requests.get(uu, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            p = out_dir / f"icon{ext}"
            with open(p, "wb") as f:
                f.write(r.content)
            return str(p)
        except Exception:
            continue
    return ""

def md_table(title: str, kv: dict) -> str:
    if not kv:
        return ""
    rows = [f"### {title}", "", "| 项目 | 数值 |", "|:---|:---|"]
    for k, v in kv.items():
        rows.append(f"| {k} | {v} |")
    rows.append("")
    return "\n".join(rows)

def hardbreak(lines: list[str]) -> str:
    lines = [ln for ln in lines if ln is not None and ln != ""]
    return "  \n".join(lines)

def safe_slug(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

# ---------- 针对不同大类的解析器 ----------
def parse_weapon(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data

    whole = text_with_newlines(table)
    quality = ""
    m = re.search(r"武器品质[:：]?\s*([^\s\n]+)", whole)
    if m: quality = m.group(1).strip()

    # 左栏类型行（去掉右浮动数值）
    left_td = None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2 and len(list(tds[0].stripped_strings)) >= 2:
            left_td = tds[0]; break
    lines = []
    fp = wt = ""
    if left_td:
        left = BeautifulSoup(str(left_td), "html.parser")
        for n in left.select('[style*="float:right"]'): n.decompose()
        raw = [s.strip() for s in text_with_newlines(left).splitlines() if s.strip()]
        for ln in raw:
            if ln.startswith("消耗专注值") or ln.startswith("重量"):
                continue
            lines.append(ln)

    # FP/重量兜底
    m = re.search(r"消耗专注值\s*([^\n\r]+)", whole)
    if m: fp = m.group(1).strip()
    m = re.search(r"重量\s*([0-9.]+(?:[^\n\r]*)?)", whole)
    if m: wt = m.group(1).strip()
    lines = remove_orphan_numbers(lines, fp, wt)

    data["header_lines"] = [f"武器品质: {quality}", *lines, f"消耗专注值 {fp}".strip(), f"重量 {wt}".strip()]
    data["image"] = extract_image_from_table(table)

    # 四块表 + 附加信息
    rows = table.find_all("tr")
    i = 0
    def row(i): return rows[i].find_all("td") if 0 <= i < len(rows) else []
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])

        if "攻击力" in rtxt and "减伤率" in rtxt:
            t2 = row(i+1)
            if len(t2)==2:
                data["kv_tables"]["攻击力"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["kv_tables"]["防御时减伤率"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        if "能力加成" in rtxt and "必需能力值" in rtxt:
            t2 = row(i+1)
            if len(t2)==2:
                data["kv_tables"]["能力加成"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["kv_tables"]["必需能力值"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        if rtxt.strip()=="附加效果":
            t2 = row(i+1)
            if t2: data["sections"]["附加效果"] = text_with_newlines(t2[0]); i += 1

        if rtxt.strip()=="简介":
            t2 = row(i+1)
            if t2: data["sections"]["简介"] = text_with_newlines(t2[0]); i += 1

        if rtxt.strip()=="获取地点":
            t2 = row(i+1)
            if t2: data["sections"]["获取地点"] = text_with_newlines(t2[0]); i += 1

        if rtxt.strip().startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", rtxt.strip())
            if m: data["sections"]["专属战技"] = m.group(1).strip()
            t2 = row(i+1)
            if t2: data["sections"]["专属战技说明"] = text_with_newlines(t2[0]); i += 1

        if rtxt.strip()=="武器使用强化石类型":
            t2 = row(i+1)
            if t2: data["sections"]["武器使用强化石类型"] = text_with_newlines(t2[0]); i += 1

        i += 1

    return data

def parse_armor(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    whole = text_with_newlines(table)
    data["image"] = extract_image_from_table(table)

    # 重量
    wt = ""
    m = re.search(r"重量\s*([0-9.]+)", whole)
    if m: wt = m.group(1).strip()
    data["header_lines"] = [f"重量 {wt}"]

    # 左列两块表（减伤率 / 抵抗力）
    parts = table.find_all("tr")
    # 尝试找“减伤率”“抵抗力”标题所在的两个表
    kvt = {}
    for i, tr in enumerate(parts):
        t = text_with_newlines(tr).strip()
        if t=="减伤率":
            t2 = parts[i+1].find_all("td")
            if t2: kvt["减伤率"] = pair_by_sequence(list(t2[0].stripped_strings))
        if t=="抵抗力":
            t2 = parts[i+1].find_all("td")
            if t2: kvt["抵抗力"] = pair_by_sequence(list(t2[0].stripped_strings))
    data["kv_tables"].update(kvt)

    # 获取途径 + 右侧简介
    # 许多页面“获取途径”是独立段落
    for i, tr in enumerate(parts):
        t = text_with_newlines(tr).strip()
        if t=="获取途径":
            t2 = parts[i+1].find_all("td")
            if t2: data["sections"]["获取途径"] = text_with_newlines(t2[0])
    # 右侧说明块
    # 在很多页面紧邻图片下会有简介文本块，直接从页面全局抓
    right_desc = soup.select_one(".mw-parser-output p")
    if right_desc:
        para = right_desc.get_text(" ", strip=True)
        if para:
            data["sections"].setdefault("说明", para)
    return data

def parse_talisman(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["image"] = extract_image_from_table(table)
    whole = text_with_newlines(table)

    wt = ""
    m = re.search(r"重量\s*([0-9.]+)", whole)
    if m: wt = m.group(1).strip()
    if wt: data["header_lines"].append(f"重量 {wt}")

    # 道具效用、获取途径
    for i, tr in enumerate(table.find_all("tr")):
        title = text_with_newlines(tr).strip()
        t2 = (table.find_all("tr")[i+1].find_all("td") if i+1 < len(table.find_all("tr")) else [])
        if title == "道具效用" and t2:
            data["sections"]["道具效用"] = text_with_newlines(t2[0])
        if title == "获取途径" and t2:
            data["sections"]["获取途径"] = text_with_newlines(t2[0])

    # 右侧段落简介
    right_ps = [p.get_text(" ", strip=True) for p in soup.select(".mw-parser-output > p") if p.get_text(strip=True)]
    if right_ps:
        data["sections"].setdefault("简介", "\n".join(right_ps[:2]))
    return data

def parse_item(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["image"] = extract_image_from_table(table)
    # 顶部规格信息（逐行）
    top_lines = []
    # 左侧第一格经常是“消耗品/持有上限/收纳上限/消耗专注值”等
    left_txt = ""
    for tr in table.find_all("tr")[:2]:
        tds = tr.find_all("td")
        if tds:
            left_txt = text_with_newlines(tds[0])
            break
    if left_txt:
        for ln in [s.strip() for s in left_txt.splitlines() if s.strip()]:
            top_lines.append(ln)
    # 能力加成（有些物品会出现）
    for i, tr in enumerate(table.find_all("tr")):
        title = text_with_newlines(tr).strip()
        t2 = (table.find_all("tr")[i+1].find_all("td") if i+1 < len(table.find_all("tr")) else [])
        if title == "道具效用" and t2:
            data["sections"]["道具效用"] = text_with_newlines(t2[0])
        if title == "能力加成" and t2:
            data["kv_tables"]["能力加成"] = pair_by_sequence(list(t2[0].stripped_strings))
        if title == "获取途径" and t2:
            data["sections"]["获取途径"] = text_with_newlines(t2[0])
    data["header_lines"] = top_lines

    right_ps = [p.get_text(" ", strip=True) for p in soup.select(".mw-parser-output > p") if p.get_text(strip=True)]
    if right_ps:
        data["sections"].setdefault("简介", "\n".join(right_ps[:2]))
    return data

def parse_spell(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    # 法术页面右侧一般有一张参数表
    table = None
    for tb in soup.select(".mw-parser-output table"):
        t = tb.get_text(" ", strip=True)
        if any(x in t for x in ("类型","精耗","蓝耗","威力","占用记忆","专注值")):
            table = tb; break
    if table:
        data["image"] = extract_image_from_table(table)
        kv = {}
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th","td"])
            if len(cells)==2:
                k = normalize_spaces(cells[0].get_text(strip=True))
                v = normalize_spaces(cells[1].get_text(" ", strip=True))
                if k and v:
                    kv[k] = v
        # 把核心规格放到“逐行”
        lines = []
        for key in ("类型","占用记忆","专注值","精耗","蓝耗"):
            if key in kv:
                lines.append(f"{key} {kv[key]}")
        data["header_lines"] = lines
        # 其余参数保留到一张表
        for rm in ("类型","占用记忆","专注值","精耗","蓝耗","图标"):
            kv.pop(rm, None)
        if kv:
            data["kv_tables"]["参数"] = kv

    # 左侧“介绍 / 描述”段落
    intro = []
    for h in soup.select(".mw-parser-output h2"):
        title = h.get_text(strip=True)
        if title.startswith("介绍") or title.startswith("描述"):
            # 取到下一个同级前的段落
            sib = h.find_next_sibling()
            while sib and sib.name != "h2":
                if sib.name == "p":
                    txt = sib.get_text(" ", strip=True)
                    if txt: intro.append(txt)
                sib = sib.find_next_sibling()
            break
    if intro:
        data["sections"]["说明"] = "\n".join(intro[:3])
    return data

def parse_ash(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = ensure_dict({}, soup.select_one("h1.firstHeading").get_text(strip=True) if soup.select_one("h1.firstHeading") else "")
    table = soup.select_one(".mw-parser-output table.wikitable")
    if table:
        data["image"] = extract_image_from_table(table)
        # 左上段常有“可用武器类型/重量/道具效用/获取途径”
        blocks = table.find_all("tr")
        for i, tr in enumerate(blocks):
            title = text_with_newlines(tr).strip()
            t2 = (blocks[i+1].find_all("td") if i+1 < len(blocks) else [])
            if title and t2:
                if title in ("重量",):
                    m = re.search(r"([0-9.]+)", text_with_newlines(t2[0]))
                    if m: data["header_lines"].append(f"重量 {m.group(1)}")
                elif title in ("道具效用","获取途径"):
                    data["sections"][title] = text_with_newlines(t2[0])
        # 第一格一般写可用武器类别与“厚重/等”属性，直接塞进逐行
        first_td = table.find("td")
        if first_td:
            lines = [s.strip() for s in text_with_newlines(first_td).splitlines() if s.strip()]
            data["header_lines"] = lines + data["header_lines"]

    # 右侧说明
    right_ps = [p.get_text(" ", strip=True) for p in soup.select(".mw-parser-output > p") if p.get_text(strip=True)]
    if right_ps:
        data["sections"].setdefault("说明", "\n".join(right_ps[:2]))
    return data

PARSERS = {
    "weapons": parse_weapon,
    "armors": parse_armor,
    "talismans": parse_talisman,
    "items": parse_item,
    "spells": parse_spell,
    "ashes": parse_ash,
}

# ---------- 写入仓库 ----------
def wipe_repo_except(keep: list[str]):
    root = pathlib.Path(".")
    for p in list(root.iterdir()):
        if p.name in keep or p.name == ".git":
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try: p.unlink()
            except Exception: pass

def write_repo(all_data: dict):
    """
    all_data: {category: [dict, dict, ...]}
    """
    root = pathlib.Path(".")
    (root / "items").mkdir(parents=True, exist_ok=True)

    # 首页
    cat_links = []
    zh_names = {
        "weapons":"武器","armors":"防具","talismans":"护符",
        "items":"物品","spells":"法术","ashes":"战灰",
    }
    for cat in ["weapons","armors","talismans","items","spells","ashes"]:
        if cat in all_data:
            cat_links.append(f"- [{zh_names[cat]}](items/{cat}/README.md)")
    (root/"README.md").write_text(
        "# 艾尔登法环 · 物品手册（样例各 3 条）\n\n" + "\n".join(cat_links) + "\n",
        encoding="utf-8"
    )

    for cat, items in all_data.items():
        md_root = root/"items"/cat
        md_root.mkdir(parents=True, exist_ok=True)
        # 分类 README
        lines = [f"# {zh_names.get(cat, cat)}（样例）",""]
        for it in items:
            lines.append(f"- [{it['name']}](./{safe_slug(it['name'])}.md)")
        (md_root/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

        # 条目
        for it in items:
            slug = safe_slug(it["name"])
            assets_dir = root/"assets"/cat/slug
            rel = ""
            img_path = download_image(it.get("image",""), assets_dir)
            if img_path:
                rel_path = os.path.relpath(img_path, md_root)
                rel = rel_path.replace(os.sep, "/")  # 避免 f-string 里写反斜杠

            body = [f"# {it['name']}"]
            if rel:
                body.append(f"![icon]({rel})")
            body.append("")
            if it.get("header_lines"):
                body.append(hardbreak(it["header_lines"]))
                body.append("")
            # 表格
            for title, kv in it.get("kv_tables", {}).items():
                body.append(md_table(title, kv))
            # 段落
            for title, txt in it.get("sections", {}).items():
                if title in ("简介","说明"):
                    block = "> " + "\n> ".join(txt.splitlines())
                    body.append(block + "\n")
                else:
                    body.append(f"**{title}**：{txt}\n")

            # 低调署名（合规必须）
            source = it.get("source","")
            if source:
                body.append(f"> 来源：本文整合自公开百科页面（保留署名以符合 CC BY-NC-SA 4.0）。\n> {source}")

            (md_root/f"{slug}.md").write_text("\n".join(body), encoding="utf-8")

def fetch_category(cat_key: str, per: int) -> list[dict]:
    index_url = INDEX[cat_key]
    triples = pick_first_unique(index_url, per)
    results = []
    for name, url, _t in triples:
        try:
            html = get_html(url)
            data = PARSERS[cat_key](html)
            if not data.get("name"):
                data["name"] = name
            data["source"] = url  # 低调尾注
            results.append(data)
        except Exception as e:
            sys.stderr.write(f"[warn] 解析失败：{name} -> {url} -> {e}\n")
        time.sleep(DELAY)
    return results

def main():
    # 清空仓库，仅保留工作流与脚本
    wipe_repo_except([".github", "scripts"])

    all_data = {}
    for key in ["weapons","armors","talismans","items","spells","ashes"]:
        try:
            all_data[key] = fetch_category(key, PER_CAT)
        except Exception as e:
            sys.stderr.write(f"[warn] 抓取分类失败：{key} -> {e}\n")

    write_repo(all_data)

if __name__ == "__main__":
    main()
