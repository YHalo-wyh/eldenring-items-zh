#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 说明：抓“武器一览”里出现的前3个武器 → 解析 → 写入 JSON 和 Markdown
# 依赖：pip install requests beautifulsoup4

import json, re, sys, time, pathlib
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://wiki.biligame.com/eldenring/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88"
LIMIT = 3
DELAY = 0.8
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/1.0"}
BAD_PREFIXES = ("特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:", "帮助:", "Help:")

def get_html(url: str, timeout: float = 25.0) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout); r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
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

def pair_by_sequence(strings):
    out, it = {}, iter(strings)
    for k in it:
        v = next(it, ""); k, v = str(k).strip(), str(v).strip()
        if k: out[k] = v
    return out

def parse_item_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser"); data = {}
    h1 = soup.select_one("h1.firstHeading")
    data["name"] = h1.get_text(strip=True) if h1 else ""
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table: return ensure_keys(data)
    rows = table.find_all("tr"); row_tds = lambda i: rows[i].find_all("td") if 0 <= i < len(rows) else []
    i = 0
    while i < len(rows):
        row = rows[i]
        row_txt = text_with_newlines(row)
        tds = row.find_all("td")

        if i <= 2 and ("武器品质" in row_txt or "品质" in row_txt):
            m = re.search(r"武器品质[:：]\s*([^\s\n]+)", row_txt)
            if m: data["quality"] = m.group(1)

        if len(tds) == 2 and i <= 4:
            left, right = tds
            img = right.select_one("img")
            if img and img.get("src"):
                src = img["src"]
                data["image"] = ("https:" + src) if src.startswith("//") else src
            left_txt = text_with_newlines(left)
            fp = re.search(r"消耗专注值\s*([^\n]+)", left_txt)
            wt = re.search(r"重量\s*([^\n]+)", left_txt)
            block = left_txt
            if fp: block = block.replace(fp.group(0), "").strip()
            if wt: block = block.replace(wt.group(0), "").strip()
            lines = [ln for ln in block.splitlines() if ln.strip()][:4]
            data["type_info"] = {
                "lines": lines,
                "fp": fp.group(1).strip() if fp else "",
                "weight": wt.group(1).strip() if wt else ""
            }

        if "攻击力" in row_txt and "减伤率" in row_txt:
            t2 = row_tds(i+1)
            if len(t2) == 2:
                data["attack"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["guard"]  = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        if "能力加成" in row_txt and "必需能力值" in row_txt:
            t2 = row_tds(i+1)
            if len(t2) == 2:
                data["scaling"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["requirements"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        if row_txt.strip() == "附加效果":
            t2 = row_tds(i+1)
            if t2: data["extra_effect"] = text_with_newlines(t2[0]); i += 1

        if row_txt.strip() == "简介":
            t2 = row_tds(i+1)
            if t2: data["intro"] = text_with_newlines(t2[0]); i += 1

        if row_txt.strip() == "获取地点":
            t2 = row_tds(i+1)
            if t2: data["location"] = text_with_newlines(t2[0]); i += 1

        if row_txt.strip().startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", row_txt.strip())
            if m: data["ash_of_war"] = m.group(1).strip()
            t2 = row_tds(i+1)
            if t2: data["ash_desc"] = text_with_newlines(t2[0]); i += 1

        if row_txt.strip() == "武器使用强化石类型":
            t2 = row_tds(i+1)
            if t2: data["upgrade"] = text_with_newlines(t2[0]); i += 1

        i += 1

    return ensure_keys(data)

def ensure_keys(d: dict) -> dict:
    defaults = {
        "quality":"", "image":"", "type_info":{"lines":[],"fp":"","weight":""},
        "attack":{}, "guard":{}, "scaling":{}, "requirements":{},
        "extra_effect":"", "intro":"", "location":"", "ash_of_war":"", "ash_desc":"", "upgrade":""
    }
    out = dict(defaults); out.update(d); out.setdefault("name",""); return out

def is_item_link(href: str) -> bool:
    if not href or not href.startswith("/eldenring/"): return False
    if "/index.php" in href:
        title = unquote(parse_qs(urlparse(href).query).get("title", [""])[0])
    else:
        title = unquote(href.split("/eldenring/", 1)[1])
    if any(title.startswith(p) for p in BAD_PREFIXES): return False
    if title in ("首页","武器一览","防具一览","护符一览","物品一览","法术一览"): return False
    return bool(title.strip())

def pick_first_n_items(index_url: str, n: int = 3):
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    found, seen = [], set()
    for a in content.find_all("a", href=True):
        if is_item_link(a["href"]):
            url = urljoin(base, a["href"]); name = a.get_text(strip=True) or url
            if (name,url) in seen: continue
            seen.add((name,url)); found.append((name,url))
            if len(found) >= n: break
    return found

def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def write_outputs(items: list):
    root = pathlib.Path(".")
    # 1) 写 JSON（方便后续当“数据真源”）
    data_dir = root / "data"; data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "items_sample.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) 写 Markdown（在仓库里直观预览）
    md_root = root / "items" / "weapons" / "samples"; md_root.mkdir(parents=True, exist_ok=True)
    index_lines = ["# 武器样例（3）", ""]
    for it in items:
        fname = safe_filename(it["name"]) + ".md"
        index_lines.append(f"- [{it['name']}]({fname})")
        desc_block = ("> " + "\n> ".join(it["intro"].splitlines())) if it.get("intro") else ""
        body = (
            f"# {it['name']}\n"
            f"![icon]({it.get('image','')})\n\n"
            f"- 品质：{it.get('quality','')}\n"
            f"- 类型：{'、'.join(it.get('type_info',{}).get('lines', []))}\n"
            f"- FP：{it.get('type_info',{}).get('fp','')}  |  重量：{it.get('type_info',{}).get('weight','')}\n\n"
            f"{desc_block}\n\n"
            f"**获取地点**：{it.get('location','')}\n\n"
            f"**专属战技**：{it.get('ash_of_war','')}  \n{it.get('ash_desc','')}\n"
        )
        (md_root / fname).write_text(body, encoding="utf-8")
    (md_root / "README.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    # 3) 根目录集中署名（合规且不打扰每页）
    attr = (
        "# Attribution / 来源说明\n\n"
        "- 本仓库中的游戏文本与术语来自 **艾尔登法环**，页面数据整理自公开 Wiki 页面。\n"
        "- 为尊重原作者与站点协议，保留集中署名与来源说明，非商业用途。\n"
    )
    (root / "ATTRIBUTION.md").write_text(attr, encoding="utf-8")

def main():
    try:
        pairs = pick_first_n_items(INDEX_URL, LIMIT)
    except Exception as e:
        sys.stderr.write(f"[warn] 目录页抓取失败：{e}\n")
        base = "https://wiki.biligame.com/eldenring/"
        pairs = [("鲜血旋流", base+"%E9%B2%9C%E8%A1%80%E6%97%8B%E6%B5%81"),
                 ("王室巨剑", base+"%E7%8E%8B%E5%AE%A4%E5%B7%A8%E5%89%91"),
                 ("白王剑",   base+"%E7%99%BD%E7%8E%8B%E5%89%91")]
    results = []
    for name, url in pairs:
        try:
            data = parse_item_html(get_html(url))
            if not data.get("name"): data["name"] = name
            results.append(data)
        except Exception as e:
            sys.stderr.write(f"[warn] 解析失败：{name} -> {url} -> {e}\n")
        time.sleep(DELAY)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    write_outputs(results)

if __name__ == "__main__":
    main()
