#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 抓取“武器一览”里标题不重复的前3个武器 → 下载首图到本地 → 生成仅3条样例目录/MD
# 依赖：pip install requests beautifulsoup4

import json, re, sys, time, pathlib, os
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
    rows = table.find_all("tr")
    def row_tds(i): return rows[i].find_all("td") if 0 <= i < len(rows) else []
    i = 0
    while i < len(rows):
        row = rows[i]; row_txt = text_with_newlines(row); tds = row.find_all("td")
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

def parse_title_from_href(href: str) -> str:
    if "/index.php" in href:
        return unquote(parse_qs(urlparse(href).query).get("title", [""])[0])
    return unquote(href.split("/eldenring/", 1)[1])

def is_item_link(href: str) -> bool:
    if not href or not href.startswith("/eldenring/"): return False
    title = parse_title_from_href(href)
    if any(title.startswith(p) for p in BAD_PREFIXES): return False
    if title in ("首页","武器一览","防具一览","护符一览","物品一览","法术一览"): return False
    return bool(title.strip())

def pick_first_n_items_unique(index_url: str, n: int = 3):
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    found, seen_titles = [], set()
    for a in content.find_all("a", href=True):
        href = a["href"]
        if not is_item_link(href): continue
        title = parse_title_from_href(href).strip()
        if title in seen_titles: continue     # ← 关键：按页面标题去重
        seen_titles.add(title)
        url = urljoin(base, href)
        name = a.get_text(strip=True) or title
        found.append((name, url, title))
        if len(found) >= n: break
    return found

def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def upgrade_thumb(url: str, size: int = 256) -> str:
    # 将 /80px-xxx.png 提升到 /256px-xxx.png（若不存在则仍可 404 回退到原图下载）
    return re.sub(r"/(\d+)px-", f"/{size}px-", url)

def download_image(url: str, out_dir: pathlib.Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    # 优先用大些的缩略图
    url_try = upgrade_thumb(url, 256)
    try_urls = [url_try, url]
    for u in try_urls:
        uu = u if u.startswith("http") else ("https:" + u if u.startswith("//") else u)
        try:
            r = requests.get(uu, headers=HEADERS, timeout=25)
            r.raise_for_status()
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            p = out_dir / f"icon{ext}"
            with open(p, "wb") as f:
                f.write(r.content)
            return str(p)
        except Exception:
            continue
    return ""

def wipe_repo_except(keep: list):
    """删除仓库根目录下的所有文件/目录，除了 keep 名称列表。"""
    root = pathlib.Path(".")
    for p in root.iterdir():
        if p.name in keep: continue
        if p.name == ".git": continue
        if p.is_dir():
            for _ in range(3):
                try:
                    import shutil; shutil.rmtree(p, ignore_errors=False)
                    break
                except Exception: time.sleep(0.2)
        else:
            try: p.unlink()
            except Exception: pass

def write_repo(items: list):
    # 清空（保留 .github 与 scripts）
    wipe_repo_except([".github", "scripts"])

    root = pathlib.Path(".")
    (root / "README.md").write_text(
        "# 艾尔登法环 · 物品手册（演示样例）\n\n- 目录： [武器（样例）](items/weapons/README.md)\n",
        encoding="utf-8"
    )

    # 列表页
    md_root = root / "items" / "weapons"; md_root.mkdir(parents=True, exist_ok=True)
    lines = ["# 武器（样例，仅3件）", ""]
    for it in items:
        lines.append(f"- [{it['name']}](./{safe_filename(it['name'])}.md)")
    (md_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 逐个物品页 + 本地图片
    for it in items:
        slug = safe_filename(it["name"])
        assets_dir = root / "assets" / "weapons" / slug
        img_url = it.get("image","")
        img_path = download_image(img_url, assets_dir) if img_url else ""
        rel_img = os.path.relpath(img_path, md_root) if img_path else ""

        desc_block = ""
        if it.get("intro"):
            desc_block = "> " + "\n> ".join(it["intro"].splitlines())

        body = (
            f"# {it['name']}\n"
            f"{f'![icon]({rel_img})' if rel_img else ''}\n\n"
            f"- 品质：{it.get('quality','')}\n"
            f"- 类型：{'、'.join(it.get('type_info',{}).get('lines', []))}\n"
            f"- FP：{it.get('type_info',{}).get('fp','')}  |  重量：{it.get('type_info',{}).get('weight','')}\n\n"
            f"{desc_block}\n\n"
            f"**获取地点**：{it.get('location','')}\n\n"
            f"**专属战技**：{it.get('ash_of_war','')}  \n{it.get('ash_desc','')}\n"
        )
        (md_root / f"{slug}.md").write_text(body, encoding="utf-8")

    # 可选：集中署名（合规、低调）
    (root / "ATTRIBUTION.md").write_text(
        "# Attribution\n\n本仓库中演示样例的文本与术语整理自公开 Wiki 页面，仅供非商业研究与学习。\n",
        encoding="utf-8"
    )

def main():
    try:
        triples = pick_first_n_items_unique(INDEX_URL, LIMIT)
    except Exception as e:
        sys.stderr.write(f"[warn] 目录页抓取失败：{e}\n")
        base = "https://wiki.biligame.com/eldenring/"
        triples = [
            ("鲜血旋流", base+"%E9%B2%9C%E8%A1%80%E6%97%8B%E6%B5%81", "鲜血旋流"),
            ("王室巨剑", base+"%E7%8E%8B%E5%AE%A4%E5%B7%A8%E5%89%91", "王室巨剑"),
            ("使命短刀", base+"%E4%BD%BF%E5%91%BD%E7%9F%AD%E5%88%80", "使命短刀"),
        ]
    results = []
    for name, url, _title in triples:
        try:
            data = parse_item_html(get_html(url))
            if not data.get("name"): data["name"] = name
            results.append(data)
        except Exception as e:
            sys.stderr.write(f"[warn] 解析失败：{name} -> {url} -> {e}\n")
        time.sleep(DELAY)

    write_repo(results)

if __name__ == "__main__":
    main()
