#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为每个大类各抓 3 个样例到仓库（含本地图片、Markdown），
大类：武器 / 防具 / 护符 / 物品 / 法术 / 战灰
依赖：pip install requests beautifulsoup4
"""

import os, re, sys, time, pathlib
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/2.0"}
DELAY = 0.8

BASE = "https://wiki.biligame.com/eldenring/"
CATS = {
    "weapons":    {"index": BASE + "%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88", "out": "items/weapons",   "limit": 3},
    "armors":     {"index": BASE + "%E9%98%B2%E5%85%B7%E4%B8%80%E8%A7%88", "out": "items/armors",    "limit": 3},
    "talismans":  {"index": BASE + "%E6%8A%A4%E7%AC%A6%E4%B8%80%E8%A7%88", "out": "items/talismans", "limit": 3},
    "consumables":{"index": BASE + "%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88", "out": "items/consumables","limit": 3},
    "spells":     {"index": BASE + "%E6%B3%95%E6%9C%AF%E4%B8%80%E8%A7%88", "out": "items/spells",    "limit": 3},
    "ashes":      {"index": BASE + "%E6%88%98%E7%81%B0%E4%B8%80%E8%A7%88", "out": "items/ashes",     "limit": 3},
}

BAD_PREFIXES = ("特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:", "帮助:", "Help:")
SKIP_HINTS   = ("一览", "目录", "类", "分类", "按", "Category:")

# ---------------- HTTP / soup ----------------
def get_html(url: str, timeout=25.0) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout); r.raise_for_status()
    if not r.encoding or r.encoding.lower()=="iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def soup_of(url: str) -> BeautifulSoup:
    return BeautifulSoup(get_html(url), "html.parser")

def text_with_newlines(tag) -> str:
    if tag is None: return ""
    for br in tag.find_all("br"): br.replace_with("\n")
    txt = tag.get_text("\n", strip=True)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt

# ------------- small helpers -------------
def parse_title_from_href(href: str) -> str:
    if "/index.php" in href:
        return unquote(parse_qs(urlparse(href).query).get("title", [""])[0])
    return unquote(href.split("/eldenring/", 1)[1])

def is_probably_item_link(href: str, text: str) -> bool:
    if not href or not href.startswith("/eldenring/"): return False
    title = parse_title_from_href(href)
    if any(title.startswith(p) for p in BAD_PREFIXES): return False
    # 跳过看起来像“目录/一览/类”的页面
    if any(h in title for h in SKIP_HINTS) or any(h in (text or "") for h in SKIP_HINTS):
        return False
    return True

def try_download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25); r.raise_for_status()
        return r.content
    except Exception:
        return None

def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def hardbreak_block(lines):
    lines = [ln for ln in lines if ln is not None and ln != ""]
    return "  \n".join(lines)

def md_table_from_pairs(title: str, kv: dict) -> str:
    if not kv: return ""
    rows = [f"### {title}", "", "| 项目 | 数值 |", "|:---|:---|"]
    for k, v in kv.items():
        rows.append(f"| {k} | {v} |")
    rows.append("")
    return "\n".join(rows)

# ---------------- parsing: generic ----------------
def extract_image(table: BeautifulSoup) -> str:
    # 优先 .img-equip；否则取第一张 >= 60px 的图片
    img = table.select_one("img.img-equip")
    if not img:
        for im in table.select("img"):
            w = int(im.get("width") or 0)
            if w >= 60: img = im; break
    if img and img.get("src"):
        src = img["src"]
        return ("https:" + src) if src.startswith("//") else src
    return ""

def download_image(url: str, out_dir: pathlib.Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not url: return ""
    u = ("https:" + url) if url.startswith("//") else url
    candidates = [u]
    m = re.search(r"/(\d+)px-", u)
    if m:
        size_tag = m.group(1)
        for sz in ["120","160","80"]:
            if sz != size_tag: candidates.append(u.replace(f"/{size_tag}px-", f"/{sz}px-"))
    for uu in candidates:
        content = try_download(uu)
        if content:
            from urllib.parse import urlparse
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            p = out_dir / f"icon{ext}"
            with open(p, "wb") as f: f.write(content)
            return str(p)
    return ""

def pair_by_sequence(strings):
    out, it = {}, iter(strings)
    for k in it:
        v = next(it, "")
        k, v = str(k).strip(), str(v).strip()
        if k: out[k] = v
    return out

def parse_kv_sections(table: BeautifulSoup):
    """把像“攻击力/减伤率”、“能力加成/必需能力值”这种成对表头 + 下一行数据解析出来"""
    rows = table.find_all("tr")
    sections = {}  # title -> dict
    def row_tds(i): return rows[i].find_all("td") if 0 <= i < len(rows) else []
    i = 0
    while i < len(rows):
        tds = rows[i].find_all("td")
        if len(tds) == 2:
            left, right = tds
            lt, rt = text_with_newlines(left), text_with_newlines(right)
            # 如果这一行像是两个小标题，下一行是两列的值对
            nxt = row_tds(i+1)
            if len(nxt) == 2 and (len(list(nxt[0].stripped_strings)) >= 2 or len(list(nxt[1].stripped_strings)) >= 2):
                sections[lt] = pair_by_sequence(list(nxt[0].stripped_strings))
                sections[rt] = pair_by_sequence(list(nxt[1].stripped_strings))
                i += 1
        i += 1
    return sections

def parse_text_blocks(table: BeautifulSoup):
    """抓取常见的长文本块：简介/获取/效果/说明等"""
    rows = table.find_all("tr")
    blocks = {}
    def row_tds(i): return rows[i].find_all("td") if 0 <= i < len(rows) else []
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i]).strip()
        if rtxt in ("简介","获取地点","效果","技能","战技","说明","备注","获取方法","可注入武器","可用武器","可使用武器","记忆空间","记忆槽数"):
            t2 = row_tds(i+1)
            if t2:
                blocks[rtxt] = text_with_newlines(t2[0])
                i += 1
        i += 1
    return blocks

def parse_quality(table: BeautifulSoup) -> str:
    txt = text_with_newlines(table)
    for key in ("武器品质","防具品质","护符品质","品质"):
        m = re.search(fr"{key}[:：]?\s*([^\s\n]+)", txt)
        if m: return m.group(1).strip()
    return ""

def parse_type_lines_and_fp_wt(table: BeautifulSoup):
    """抽出左侧类型行 + FP/重量（同武器解析法：去掉 float:right 数值）"""
    fp = wt = ""
    lines = []
    left_td = None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds)==2 and len(list(tds[0].stripped_strings))>=2:
            left_td = tds[0]; break
    if left_td:
        clone = BeautifulSoup(str(left_td), "html.parser")
        for n in clone.select('[style*="float:right"]'): n.decompose()
        raw = [ln.strip() for ln in text_with_newlines(clone).splitlines() if ln.strip()]
        for ln in raw:
            if ln.startswith("消耗专注值") or ln.startswith("重量"): continue
            lines.append(ln)
    full = text_with_newlines(table)
    m = re.search(r"消耗专注值\s*([^\n\r]+)", full)
    if m: fp = m.group(1).strip()
    m = re.search(r"重量\s*([0-9.]+(?:[^\n\r]*)?)", full)
    if m: wt = m.group(1).strip()
    # 清理可能混入的孤儿数值
    norm = lambda s: re.sub(r"\s+","", s or "")
    lines = [ln for ln in lines if norm(ln) not in {norm(fp), norm(wt)}]
    return lines, fp, wt

# ---------------- specialized: weapon (与之前一致风格) ----------------
def parse_weapon_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"name": "", "quality":"", "image":"", "type_info":{"lines":[],"fp":"","weight":""},
            "attack":{}, "guard":{}, "scaling":{}, "requirements":{},
            "extra_effect":"", "intro":"", "location":"", "ash_of_war":"", "ash_desc":"", "upgrade":""}
    h1 = soup.select_one("h1.firstHeading")
    data["name"] = h1.get_text(strip=True) if h1 else ""

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table: return data

    data["image"]   = extract_image(table)
    data["quality"] = parse_quality(table)
    lines, fp, wt   = parse_type_lines_and_fp_wt(table)
    data["type_info"] = {"lines": lines, "fp": fp, "weight": wt}

    # 抓四块 + 文本
    rows = table.find_all("tr")
    def row_tds(i): return rows[i].find_all("td") if 0 <= i < len(rows) else []
    i=0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i]).strip()
        if "攻击力" in rtxt and "减伤率" in rtxt:
            t2 = row_tds(i+1)
            if len(t2)==2:
                data["attack"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["guard"]  = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1
        if "能力加成" in rtxt and "必需能力值" in rtxt:
            t2 = row_tds(i+1)
            if len(t2)==2:
                data["scaling"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["requirements"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1
        if rtxt == "附加效果":
            t2 = row_tds(i+1)
            if t2: data["extra_effect"] = text_with_newlines(t2[0]); i += 1
        if rtxt == "简介":
            t2 = row_tds(i+1)
            if t2: data["intro"] = text_with_newlines(t2[0]); i += 1
        if rtxt == "获取地点":
            t2 = row_tds(i+1)
            if t2: data["location"] = text_with_newlines(t2[0]); i += 1
        if rtxt.startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", rtxt)
            if m: data["ash_of_war"] = m.group(1).strip()
            t2 = row_tds(i+1)
            if t2: data["ash_desc"] = text_with_newlines(t2[0]); i += 1
        if rtxt == "武器使用强化石类型":
            t2 = row_tds(i+1)
            if t2: data["upgrade"] = text_with_newlines(t2[0]); i += 1
        i += 1
    # 去掉与战技重复的类型行
    ash = data.get("ash_of_war","")
    data["type_info"]["lines"] = [ln for ln in data["type_info"]["lines"] if ln.strip() and ln.strip()!=ash]
    return data

# ---------------- generic item page (防具/护符/物品/法术/战灰) ----------------
def parse_generic_item(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.firstHeading")
    name = h1.get_text(strip=True) if h1 else ""

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return {"name": name, "quality":"", "image":"", "type_lines":[], "fp":"", "weight":"",
                "sections":{}, "blocks":{}}

    quality = parse_quality(table)
    image   = extract_image(table)
    type_lines, fp, wt = parse_type_lines_and_fp_wt(table)
    sections = parse_kv_sections(table)   # 若存在“攻击力/减伤率”等会出现在这里
    blocks   = parse_text_blocks(table)   # 简介/效果/获取…

    return {"name": name, "quality": quality, "image": image,
            "type_lines": type_lines, "fp": fp, "weight": wt,
            "sections": sections, "blocks": blocks}

# -------------- pick 3 real items from index --------------
def pick_three_real_items(index_url: str):
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    picked, seen = [], set()
    for a in content.find_all("a", href=True):
        href, text = a["href"], a.get_text(strip=True)
        if not is_probably_item_link(href, text): continue
        title = parse_title_from_href(href).strip()
        if title in seen: continue
        seen.add(title)
        url = urljoin(base, href)
        # 试探是否真实条目（有信息表）
        try:
            html = get_html(url)
            soup_item = BeautifulSoup(html, "html.parser")
            if soup_item.select_one(".mw-parser-output table.wikitable"):
                picked.append((text or title, url))
        except Exception:
            continue
        if len(picked) >= 3: break
    return picked

# -------------- write helpers --------------
def wipe_repo_except(keep):
    root = pathlib.Path(".")
    for p in root.iterdir():
        if p.name in keep or p.name == ".git": continue
        if p.is_dir():
            import shutil; shutil.rmtree(p, ignore_errors=True)
        else:
            try: p.unlink()
            except Exception: pass

def write_root_readme(cat_items):
    root = pathlib.Path(".")
    lines = ["# 艾尔登法环 · 物品手册（演示样例：各大类 3 件）", ""]
    for key, cfg in CATS.items():
        outdir = cfg["out"]
        name_zh = {"weapons":"武器","armors":"防具","talismans":"护符","consumables":"物品","spells":"法术","ashes":"战灰"}[key]
        lines.append(f"- [{name_zh}]({outdir}/README.md)")
    root.joinpath("README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_cat_readme(cat_key, items_parsed):
    name_zh = {"weapons":"武器","armors":"防具","talismans":"护符","consumables":"物品","spells":"法术","ashes":"战灰"}[cat_key]
    outdir = pathlib.Path(CATS[cat_key]["out"]); outdir.mkdir(parents=True, exist_ok=True)
    lines = [f"# {name_zh}（样例，仅3件）", ""]
    for it in items_parsed:
        lines.append(f"- [{it['name']}](./{safe_filename(it['name'])}.md)")
    outdir.joinpath("README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_weapon_md(cat_key, item):
    outdir = pathlib.Path(CATS[cat_key]["out"]); outdir.mkdir(parents=True, exist_ok=True)
    slug = safe_filename(item["name"])
    assets_dir = pathlib.Path("assets") / "weapons" / slug
    img_path = download_image(item.get("image",""), assets_dir)
    rel_img = os.path.relpath(img_path, outdir) if img_path else ""
    # 逐行（品质/类型/战技/FP/重量）
    type_lines = [ln for ln in (item.get("type_info",{}).get("lines",[]) or []) if ln.strip() and ln.strip()!=item.get("ash_of_war","")]
    section1 = hardbreak_block([f"武器品质: {item.get('quality','')}", *type_lines])
    section2 = hardbreak_block([item.get("ash_of_war","")]) if item.get("ash_of_war") else ""
    section3 = hardbreak_block([f"消耗专注值 {item.get('type_info',{}).get('fp','')}".strip(),
                                f"重量 {item.get('type_info',{}).get('weight','')}".strip()])

    body = [f"# {item['name']}"]
    if rel_img: body.append(f"![icon]({rel_img})")
    body += ["", section1, "", section2] if section2 else ["", section1, ""]
    body += [section3, ""]
    body.append(md_table_from_pairs("攻击力", item.get("attack", {})))
    body.append(md_table_from_pairs("防御时减伤率", item.get("guard", {})))
    body.append(md_table_from_pairs("能力加成", item.get("scaling", {})))
    body.append(md_table_from_pairs("必需能力值", item.get("requirements", {})))
    if item.get("extra_effect"): body.append(f"**附加效果**：{item['extra_effect']}\n")
    if item.get("intro"): body.append("> " + "\n> ".join(item["intro"].splitlines()) + "\n")
    if item.get("location"): body.append(f"**获取地点**：{item['location']}\n")
    if item.get("ash_desc"): body.append(f"**专属战技说明**：{item['ash_desc']}\n")
    if item.get("upgrade"): body.append(f"**武器使用强化石类型**：{item['upgrade']}\n")
    outdir.joinpath(f"{slug}.md").write_text("\n".join([b for b in body if b is not None]), encoding="utf-8")

def write_generic_md(cat_key, item):
    outdir = pathlib.Path(CATS[cat_key]["out"]); outdir.mkdir(parents=True, exist_ok=True)
    slug = safe_filename(item["name"])
    assets_dir = pathlib.Path("assets") / cat_key / slug
    img_path = download_image(item.get("image",""), assets_dir)
    rel_img = os.path.relpath(img_path, outdir) if img_path else ""
    # 逐行（品质/类型/FP/重量）
    section1 = hardbreak_block([f"品质: {item.get('quality','')}", *item.get("type_lines",[])])
    section2 = hardbreak_block([f"消耗专注值 {item.get('fp','')}".strip(), f"重量 {item.get('weight','')}".strip()])
    body = [f"# {item['name']}"]
    if rel_img: body.append(f"![icon]({rel_img})")
    body += ["", section1, "", section2, ""]
    # 所有键值 section 按表格渲染
    for sec_title, kv in item.get("sections",{}).items():
        body.append(md_table_from_pairs(sec_title, kv))
    # 文本块
    for key in ("简介","效果","技能","说明","获取地点","获取方法"):
        val = item.get("blocks",{}).get(key)
        if val:
            body.append(f"**{key}**：\n")
            body.append("> " + "\n> ".join(val.splitlines()) + "\n")
    outdir.joinpath(f"{slug}.md").write_text("\n".join([b for b in body if b is not None]), encoding="utf-8")

# ---------------- main ----------------
def main():
    # 清仓（保留 CI 和脚本）
    wipe_repo_except([".github", "scripts"])

    root = pathlib.Path(".")
    parsed_by_cat = {}

    # 各大类抓取 3 个真实条目
    for cat_key, cfg in CATS.items():
        picked = pick_three_real_items(cfg["index"])
        results = []
        for name, url in picked:
            try:
                html = get_html(url)
                if cat_key == "weapons":
                    data = parse_weapon_page(html)
                else:
                    data = parse_generic_item(html)
                if not data.get("name"): data["name"] = name
                results.append(data)
            except Exception as e:
                sys.stderr.write(f"[warn] 解析失败：{name} -> {url} -> {e}\n")
            time.sleep(DELAY)
        parsed_by_cat[cat_key] = results

    # 根 README
    write_root_readme(parsed_by_cat)

    # 各大类 README + 条目
    for cat_key, items in parsed_by_cat.items():
        write_cat_readme(cat_key, items)
        for it in items:
            if cat_key == "weapons":
                write_weapon_md(cat_key, it)
            else:
                write_generic_md(cat_key, it)

    # Attribution（低调）
    pathlib.Path("ATTRIBUTION.md").write_text(
        "# Attribution\n\n本仓库演示样例的文本与术语整理自公开资料，仅供非商业研究与学习。\n",
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
