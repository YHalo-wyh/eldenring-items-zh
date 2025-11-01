#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lib_cn.py
公共 HTTP/HTML 工具、通用写盘函数，以及“按分类”的解析与渲染器。
依赖: requests, beautifulsoup4
"""

import os
import re
import time
import pathlib
from urllib.parse import urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ER-Items-Fetch/2.0"}
BAD_PREFIXES = (
    "特殊:", "分类:", "Category:", "模板", "Template:", "文件:", "File:", "MediaWiki:",
    "帮助:", "Help:", "首页", "艾尔登法环WIKI_BWIKI_哔哩哔哩"
)


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
    """把 <br> 换成换行，避免挤成一行。"""
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
    return bool(title.strip())

def pair_by_sequence(strings):
    """把 ['物理','98','魔力','0',...] 变成 dict。"""
    out, it = {}, iter(strings)
    for k in it:
        v = next(it, "")
        k, v = str(k).strip(), str(v).strip()
        if k:
            out[k] = v
    return out


# -------------------- 抓目录链接 --------------------

def pick_first_n_items_unique(index_url: str, n: int = 3):
    """从某“*一览”页里，抓首 n 个不重复的条目链接。"""
    base = "{u.scheme}://{u.netloc}".format(u=urlparse(index_url))
    soup = soup_of(index_url)
    content = soup.select_one("#mw-content-text .mw-parser-output") or soup.select_one(".mw-parser-output") or soup
    found, seen_titles = [], set()
    for a in content.find_all("a", href=True):
        href = a["href"]
        if not is_item_link(href):
            continue
        title = parse_title_from_href(href).strip()
        # 过滤明显不是条目页的链接
        if title in seen_titles or any(key in title for key in ("一览", "列表", "编辑", "最近更改", "特殊:")):
            continue
        seen_titles.add(title)
        name = a.get_text(strip=True) or title
        url = urljoin(base, href)
        found.append((name, url, title))
        if len(found) >= n:
            break
    return found


# -------------------- 杂项 & 下载 --------------------

def ensure_dir(p: pathlib.Path):
    p.mkdir(parents=True, exist_ok=True)

def safe_filename(name: str) -> str:
    return re.sub(r"[\\/<>:\"|?*]+", "_", name).strip() or "unknown"

def try_download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

def download_icon_from_table(table, out_dir: pathlib.Path) -> str:
    """
    表格右侧的大图通常有 class=img-equip；若无，就取表格里第一张图。
    返回保存后的相对路径（posix）。
    """
    ensure_dir(out_dir)
    img = table.select_one("img.img-equip") or table.select_one("img")
    if not img or not img.get("src"):
        return ""
    src = img["src"]
    u = ("https:" + src) if src.startswith("//") else src
    # 尝试 120px/160px 回落
    cands = [u]
    m = re.search(r"/(\d+)px-", u)
    if m:
        size = m.group(1)
        for sz in ("160", "120", "80"):
            if sz != size:
                cands.append(u.replace(f"/{size}px-", f"/{sz}px-"))
    for uu in cands:
        content = try_download(uu)
        if content:
            ext = os.path.splitext(urlparse(uu).path)[1] or ".png"
            fpath = out_dir / f"icon{ext}"
            with open(fpath, "wb") as f:
                f.write(content)
            # 转 posix，避免 Windows 反斜杠弄坏 Markdown
            return pathlib.Path(os.path.relpath(fpath)).as_posix()
    return ""


# -------------------- 基础提取 & 共同字段 --------------------

def extract_block_after_title(rows, i, title_text) -> tuple[str, int]:
    """找标题行 == title_text 的下一行单元格文本；返回(文本,新的i游标偏移)。"""
    txt = ""
    if i < len(rows):
        rtxt = text_with_newlines(rows[i]).strip()
        if rtxt == title_text:
            tds = rows[i+1].find_all("td") if i + 1 < len(rows) else []
            if tds:
                txt = text_with_newlines(tds[0])
            return txt, i + 1
    return txt, i

def extract_fp_weight_lines(table) -> tuple[str, str, list[str]]:
    """
    从“信息卡”左单元格抽“类型行/战技名”等，并解析 FP、重量：
    - 先复制左 td，把 style 里 float:right 的数字块删掉，避免把“3（-/-）/3.5”误当一行；
    - 再从整表文本用正则兜底解析 FP/重量。
    """
    fp = wt = ""
    type_lines = []

    # 找到“左半块”td
    left_td = None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2 and len(list(tds[0].stripped_strings)) >= 1:
            left_td = tds[0]
            break
    if left_td:
        left_clone = BeautifulSoup(str(left_td), "html.parser")
        for n in left_clone.select('[style*="float:right"]'):
            n.decompose()
        raw = [ln.strip() for ln in text_with_newlines(left_clone).splitlines() if ln.strip()]
        for ln in raw:
            if ln.startswith("消耗专注值") or ln.startswith("重量"):
                continue
            type_lines.append(ln)

    full = text_with_newlines(table)
    m = re.search(r"(?:消耗专注值|FP)\s*([^\n\r]+)", full)
    if m:
        fp = m.group(1).strip()
    m = re.search(r"重量\s*([0-9.]+(?:[^\n\r]*)?)", full)
    if m:
        wt = m.group(1).strip()

    # 把“孤儿数值行”剔除（如果它被误混进 type_lines）
    def norm(s): return re.sub(r"\s+", "", s or "")
    type_lines = [ln for ln in type_lines if norm(ln) not in {norm(fp), norm(wt)}]
    return fp, wt, type_lines


# -------------------- 各分类解析器 --------------------

def parse_weapon(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "weapons"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data

    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/weapons") / safe_filename(data["name"]))
    fp, wt, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]  # 可能含“战技名”
    data["fp"] = fp
    data["weight"] = wt

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])
        if "攻击力" in rtxt and "减伤率" in rtxt:
            tds = rows[i+1].find_all("td") if i + 1 < len(rows) else []
            if len(tds) == 2:
                data["attack"] = pair_by_sequence(list(tds[0].stripped_strings))
                data["guard"]  = pair_by_sequence(list(tds[1].stripped_strings))
            i += 1
        if "能力加成" in rtxt and "必需能力值" in rtxt:
            tds = rows[i+1].find_all("td") if i + 1 < len(rows) else []
            if len(tds) == 2:
                data["scaling"] = pair_by_sequence(list(tds[0].stripped_strings))
                data["reqs"]    = pair_by_sequence(list(tds[1].stripped_strings))
            i += 1

        txt, i = extract_block_after_title(rows, i, "附加效果")
        if txt: data["extra"] = txt
        txt, i = extract_block_after_title(rows, i, "简介")
        if txt: data["intro"] = txt
        txt, i = extract_block_after_title(rows, i, "获取地点")
        if txt: data["location"] = txt

        # “专属战技-xxx”
        t = rtxt.strip()
        if t.startswith("专属战技"):
            m = re.match(r"专属战技[-：:]\s*(.+)", t)
            if m:
                data["ash_name"] = m.group(1).strip()
            desc, i2 = extract_block_after_title(rows, i, t)
            if desc:
                data["ash_desc"] = desc
                i = i2
        if t == "武器使用强化石类型":
            txt, i = extract_block_after_title(rows, i, t)
            if txt: data["upgrade"] = txt

        i += 1
    return data


def parse_armor(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "armors"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data

    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/armors") / safe_filename(data["name"]))
    # 盔甲没有 FP，保留重量 + 左列几行作为“类型信息”
    fp, wt, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]      # 如：头盔/轻/中/重 等文本行（有则保留）
    data["weight"]     = wt

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])

        if "减伤率" in rtxt and "抵抗力" in rtxt:
            tds = rows[i+1].find_all("td") if i + 1 < len(rows) else []
            if len(tds) == 2:
                data["defence"] = pair_by_sequence(list(tds[0].stripped_strings))
                data["resist"]  = pair_by_sequence(list(tds[1].stripped_strings))
            i += 1

        txt, i = extract_block_after_title(rows, i, "获取途径")
        if txt: data["location"] = txt
        txt, i = extract_block_after_title(rows, i, "简介")
        if txt: data["intro"] = txt

        i += 1
    return data


def parse_talisman(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "talismans"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/talismans") / safe_filename(data["name"]))
    _, wt, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]
    data["weight"] = wt

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])

        txt, i = extract_block_after_title(rows, i, "效果")
        if txt: data["effect"] = txt
        txt, i = extract_block_after_title(rows, i, "负面效果")
        if txt: data["side_effect"] = txt
        txt, i = extract_block_after_title(rows, i, "获取地点")
        if txt: data["location"] = txt
        txt, i = extract_block_after_title(rows, i, "简介")
        if txt: data["intro"] = txt

        i += 1
    return data


def parse_item(html: str) -> dict:
    """普通消耗/素材等道具。"""
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "items"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/items") / safe_filename(data["name"]))
    _, wt, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]
    data["weight"] = wt

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])
        for key in ("效果", "用途", "使用效果", "道具效用"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["effect"] = txt
        for key in ("获取地点", "获取途径", "获得方法"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["location"] = txt
        txt, i = extract_block_after_title(rows, i, "简介")
        if txt: data["intro"] = txt

        i += 1
    return data


def parse_spell(html: str) -> dict:
    """法术（魔法/祷告）"""
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "spells"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/spells") / safe_filename(data["name"]))
    fp, _, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]
    data["fp"] = fp

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])
        # 记忆空格 / 必需能力值 常见
        m = re.search(r"记忆空格[:：]?\s*([^\n]+)", rtxt)
        if m: data["slots"] = m.group(1).strip()

        if "必需能力值" in rtxt:
            tds = rows[i+1].find_all("td") if i + 1 < len(rows) else []
            if tds:
                data["reqs"] = pair_by_sequence(list(tds[0].stripped_strings))
            i += 1

        for key in ("效果", "说明", "简介"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["intro"] = txt

        for key in ("获取地点", "获取途径", "获得方法"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["location"] = txt

        i += 1
    return data


def parse_ash(html: str) -> dict:
    """战灰"""
    soup = BeautifulSoup(html, "html.parser")
    data = {"category": "ashes"}
    data["name"] = (soup.select_one("h1.firstHeading") or soup.find("h1")).get_text(strip=True)

    table = soup.select_one(".mw-parser-output table.wikitable")
    if not table:
        return data
    data["icon_rel"] = download_icon_from_table(table, pathlib.Path("assets/ashes") / safe_filename(data["name"]))
    fp, _, lines = extract_fp_weight_lines(table)
    data["type_lines"] = [ln for ln in lines]
    data["fp"] = fp

    rows = table.find_all("tr")
    i = 0
    while i < len(rows):
        rtxt = text_with_newlines(rows[i])

        for key in ("道具效用", "效果", "说明"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["effect"] = txt

        for key in ("可注入武器", "可附着武器", "派生"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["inject"] = txt

        for key in ("获取地点", "获取途径", "获得方法"):
            txt, i = extract_block_after_title(rows, i, key)
            if txt:
                data["location"] = txt

        i += 1
    return data


# -------------------- 渲染：逐行 + 表格 --------------------

def hardbreak(lines: list[str]) -> str:
    lines = [ln for ln in lines if ln]
    return "  \n".join(lines)

def tbl(title: str, kv: dict | None) -> str:
    if not kv:
        return ""
    rows = [f"### {title}", "", "| 项目 | 数值 |", "|:---|:---|"]
    for k, v in kv.items():
        rows.append(f"| {k} | {v} |")
    rows.append("")
    return "\n".join(rows)

def md_footer(source_url: str) -> str:
    return (
        "\n> 来源：该条目整理自公开百科页面（保留署名以符合 CC BY-NC-SA 4.0）。\n"
        f"> {source_url}\n"
    )

def write_md_by_data(data: dict, source_url: str):
    """
    根据解析结果写出单页 MD（按分类落目录）。
    """
    cat = data.get("category", "misc")
    name = data.get("name", "unknown")
    slug = safe_filename(name)
    root_md = pathlib.Path("items") / cat
    ensure_dir(root_md)

    # 决定“逐行”顶部块
    top_lines = []
    if cat == "weapons":
        top_lines = [f"武器品质: {data.get('quality','')}".strip()] if data.get("quality") else []
        top_lines += data.get("type_lines", [])
        if data.get("ash_name"):
            # 战技名单独占一块
            pass
    elif cat == "armors":
        top_lines = data.get("type_lines", [])
        if data.get("weight"):
            top_lines.append(f"重量 {data['weight']}")
    elif cat == "talismans":
        top_lines = data.get("type_lines", [])
        if data.get("weight"):
            top_lines.append(f"重量 {data['weight']}")
    elif cat == "items":
        top_lines = data.get("type_lines", [])
        if data.get("weight"):
            top_lines.append(f"重量 {data['weight']}")
    elif cat == "spells":
        top_lines = data.get("type_lines", [])
        if data.get("fp"):
            top_lines.append(f"消耗专注值 {data['fp']}")
        if data.get("slots"):
            top_lines.append(f"记忆空格 {data['slots']}")
    elif cat == "ashes":
        top_lines = data.get("type_lines", [])
        if data.get("fp"):
            top_lines.append(f"消耗专注值 {data['fp']}")

    body = [f"# {name}"]
    if data.get("icon_rel"):
        # 用 posix 路径，不要在 f-string 里 replace 反斜杠（避免你之前遇到的 SyntaxError）
        icon_rel = pathlib.Path(data["icon_rel"]).as_posix()
        body.append(f"![icon]({icon_rel})")
    body.append("")  # 空行

    if top_lines:
        body.append(hardbreak(top_lines))
        body.append("")

    # 武器的“战技名”单独块
    if cat == "weapons" and data.get("ash_name"):
        body.append(hardbreak([data["ash_name"]]))
        body.append("")

    # 公共：表格
    if cat == "weapons":
        body.append(tbl("攻击力", data.get("attack")))
        body.append(tbl("防御时减伤率", data.get("guard")))
        body.append(tbl("能力加成", data.get("scaling")))
        body.append(tbl("必需能力值", data.get("reqs")))
    elif cat == "armors":
        body.append(tbl("减伤率", data.get("defence")))
        body.append(tbl("抵抗力", data.get("resist")))
    elif cat == "spells":
        body.append(tbl("必需能力值", data.get("reqs")))

    # 文本段
    if data.get("extra"):
        body.append(f"**附加效果**：{data['extra']}\n")
    if data.get("effect"):
        body.append(f"**效果**：{data['effect']}\n")
    if data.get("inject"):
        body.append(f"**可注入武器**：{data['inject']}\n")
    if data.get("intro"):
        body.append("> " + "\n> ".join(data["intro"].splitlines()) + "\n")
    if data.get("location"):
        body.append(f"**获取地点**：{data['location']}\n")
    if data.get("ash_desc"):
        body.append(f"**专属战技说明**：{data['ash_desc']}\n")
    if data.get("upgrade"):
        body.append(f"**武器使用强化石类型**：{data['upgrade']}\n")

    body.append(md_footer(source_url))

    (root_md / f"{slug}.md").write_text("\n".join(body), encoding="utf-8")

def append_index(cat: str, items: list[tuple[str, str]]):
    """更新分类 README 索引。"""
    root_md = pathlib.Path("items") / cat
    ensure_dir(root_md)
    lines = [f"# {display_name(cat)}（样例）", ""]
    for title, path_rel in items:
        posix = pathlib.Path(path_rel).as_posix()
        lines.append(f"- [{title}]({posix})")
    lines.append("")
    (root_md / "README.md").write_text("\n".join(lines), encoding="utf-8")

def display_name(cat: str) -> str:
    return {
        "weapons": "武器",
        "armors": "防具",
        "talismans": "护符",
        "items": "物品",
        "spells": "法术",
        "ashes": "战灰",
    }.get(cat, cat)

def wipe_repo_except(keep: list[str]):
    """清仓（保留 .github 与 scripts 可选）"""
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
