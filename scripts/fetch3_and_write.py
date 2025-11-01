#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取“武器一览”里标题不重复的前 3 件武器 → 解析整块信息 → 下载首图到本地 →
生成仅 3 条样例的目录与 Markdown。（适用于 GitHub Actions 零参数运行）

依赖：
    pip install requests beautifulsoup4
"""

import os
import re
import sys
import time
import json
import pathlib
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

# -------------------- 配置 --------------------
INDEX_URL = "https://wiki.biligame.com/eldenring/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88"
LIMIT = 3
DELAY = 0.8
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/1.3"}
BAD_PREFIXES = ("特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:", "帮助:", "Help:")

# -------------------- HTTP & HTML --------------------
def get_html(url: str, timeout: float = 25.0) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
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

def pair_by_sequence(strings):
    out, it = {}, iter(strings)
    for k in it:
        v = next(it, "")
        k, v = str(k).strip(), str(v).strip()
        if k:
            out[k] = v
    return out

# -------------------- 工具 --------------------
def ensure_keys(d: dict) -> dict:
    defaults = {
        "name": "",
        "quality": "",
        "image": "",
        "type_info": {"lines": [], "fp": "", "weight": ""},
        "attack": {},
        "guard": {},
        "scaling": {},
        "requirements": {},
        "extra_effect": "",
        "intro": "",
        "location": "",
        "ash_of_war": "",
        "ash_desc": "",
        "upgrade": "",
    }
    out = dict(defaults)
    out.update(d or {})
    return out

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
    if title in ("首页", "武器一览", "防具一览", "护符一览", "物品一览", "法术一览"):
        return False
    return bool(title.strip())

def pick_first_n_items_unique(index_url: str, n: int = 3):
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    found, seen_titles = [], set()
    for a in content.find_all("a", href=True):
        href = a["href"]
        if not is_item_link(href):
            continue
        title = parse_title_from_href(href).strip()
        if title in seen_titles:
            continue
        seen_titles.add(title)
        url = urljoin(base, href)
        name = a.get_text(strip=True) or title
        found.append((name, url, title))
        if len(found) >= n:
            break
    return found

# -------------------- 解析物品页 --------------------
def extract_fp_weight_and_lines(table) -> tuple[str, str, list]:
    """从卡片左侧信息单元格解析 FP / 重量 / 类型行（更鲁棒，带兜底）"""
    fp = wt = ""
    lines = []

    # 先找“左大单元格”（含多行“短剑/流派/战技名”等）
    left_td = None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2 and len(list(tds[0].stripped_strings)) >= 3:
            left_td = tds[0]
            break

    if left_td:
        raw = [ln.strip() for ln in text_with_newlines(left_td).splitlines() if ln.strip()]
        for ln in raw:
            if ln.startswith("消耗专注值"):
                m = re.search(r"消耗专注值\s*([^\n\r]+)", ln)
                fp = (m.group(1).strip() if m else ln.replace("消耗专注值", "").strip())
            elif ln.startswith("重量"):
                m = re.search(r"重量\s*([0-9.]+(?:[^\n\r]*)?)", ln)
                wt = (m.group(1).strip() if m else ln.replace("重量", "").strip())
            else:
                lines.append(ln)

    # 兜底，全表再搜
    if not fp or not wt:
        full = text_with_newlines(table)
        if not fp:
            m = re.search(r"消耗专注值\s*([^\n\r]+)", full)
            if m:
                fp = m.group(1).strip()
        if not wt:
            m = re.search(r"重量\s*([0-9.]+(?:[^\n\r]*)?)", full)
            if m:
                wt = m.group(1).strip()

    return fp, wt, lines

def parse_item_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {}

    # 标题
    h1 = soup.select_one("h1.firstHeading")
    data["name"] = h1.get_text(strip=True) if h1 else ""

    # 信息卡表
    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return ensure_keys(data)

    rows = table.find_all("tr")
    row_tds = lambda i: rows[i].find_all("td") if 0 <= i < len(rows) else []

    # 品质（从整表文本找）
    whole = text_with_newlines(table)
    m_q = re.search(r"武器品质[:：]?\s*([^\s\n]+)", whole)
    if m_q:
        data["quality"] = m_q.group(1).strip()

    # 图片：只取 .img-equip（卡片右侧大图）
    img = table.select_one("img.img-equip")
    if img and img.get("src"):
        src = img["src"]
        data["image"] = ("https:" + src) if src.startswith("//") else src

    # 类型/战技 + FP + 重量
    fp, wt, typ_lines = extract_fp_weight_and_lines(table)
    data["type_info"] = {"lines": typ_lines, "fp": fp, "weight": wt}

    # 表格四块 + 其他块
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])

        # 攻击力 / 减伤率
        if "攻击力" in rtxt and "减伤率" in rtxt:
            t2 = row_tds(i + 1)
            if len(t2) == 2:
                data["attack"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["guard"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        # 能力加成 / 必需能力值
        if "能力加成" in rtxt and "必需能力值" in rtxt:
            t2 = row_tds(i + 1)
            if len(t2) == 2:
                data["scaling"] = pair_by_sequence(list(t2[0].stripped_strings))
                data["requirements"] = pair_by_sequence(list(t2[1].stripped_strings))
            i += 1

        # 附加效果
        if rtxt.strip() == "附加效果":
            t2 = row_tds(i + 1)
            if t2:
                data["extra_effect"] = text_with_newlines(t2[0])
            i += 1

        # 简介
        if rtxt.strip() == "简介":
            t2 = row_tds(i + 1)
            if t2:
                data["intro"] = text_with_newlines(t2[0])
            i += 1

        # 获取地点
        if rtxt.strip() == "获取地点":
            t2 = row_tds(i + 1)
            if t2:
                data["location"] = text_with_newlines(t2[0])
            i += 1

        # 专属战技
        if rtxt.strip().startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", rtxt.strip())
            if m:
                data["ash_of_war"] = m.group(1).strip()
            t2 = row_tds(i + 1)
            if t2:
                data["ash_desc"] = text_with_newlines(t2[0])
            i += 1

        # 强化石
        if rtxt.strip() == "武器使用强化石类型":
            t2 = row_tds(i + 1)
            if t2:
                data["upgrade"] = text_with_newlines(t2[0])
            i += 1

        i += 1

    return ensure_keys(data)

# -------------------- 写入仓库 --------------------
def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def try_download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

def download_image(url: str, out_dir: pathlib.Path) -> str:
    """优先原始 src；若带 /80px-/120px-/160px-，尝试 120/160/80 回退。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not url:
        return ""
    u = ("https:" + url) if url.startswith("//") else url
    candidates = [u]
    m = re.search(r"/(\d+)px-", u)
    if m:
        size_tag = m.group(1)
        for sz in ["120", "160", "80"]:
            if sz != size_tag:
                candidates.append(u.replace(f"/{size_tag}px-", f"/{sz}px-"))
    for uu in candidates:
        content = try_download(uu)
        if content:
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            p = out_dir / f"icon{ext}"
            with open(p, "wb") as f:
                f.write(content)
            return str(p)
    return ""

def wipe_repo_except(keep: list[str]):
    root = pathlib.Path(".")
    for p in root.iterdir():
        if p.name in keep or p.name == ".git":
            continue
        if p.is_dir():
            import shutil
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except Exception:
                pass

def md_table_from_pairs(title: str, kv: dict) -> str:
    """表格列左对齐：|:---|:---|"""
    if not kv:
        return ""
    rows = [f"### {title}", "", "| 项目 | 数值 |", "|:---|:---|"]
    for k, v in kv.items():
        rows.append(f"| {k} | {v} |")
    rows.append("")
    return "\n".join(rows)

def hardbreak_block(lines: list[str]) -> str:
    """两个空格 + 换行 → 强制逐行换行"""
    lines = [ln for ln in lines if ln is not None and ln != ""]
    return "  \n".join(lines)

def write_repo(items: list[dict]):
    # 清空（保留 .github 与 scripts）
    wipe_repo_except([".github", "scripts"])

    root = pathlib.Path(".")
    (root / "README.md").write_text(
        "# 艾尔登法环 · 物品手册（演示样例）\n\n- 目录： [武器（样例）](items/weapons/README.md)\n",
        encoding="utf-8",
    )

    md_root = root / "items" / "weapons"
    md_root.mkdir(parents=True, exist_ok=True)
    lines = ["# 武器（样例，仅3件）", ""]
    for it in items:
        lines.append(f"- [{it['name']}](./{safe_filename(it['name'])}.md)")
    (md_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 写每个物品 MD + 本地图片
    for it in items:
        slug = safe_filename(it["name"])
        assets_dir = root / "assets" / "weapons" / slug
        img_path = download_image(it.get("image", ""), assets_dir)
        rel_img = os.path.relpath(img_path, md_root) if img_path else ""

        quality = it.get("quality", "")
        type_lines = (it.get("type_info", {}).get("lines", []) or [])
        ash = it.get("ash_of_war", "")
        fp = it.get("type_info", {}).get("fp", "")
        wt = it.get("type_info", {}).get("weight", "")

        section1 = hardbreak_block([f"武器品质: {quality}", *type_lines])  # 逐行竖排
        section2 = hardbreak_block([ash]) if ash else ""
        section3 = hardbreak_block([f"消耗专注值 {fp}".strip(), f"重量 {wt}".strip()])

        body = []
        body.append(f"# {it['name']}")
        if rel_img:
            body.append(f"![icon]({rel_img})")
        body.append("")  # 空行
        body.append(section1)
        if section2:
            body.append("")
            body.append(section2)
        body.append("")
        body.append(section3)
        body.append("")

        # 四张表
        body.append(md_table_from_pairs("攻击力", it.get("attack", {})))
        body.append(md_table_from_pairs("防御时减伤率", it.get("guard", {})))
        body.append(md_table_from_pairs("能力加成", it.get("scaling", {})))
        body.append(md_table_from_pairs("必需能力值", it.get("requirements", {})))

        # 其余文本
        if it.get("extra_effect"):
            body.append(f"**附加效果**：{it['extra_effect']}\n")
        if it.get("intro"):
            intro_block = "> " + "\n> ".join(it["intro"].splitlines())
            body.append(intro_block + "\n")
        if it.get("location"):
            body.append(f"**获取地点**：{it['location']}\n")
        if it.get("ash_desc"):
            body.append(f"**专属战技说明**：{it['ash_desc']}\n")
        if it.get("upgrade"):
            body.append(f"**武器使用强化石类型**：{it['upgrade']}\n")

        (md_root / f"{slug}.md").write_text("\n".join(body), encoding="utf-8")

    # 集中署名（低调）
    (root / "ATTRIBUTION.md").write_text(
        "# Attribution\n\n本仓库演示样例的文本与术语整理自公开资料，仅供非商业研究与学习。\n",
        encoding="utf-8",
    )

# -------------------- 主流程 --------------------
def main():
    try:
        triples = pick_first_n_items_unique(INDEX_URL, LIMIT)
    except Exception as e:
        sys.stderr.write(f"[warn] 目录页抓取失败：{e}\n")
        base = "https://wiki.biligame.com/eldenring/"
        triples = [
            ("刺剑", base + "%E5%88%BA%E5%89%91", "刺剑"),
            ("权贵细身剑", base + "%E6%9D%83%E8%B4%B5%E7%BB%86%E8%BA%AB%E5%89%91", "权贵细身剑"),
            ("使命短刀", base + "%E4%BD%BF%E5%91%BD%E7%9F%AD%E5%88%80", "使命短刀"),
        ]
    results = []
    for name, url, _title in triples:
        try:
            data = parse_item_html(get_html(url))
            if not data.get("name"):
                data["name"] = name
            results.append(data)
        except Exception as e:
            sys.stderr.write(f"[warn] 解析失败：{name} -> {url} -> {e}\n")
        time.sleep(DELAY)

    write_repo(results)

if __name__ == "__main__":
    main()
