#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 抓“武器一览”里标题不重复的前3件武器 → 解析整块信息 → 下载首图到本地 → 生成仅3条样例目录/MD
# 依赖：pip install requests beautifulsoup4

import json, re, sys, time, pathlib, os
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

INDEX_URL = "https://wiki.biligame.com/eldenring/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88"
LIMIT = 3
DELAY = 0.8
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/1.1"}
BAD_PREFIXES = ("特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:", "帮助:", "Help:")

# -------------------- HTTP & HTML helpers --------------------
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
        v = next(it, "")
        k, v = str(k).strip(), str(v).strip()
        if k:
            out[k] = v
    return out

# -------------------- Parse item page --------------------
def parse_item_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser"); data = {}

    h1 = soup.select_one("h1.firstHeading")
    data["name"] = h1.get_text(strip=True) if h1 else ""

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:  # 兜底，返回空结构
        return ensure_keys(data)

    rows = table.find_all("tr")
    row_tds = lambda i: rows[i].find_all("td") if 0 <= i < len(rows) else []
    i = 0
    while i < len(rows):
        row = rows[i]
        row_txt = text_with_newlines(row)
        tds = row.find_all("td")

        # 武器品质
        if i <= 2 and ("武器品质" in row_txt or "品质" in row_txt):
            m = re.search(r"武器品质[:：]\s*([^\s\n]+)", row_txt)
            if m: data["quality"] = m.group(1)

        # 左列（类型/战技） + 右列（首图）
        if len(tds) == 2 and i <= 4:
            left, right = tds
            img = right.select_one("img")
            if img and img.get("src"):
                src = img["src"]
                data["image"] = ("https:" + src) if src.startswith("//") else src

            left_txt = text_with_newlines(left)
            fp = re.search(r"消耗专注值\s*([^\n]+)", left_txt)
            wt = re.search(r"重量\s*([^\n]+)", left_txt)

            # 类型信息：剔除 FP/重量，保留“短剑 / 斩击/突刺 / 碎步（战技名）”
            block = left_txt
            if fp: block = block.replace(fp.group(0), "").strip()
            if wt: block = block.replace(wt.group(0), "").strip()
            lines = [ln for ln in block.splitlines() if ln.strip()][:4]
            data["type_info"] = {
                "lines": lines,
                "fp": fp.group(1).strip() if fp else "",
                "weight": wt.group(1).strip() if wt else ""
            }

        # 攻击力 / 防御时减伤率 —— 下一行是数值
        if "攻击力" in row_txt and "减伤率" in row_txt:
            t2 = row_tds(i + 1)
            if len(t2) == 2:
                data["attack"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["guard"]  = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        # 能力加成 / 必需能力值 —— 下一行是值
        if "能力加成" in row_txt and "必需能力值" in row_txt:
            t2 = row_tds(i + 1)
            if len(t2) == 2:
                data["scaling"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["requirements"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        # 附加效果
        if row_txt.strip() == "附加效果":
            t2 = row_tds(i + 1)
            if t2: data["extra_effect"] = text_with_newlines(t2[0])
            i += 1

        # 简介
        if row_txt.strip() == "简介":
            t2 = row_tds(i + 1)
            if t2: data["intro"] = text_with_newlines(t2[0])
            i += 1

        # 获取地点
        if row_txt.strip() == "获取地点":
            t2 = row_tds(i + 1)
            if t2: data["location"] = text_with_newlines(t2[0])
            i += 1

        # 专属战技
        if row_txt.strip().startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", row_txt.strip())
            if m: data["ash_of_war"] = m.group(1).strip()
            t2 = row_tds(i + 1)
            if t2: data["ash_desc"] = text_with_newlines(t2[0])
            i += 1

        # 强化石
        if row_txt.strip() == "武器使用强化石类型":
            t2 = row_tds(i + 1)
            if t2: data["upgrade"] = text_with_newlines(t2[0])
            i += 1

        i += 1

    return ensure_keys(data)

def ensure_keys(d: dict) -> dict:
    defaults = {
        "quality":"", "image":"", "type_info":{"lines":[],"fp":"","weight":""},
        "attack":{}, "guard":{}, "scaling":{}, "requirements":{},
        "extra_effect":"", "intro":"", "location":"", "ash_of_war":"", "ash_desc":"", "upgrade":""
    }
    out = dict(defaults); out.update(d); out.setdefault("name",""); return out

# -------------------- Index picking (unique) --------------------
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
        if title in seen_titles: continue
        seen_titles.add(title)
        url = urljoin(base, href)
        name = a.get_text(strip=True) or title
        found.append((name, url, title))
        if len(found) >= n: break
    return found

# -------------------- Assets & writing --------------------
def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def upgrade_thumb(url: str, size: int = 256) -> str:
    return re.sub(r"/(\d+)px-", f"/{size}px-", url)

def download_image(url: str, out_dir: pathlib.Path) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    try_urls = []
    if url:
        try_urls.append(upgrade_thumb(url, 256))
        try_urls.append(url)
    for u in try_urls:
        uu = u if u.startswith("http") else ("https:" + u if u.startswith("//") else u)
        try:
            r = requests.get(uu, headers=HEADERS, timeout=25); r.raise_for_status()
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            p = out_dir / f"icon{ext}"
            with open(p, "wb") as f: f.write(r.content)
            return str(p)
        except Exception:
            continue
    return ""

def wipe_repo_except(keep: list):
    root = pathlib.Path(".")
    for p in root.iterdir():
        if p.name in keep or p.name == ".git": continue
        if p.is_dir():
            import shutil; shutil.rmtree(p, ignore_errors=True)
        else:
            try: p.unlink()
            except Exception: pass

def md_table_from_pairs(title: str, kv: dict) -> str:
    if not kv: return ""
    rows = [f"### {title}", "", "| 项目 | 数值 |", "|---|---|"]
    for k, v in kv.items():
        rows.append(f"| {k} | {v} |")
    rows.append("")  # end with blank line
    return "\n".join(rows)

def write_repo(items: list):
    # 清空（保留 .github 与 scripts）
    wipe_repo_except([".github", "scripts"])

    root = pathlib.Path(".")
    (root / "README.md").write_text(
        "# 艾尔登法环 · 物品手册（演示样例）\n\n- 目录： [武器（样例）](items/weapons/README.md)\n",
        encoding="utf-8"
    )

    md_root = root / "items" / "weapons"; md_root.mkdir(parents=True, exist_ok=True)
    lines = ["# 武器（样例，仅3件）", ""]
    for it in items:
        lines.append(f"- [{it['name']}](./{safe_filename(it['name'])}.md)")
    (md_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for it in items:
        slug = safe_filename(it["name"])
        assets_dir = root / "assets" / "weapons" / slug
        img_path = download_image(it.get("image",""), assets_dir)
        rel_img = os.path.relpath(img_path, md_root) if img_path else ""

        # 把“图1”需要的块全部写出来
        type_lines = it.get("type_info",{}).get("lines", [])
        type_block = " / ".join([ln for ln in type_lines if ln]) if type_lines else ""
        fp = it.get("type_info",{}).get("fp","")
        wt = it.get("type_info",{}).get("weight","")

        intro_block = ("> " + "\n> ".join(it["intro"].splitlines())) if it.get("intro") else ""

        body = []
        body.append(f"# {it['name']}")
        if rel_img: body.append(f"![icon]({rel_img})")
        body.append("")
        body.append(f"- **武器品质**：{it.get('quality','')}")
        body.append(f"- **类型**：{type_block}")
        body.append(f"- **FP**：{fp}    |    **重量**：{wt}")
        body.append("")
        body.append(md_table_from_pairs("攻击力", it.get("attack", {})))
        body.append(md_table_from_pairs("防御时减伤率", it.get("guard", {})))
        body.append(md_table_from_pairs("能力加成", it.get("scaling", {})))
        body.append(md_table_from_pairs("必需能力值", it.get("requirements", {})))
        if it.get("extra_effect"): body.append(f"**附加效果**：{it['extra_effect']}\n")
        if intro_block: body.append(intro_block + "\n")
        if it.get("location"): body.append(f"**获取地点**：{it['location']}\n")
        if it.get("ash_of_war"): body.append(f"**专属战技**：{it['ash_of_war']}  \n{it.get('ash_desc','')}\n")
        if it.get("upgrade"): body.append(f"**武器使用强化石类型**：{it['upgrade']}\n")

        (md_root / f"{slug}.md").write_text("\n".join([b for b in body if b is not None]), encoding="utf-8")

    # 集中署名（低调）
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
