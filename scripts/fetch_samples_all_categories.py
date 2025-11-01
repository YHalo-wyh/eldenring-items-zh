# -*- coding: utf-8 -*-
"""
抓取 BWIKI（艾尔登法环）多品类各 3 个样例，生成：
- docs/<大类>/<物品名>.md  （逐行信息块 + 表格）
- images/<slug>.<ext>      （物品主图；若存在多尺寸优先保存 120px 小图）
- README.md                （总目录索引）

运行：
  python scripts/fetch_samples_all_categories.py --per-category 3 --delay 0.6

依赖：
  pip install requests beautifulsoup4 lxml unidecode

合规：
  BWIKI 页面内容通常遵循 CC BY-NC-SA 4.0，脚本会在 md 最下方自动附上来源和许可说明。
"""
import argparse
import os
import re
import time
import json
import random
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from unidecode import unidecode

BASE = "https://wiki.biligame.com/eldenring/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Referer": BASE,
    "Accept-Language": "zh-CN,zh;q=0.9"
}

# 目标抓取的大类索引页
CATEGORY_INDEX = {
    "武器": "武器一览",
    "防具": "防具一览",
    "护符": "护符一览",
    "法术": "法术一览",
    "战灰": "战灰一览",
    "物品": "物品一览",
}

OUT_ROOT = Path(".")
DOCS_DIR = OUT_ROOT / "docs"
IMAGES_DIR = OUT_ROOT / "images"


def slugify(text: str) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"[^\w\-\u4e00-\u9fa5]", "-", text)
    ascii_text = unidecode(text)
    ascii_text = re.sub(r"[^A-Za-z0-9\-\_]+", "-", ascii_text)
    ascii_text = re.sub(r"-{2,}", "-", ascii_text).strip("-")
    return ascii_text or "item"


def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def text_norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace(" ：", "：").replace(" ,", ",").strip()


def pick_icon(img_tag) -> str | None:
    """优先选 120px/160px 小图；没有则回退 src；返回绝对地址。"""
    if not img_tag:
        return None
    # srcset 优先
    srcset = img_tag.get("srcset", "")
    cand = None
    if srcset:
        pairs = [p.strip() for p in srcset.split(",")]
        # 更偏好 120px > 160px > 80px
        pref = ["120px", "160px", "80px"]
        scored = []
        for p in pairs:
            url_part = p.split(" ")[0]
            score = 100
            for i, key in enumerate(pref):
                if key in url_part:
                    score = i
                    break
            scored.append((score, url_part))
        if scored:
            scored.sort(key=lambda x: x[0])
            cand = scored[0][1]
    if not cand:
        cand = img_tag.get("src")
    if not cand:
        return None
    if cand.startswith("//"):
        cand = "https:" + cand
    elif cand.startswith("/"):
        cand = urljoin(BASE, cand.lstrip("/"))
    return cand


def download_image(url: str, name_hint: str) -> str | None:
    if not url:
        return None
    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        fname = f"{slugify(name_hint)}{ext}"
        fpath = IMAGES_DIR / fname
        if not fpath.exists():
            with requests.get(url, headers=HEADERS, timeout=30, stream=True) as r:
                r.raise_for_status()
                with open(fpath, "wb") as out:
                    shutil.copyfileobj(r.raw, out)
        return str(fpath.as_posix())
    except Exception:
        return None


def collect_detail_links(index_url: str, max_items: int = 3) -> list[tuple[str, str]]:
    """从索引页收集若干个条目链接 (title, url)"""
    soup = get_soup(index_url)
    links = []
    # 常见：内容区大量 a 标签
    for a in soup.select(".mw-parser-output a[href]"):
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or len(title) > 40:
            continue
        # 过滤锚点/编辑等特殊链接
        if href.startswith("#") or "action=edit" in href:
            continue
        if not href.startswith("http"):
            href = urljoin(BASE, href.lstrip("/"))
        # 只要落在 /eldenring/
        if "/eldenring/" not in href:
            continue
        # 忽略目录/分类页
        if any(key in href for key in ["一览", "目录", "分类", "特殊", "模板"]):
            continue
        links.append((title, href))

    # 去重（按 URL）
    seen = set()
    uniq = []
    for t, u in links:
        if u not in seen:
            uniq.append((t, u))
            seen.add(u)
    return uniq[:max_items]


def parse_weapon(soup: BeautifulSoup) -> dict:
    name = soup.select_one("#firstHeading").get_text(strip=True)
    quality = ""
    type_lines = []
    skill = ""
    fp = ""
    weight = ""
    intro = ""
    icon = ""

    # 主表（图片 + 列表）：
    icon_tag = soup.select_one(".mw-parser-output table.wikitable img")
    if icon_tag:
        icon = pick_icon(icon_tag)

    # 头部信息块（“武器品质 / 分类 / 战技 / FP / 重量”常在前几格）
    rows = soup.select(".mw-parser-output table.wikitable tr")
    # 粗暴但实用：遍历前 4~6 行的 td / br
    head_texts = []
    for tr in rows[:6]:
        tds = tr.find_all("td")
        for td in tds:
            parts = [p.get_text(" ", strip=True) for p in td.contents if hasattr(p, "get_text")]
            txt = td.get_text("\n", strip=True)
            head_texts.extend([p.strip() for p in txt.split("\n") if p.strip()])

    # 解析出“武器品质/分类/战技/消耗专注值/重量”
    # 典型顺序类似：
    #  武器品质:白
    #  短剑
    #  斩击/突刺
    #  碎步
    #  消耗专注值 3（-/7）
    #  重量 3.5
    for line in head_texts[:12]:
        if "品质" in line and not quality:
            quality = line.split("：", 1)[-1].replace("武器品质", "").replace("品质", "").strip(" ：")
        elif "消耗专注值" in line and not fp:
            fp = line.replace("消耗专注值", "").strip()
        elif line.startswith("重量") and not weight:
            weight = line.replace("重量", "").strip()
        elif any(k in line for k in ["战技", "刀刃", "剑技"]) and not skill:
            skill = line.replace("专属战技-", "").replace("战技", "").replace("刀刃", "").strip(" ：")
        else:
            # 把类似 “短剑”、“斩击/突刺” 这样的类型行收集起来
            if 1 <= len(line) <= 12 and not any(key in line for key in ["攻击力", "减伤率", "能力", "必需", "附加效果", "简介", "获取", "专属", "重量", "消耗专注值", "品质", "防御", "武器使用"]):
                type_lines.append(line)

    # 简介 / 获取地点 / 战技说明等文本块（按“简介 / 获取 / 专属战技”标题行抓）
    blocks = soup.select(".mw-parser-output table.wikitable tr td[colspan]")
    def find_block(title: str) -> str:
        for i, tr in enumerate(rows):
            th = tr.find("td")
            if th and title in th.get_text(strip=True):
                # 下一行（或同格）里的正文
                if i + 1 < len(rows):
                    td = rows[i + 1].find("td")
                    if td:
                        return td.get_text("\n", strip=True)
        return ""

    intro = find_block("简介")
    get_loc = find_block("获取")
    skill_desc = ""
    if "战技" in (skill or ""):
        skill_desc = find_block("战技")
    if not skill_desc:
        skill_desc = find_block("专属战技")
    extra_effect = find_block("附加效果")

    # 攻击力 / 防御时减伤率 两个小表
    def parse_table(left: bool = True) -> dict:
        mapping = {}
        start = None
        for i, tr in enumerate(rows):
            tx = tr.get_text(" ", strip=True)
            if (left and "攻击力" in tx) or ((not left) and "防御时减伤率" in tx):
                start = i + 1
                break
        if start is None:
            return mapping
        for tr in rows[start: start + 12]:
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) >= 1:
                key = cols[0].replace("：", "").strip()
                val = cols[1].strip() if len(cols) >= 2 else ""
                if key:
                    mapping[key] = val
        return mapping

    attack = parse_table(left=True)
    guard = parse_table(left=False)

    data = {
        "name": name,
        "quality": quality,
        "type_info": {
            "lines": type_lines[:3],  # 显示前三行
            "skill": skill,
            "fp": fp,
            "weight": weight
        },
        "attack": attack,
        "guard": guard,
        "extra_effect": extra_effect,
        "intro": intro,
        "get": get_loc,
        "skill_desc": skill_desc,
        "image": icon
    }
    return data


def parse_armor(soup: BeautifulSoup) -> dict:
    name = soup.select_one("#firstHeading").get_text(strip=True)
    # 取主图（右侧大图或 80/120 小图）
    icon_tag = soup.select_one(".mw-parser-output table.wikitable img") or soup.select_one(".mw-parser-output img")
    icon = pick_icon(icon_tag)

    # 重量
    weight = ""
    for t in soup.select(".mw-parser-output table.wikitable tr td"):
        txt = t.get_text(" ", strip=True)
        if txt.startswith("重量"):
            weight = txt.replace("重量", "").strip()
            break

    # 左：减伤率；右：抗抗性（免疫力/健壮度/理智度/抗死度/强韧度）
    rows = soup.select(".mw-parser-output table.wikitable tr")
    def parse_block(start_key: str, keys_limit=10):
        m = {}
        start = None
        for i, tr in enumerate(rows):
            if start_key in tr.get_text(" ", strip=True):
                start = i + 1
                break
        if start is None:
            return m
        for tr in rows[start: start + keys_limit]:
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) >= 2:
                k, v = cols[0], cols[1]
                if k and v:
                    m[k] = v
        return m

    reduce_rates = parse_block("减伤率")
    resistances = parse_block("抵抗力") or parse_block("抗性")

    # 获取途径 & 描述
    intro = ""
    get_loc = ""
    for i, tr in enumerate(rows):
        tx = tr.get_text(" ", strip=True)
        if "获取途径" in tx and i + 1 < len(rows):
            td = rows[i + 1].find("td")
            get_loc = td.get_text("\n", strip=True) if td else ""
        if ("简介" in tx or "说明" in tx) and i + 1 < len(rows):
            td = rows[i + 1].find("td")
            intro = td.get_text("\n", strip=True) if td else ""

    return {
        "name": name,
        "image": icon,
        "type_info": {
            "lines": ["防具"],
            "fp": "",
            "weight": weight
        },
        "reduce": reduce_rates,
        "resist": resistances,
        "intro": intro,
        "get": get_loc
    }


def parse_simple_item(soup: BeautifulSoup, type_tag: str) -> dict:
    """护符/法术/战灰/物品：字段差异很大，统一基础抽取。"""
    name = soup.select_one("#firstHeading").get_text(strip=True)
    icon_tag = soup.select_one(".mw-parser-output table.wikitable img") or soup.select_one(".mw-parser-output img")
    icon = pick_icon(icon_tag)

    weight = ""
    fp = ""
    effect = ""
    intro = ""
    get_loc = ""
    rows = soup.select(".mw-parser-output table.wikitable tr")
    cells_text = [td.get_text("\n", strip=True) for td in soup.select(".mw-parser-output table.wikitable td")]

    # 粗粒度抽字段
    for tx in cells_text:
        if (("重量" in tx) and not weight):
            weight = tx.replace("重量", "").replace("：", "").strip()
        if (("消耗专注值" in tx or tx.startswith("FP")) and not fp):
            fp = tx.replace("消耗专注值", "").replace("FP", "").replace("：", "").strip()
        if ("道具效用" in tx or "效果" in tx) and not effect:
            effect = tx.replace("道具效用", "").replace("效果", "").replace("：", "").strip()

    # 简介 / 获取
    def find_block(title: str) -> str:
        for i, tr in enumerate(rows):
            th = tr.find("td")
            if th and title in th.get_text(strip=True):
                if i + 1 < len(rows):
                    td = rows[i + 1].find("td")
                    if td:
                        return td.get_text("\n", strip=True)
        return ""

    intro = find_block("简介") or find_block("说明")
    get_loc = find_block("获取") or find_block("获取地点") or find_block("获取途径")

    return {
        "name": name,
        "image": icon,
        "type_info": {
            "lines": [type_tag],
            "fp": fp,
            "weight": weight
        },
        "effect": effect,
        "intro": intro,
        "get": get_loc
    }


def detect_kind(url: str) -> str:
    # 尝试根据 url 或页面分类词判断
    if "Weapon" in url or "武器" in url:
        return "武器"
    if "防具" in url or any(k in url for k in ["头", "臂", "腿", "胴"]):
        return "防具"
    if "护符" in url:
        return "护符"
    if "战灰" in url or "战技" in url:
        return "战灰"
    if "法术" in url or "魔法" in url or "祷告" in url:
        return "法术"
    return "物品"


def render_weapon_md(d: dict, source_url: str) -> str:
    lines = []
    lines.append(f"# {d['name']}\n")
    if d.get("image"):
        rel = os.path.relpath(d["image"], OUT_ROOT.as_posix()).replace("\\", "/")
        lines.append(f"![icon]({rel})\n")
    # 逐行信息块
    ti = d["type_info"]
    q = d.get("quality", "")
    if q:
        lines.append(f"**武器品质：** {q}")
    for x in ti.get("lines", []):
        lines.append(x)
    if ti.get("skill"):
        lines.append(ti["skill"])
    if ti.get("fp"):
        lines.append(f"消耗专注值 {ti['fp']}")
    if ti.get("weight"):
        lines.append(f"重量 {ti['weight']}")
    lines.append("")  # 空行

    # 攻击/防御表
    def table(title: str, mp: dict):
        if not mp:
            return ""
        t = [f"### {title}", "", "| 项目 | 数值 |", "|---|---|"]
        for k, v in mp.items():
            t.append(f"| {k} | {v} |")
        t.append("")
        return "\n".join(t)
    lines.append(table("攻击力", d.get("attack", {})))
    lines.append(table("防御时减伤率", d.get("guard", {})))

    if d.get("extra_effect"):
        lines.append("### 附加效果\n")
        lines.append(d["extra_effect"])
        lines.append("")

    if d.get("intro"):
        lines.append("### 简介\n")
        lines.append(d["intro"])
        lines.append("")
    if d.get("get"):
        lines.append("### 获取地点\n")
        lines.append(d["get"])
        lines.append("")
    if d.get("skill_desc"):
        lines.append("### 战技\n")
        lines.append(d["skill_desc"])
        lines.append("")

    # 许可与来源
    lines.append("---")
    lines.append(f"来源：该条目的公开百科页面。原始页面链接：{source_url}")
    lines.append("根据 CC BY-NC-SA 4.0 使用，保留来源与相同方式共享。")
    return "\n".join([l for l in lines if l is not None])


def render_simple_md(d: dict, source_url: str, kind: str) -> str:
    lines = []
    lines.append(f"# {d['name']}\n")
    if d.get("image"):
        rel = os.path.relpath(d["image"], OUT_ROOT.as_posix()).replace("\\", "/")
        lines.append(f"![icon]({rel})\n")
    # 逐行信息块
    ti = d.get("type_info", {})
    if kind == "防具":
        lines.append("防具")
    else:
        lines.append(kind)
    if ti.get("fp"):
        lines.append(f"消耗专注值 {ti['fp']}")
    if ti.get("weight"):
        lines.append(f"重量 {ti['weight']}")
    lines.append("")

    # 根据种类输出结构化块
    if kind == "防具":
        if d.get("reduce"):
            lines.append("### 减伤率\n")
            lines.append("| 项目 | 数值 |")
            lines.append("|---|---|")
            for k, v in d["reduce"].items():
                lines.append(f"| {k} | {v} |")
            lines.append("")
        if d.get("resist"):
            lines.append("### 抵抗力\n")
            lines.append("| 项目 | 数值 |")
            lines.append("|---|---|")
            for k, v in d["resist"].items():
                lines.append(f"| {k} | {v} |")
            lines.append("")
    else:
        if d.get("effect"):
            lines.append("### 效果\n")
            lines.append(d["effect"])
            lines.append("")

    if d.get("intro"):
        lines.append("### 简介\n")
        lines.append(d["intro"])
        lines.append("")
    if d.get("get"):
        lines.append("### 获取\n")
        lines.append(d["get"])
        lines.append("")

    lines.append("---")
    lines.append(f"来源：该条目的公开百科页面。原始页面链接：{source_url}")
    lines.append("根据 CC BY-NC-SA 4.0 使用，保留来源与相同方式共享。")
    return "\n".join(lines)


def write_readme(collected: dict):
    """生成总首页 README"""
    md = ["# 艾尔登法环 · 物品样例（各大类 3 条）", ""]
    for cate, items in collected.items():
        md.append(f"## {cate}（示例）")
        md.append("")
        for title, path in items:
            rel = os.path.relpath(path, OUT_ROOT.as_posix()).replace("\\", "/")
            md.append(f"- [{title}]({rel})")
        md.append("")
    (OUT_ROOT / "README.md").write_text("\n".join(md), "utf-8")


def ensure_dirs():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    # 清空 docs 下旧内容（只保留 .git 等）
    for p in DOCS_DIR.glob("*"):
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)


def main(per_category: int, delay: float):
    ensure_dirs()
    collected_paths = {}

    for cate, idx_name in CATEGORY_INDEX.items():
        idx_url = urljoin(BASE, idx_name)
        try:
            links = collect_detail_links(idx_url, max_items=per_category)
        except Exception as e:
            print("索引抓取失败：", cate, idx_url, e)
            links = []
        saved = []
        cat_dir = DOCS_DIR / cate
        cat_dir.mkdir(parents=True, exist_ok=True)

        for title, url in links:
            try:
                soup = get_soup(url)
                kind = cate  # 以大类为准
                if cate == "武器":
                    data = parse_weapon(soup)
                    img_path = download_image(data.get("image"), data["name"])
                    if img_path:
                        data["image"] = img_path
                    md = render_weapon_md(data, source_url=url)
                elif cate == "防具":
                    data = parse_armor(soup)
                    img_path = download_image(data.get("image"), data["name"])
                    if img_path:
                        data["image"] = img_path
                    md = render_simple_md(data, source_url=url, kind="防具")
                else:
                    # 护符/法术/战灰/物品
                    data = parse_simple_item(soup, type_tag=cate)
                    img_path = download_image(data.get("image"), data["name"])
                    if img_path:
                        data["image"] = img_path
                    md = render_simple_md(data, source_url=url, kind=cate)

                fname = f"{slugify(data['name'])}.md"
                fpath = cat_dir / fname
                fpath.write_text(md, "utf-8")
                saved.append((data["name"], fpath))
                print("OK:", cate, data["name"])
            except Exception as e:
                print("ERR:", cate, title, url, "=>", e)
            time.sleep(delay + random.random() * delay)

        collected_paths[cate] = saved

    write_readme(collected_paths)
    print("完成 ✅")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=3, help="每个大类抓取条数")
    ap.add_argument("--delay", type=float, default=0.6, help="请求间隔（秒，基础 + 随机）")
    args = ap.parse_args()
    main(args.per_category, args.delay)
