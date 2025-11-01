# -*- coding: utf-8 -*-
"""
fetch_samples_all_categories.py
抓取 BWIKI（艾尔登法环）各大类样例（各 3 个），生成逐行+表格风格 Markdown，并下载图片。

运行：
  pip install requests beautifulsoup4 lxml
  python scripts/fetch_samples_all_categories.py

注意：BWIKI 页面结构存在差异，脚本对“武器/护符/法术/战灰/物品”做了启发式解析；
     若遇到极少数特别页面，可能会跳过，继续抓取下一个。
"""
import os, re, time, json, html
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.biligame.com/eldenring"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ER-Sampler/1.0)"}

OUT_ROOT = Path("items")
IMG_ROOT = Path("assets/images")
OUT_ROOT.mkdir(parents=True, exist_ok=True)
IMG_ROOT.mkdir(parents=True, exist_ok=True)

# ========== 工具函数 ==========
def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "lxml")

def slugify(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|#\s]+", "_", name.strip())
    return s.strip("_")

def textnorm(s):
    return re.sub(r"\s+", " ", s).strip()

def pick_main_image(soup: BeautifulSoup) -> str:
    """
    物品页上方信息表右侧的 80/120/160px 缩略图，或正文第一张 PNG。
    """
    # 常见：img-equip / 80px-xxx.png
    img = soup.select_one('img.img-equip')
    if img and img.get("src"):
        return img["src"]

    # 战灰/法术等经常在正文最上有一个文件图片
    cand = soup.select_one('.mw-parser-output img')
    if cand and cand.get("src"):
        return cand["src"]

    # 兜底：页面任何 PNG
    cand2 = soup.find("img", src=re.compile(r"\.png"))
    if cand2 and cand2.get("src"):
        return cand2["src"]
    return ""

def absolutize_url(src: str) -> str:
    if not src:
        return ""
    if src.startswith("http"):
        return src
    # 处理 // 开头的
    if src.startswith("//"):
        return "https:" + src
    # 相对路径
    return "https://patchwiki.biligame.com" + src if src.startswith("/images/") else BASE + src

def save_image(img_url: str, save_dir: Path, name_slug: str) -> str:
    if not img_url:
        return ""
    url = absolutize_url(img_url)
    save_dir.mkdir(parents=True, exist_ok=True)
    ext = ".png"
    fn = save_dir / f"{name_slug}{ext}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        with open(fn, "wb") as f:
            f.write(resp.content)
        return str(fn).replace("\\", "/")
    except Exception:
        return ""

def md_h1(s):   return f"# {s}\n\n"
def md_h2(s):   return f"## {s}\n\n"
def md_quote(s): 
    s = s.strip()
    if not s: return ""
    return "\n".join(["> " + line for line in s.splitlines() if line.strip()]) + "\n\n"

def md_kv_lines(lines):
    """逐行显示（用 Markdown 的显式换行）"""
    lines = [l for l in lines if l]
    return "".join(l + "  \n" for l in lines) + "\n" if lines else ""

def md_table(title, kv_pairs):
    """
    kv_pairs: List[Tuple[key, value]]
    渲染为两列表格：项目 / 数值
    """
    if not kv_pairs:
        return ""
    s = f"### {title}\n\n| 项目 | 数值 |\n|---|---|\n"
    for k, v in kv_pairs:
        s += f"| {k} | {v} |\n"
    return s + "\n"

def clean_num(s):
    return textnorm(s).replace("：", ":").replace(" ", "")

# ========== 抽取块 ==========

def extract_cell_texts(table):
    """提取表格中所有单元格纯文本"""
    return [textnorm(td.get_text(" ", strip=True)) for td in table.select("td")]

def find_first_wikitable(soup):
    t = soup.select_one("table.wikitable")
    return t

def parse_generic_blocks(soup):
    """
    通用：抓“道具效用”“获取途径”“简介/说明”等。
    返回 dict：{effect, obtain, intro}
    """
    effect = ""
    obtain = ""
    intro  = ""

    # 表头常写在一行 <td>道具效用</td><td>xxxx</td>
    tbl = find_first_wikitable(soup)
    if tbl:
        tds = tbl.select("td")
        for i in range(0, len(tds)-1, 1):
            key = textnorm(tds[i].get_text(" ", strip=True))
            val = textnorm(tds[i+1].get_text("\n", strip=True))
            if not effect and ("道具效用" in key or "效果" == key):
                effect = val
            if not obtain and ("获取" in key or "入手" in key):
                obtain = val

    # 正文里“简介”“说明”
    body = soup.select_one(".mw-parser-output")
    if body:
        # 取第一个段落块作为简介（多数物品有）
        ps = [p for p in body.select("p") if textnorm(p.get_text())]
        if ps:
            intro = textnorm(ps[-1].get_text()) if not intro else intro

    return {"effect": effect, "obtain": obtain, "intro": intro}

# ========== 各大类解析 ==========
def parse_weapon(soup, name):
    """
    逐行：武器品质 / 类型行们 / 战技 / FP / 重量
    表格：攻击力、防御时减伤率、能力加成、必需能力值
    """
    lines = []
    kv_attack = []
    kv_guard  = []
    kv_scale  = []
    kv_req    = []

    tbl = find_first_wikitable(soup)
    if tbl:
        # 品质 / 类型 / 战技 / FP / 重量
        raw = tbl.get_text("\n", strip=True)
        m_quality = re.search(r"武器品质[:：]\s*([^\n]+)", raw)
        if m_quality: lines.append(f"武器品质：{textnorm(m_quality.group(1))}")

        # 左列类型块
        left_td = tbl.select_one("tr:nth-of-type(3) td:nth-of-type(1)")
        if left_td:
            # 逐行：短剑 / 斩击/突刺 / 战技名
            left_lines = [textnorm(x) for x in left_td.stripped_strings if textnorm(x)]
            # 去掉“消耗专注值/重量”的重复数值（它们会在右侧单元格再显示）
            left_lines = [ln for ln in left_lines if not re.fullmatch(r"\d+(\.\d+)?\s*[（(].*[)）]", ln)]
            lines.extend(left_lines)

        # FP/重量（在右侧）
        right_td = tbl.select_one("tr:nth-of-type(3) td:nth-of-type(2)")
        if right_td:
            rt = right_td.get_text("\n", strip=True)
            mfp = re.search(r"消耗专注值\s*([^\n]+)", rt)
            mw  = re.search(r"重量\s*([^\n]+)", rt)
            if mfp: lines.append(f"消耗专注值 {textnorm(mfp.group(1))}")
            if mw:  lines.append(f"重量 {textnorm(mw.group(1))}")

        # 攻击/防御
        rows = tbl.select("tr")
        def col_text(tr, idx):
            td = tr.select_one(f"td:nth-of-type({idx})")
            return textnorm(td.get_text(" ", strip=True)) if td else ""

        # 扫描“攻击力 / 防御时减伤率”
        for i, tr in enumerate(rows):
            t0 = col_text(tr, 1)
            t1 = col_text(tr, 2)
            if t0 == "攻击力":
                # 下一行开始若出现“物理/魔力/火/雷/圣/致命一击/魔法加成”
                for j in range(i+1, min(i+20, len(rows))):
                    L = col_text(rows[j], 1)
                    R = col_text(rows[j], 2)
                    if not L: break
                    kv_attack.append((L, R))
            if t1 == "防御时减伤率":
                # 下一行开始，右半列
                for j in range(i+1, min(i+20, len(rows))):
                    L = col_text(rows[j], 1)
                    R = col_text(rows[j], 2)
                    if not R: break
                    kv_guard.append((L, R))

            if t0 == "能力加成":
                # 下面两列映射 E/D/- 等
                row_next = rows[i+1] if i+1 < len(rows) else None
                if row_next:
                    # 收集若干行直到遇到空
                    for j in range(i+1, min(i+10, len(rows))):
                        r = rows[j]
                        c1 = col_text(r, 1)
                        c2 = col_text(r, 2)
                        if not c1 and not c2: break
                        if c1 and c2:
                            # 行内常含 “力气 D   灵巧 E” 这种两列结构
                            # 直接拆子列
                            segs = [textnorm(x) for x in r.stripped_strings if textnorm(x)]
                            # 形如：力气 D | 灵巧 E
                            for k in range(0, len(segs), 2):
                                if k+1 < len(segs):
                                    kv_scale.append((segs[k], segs[k+1]))
                continue

            if t1 == "必需能力值":
                # 同上
                for j in range(i+1, min(i+10, len(rows))):
                    r = rows[j]
                    segs = [textnorm(x) for x in r.stripped_strings if textnorm(x)]
                    if not segs: break
                    for k in range(0, len(segs), 2):
                        if k+1 < len(segs):
                            kv_req.append((segs[k], segs[k+1]))

    blocks = parse_generic_blocks(soup)
    return {
        "lines": lines,
        "tables": [
            ("攻击力", kv_attack),
            ("防御时减伤率", kv_guard),
            ("能力加成", kv_scale),
            ("必需能力值", kv_req),
        ],
        "effect": blocks["effect"],
        "obtain": blocks["obtain"],
        "intro": blocks["intro"],
    }

def parse_talisman(soup, name):
    lines = []
    kvs = []
    tbl = find_first_wikitable(soup)
    if tbl:
        raw = tbl.get_text("\n", strip=True)
        m_w = re.search(r"重量[:：]?\s*([0-9.]+)", raw)
        if m_w: lines.append(f"重量 {m_w.group(1)}")
    blocks = parse_generic_blocks(soup)
    return {
        "lines": lines,
        "tables": [("属性/数值", kvs)],  # 护符多数没有固定数值表，这里留空
        "effect": blocks["effect"],
        "obtain": blocks["obtain"],
        "intro": blocks["intro"],
    }

def parse_spell(soup, name):
    lines = []
    tbl = find_first_wikitable(soup)
    kv_req = []
    if tbl:
        raw = tbl.get_text("\n", strip=True)
        # 类型（魔法/祷告）不一定显式；若标题含“祷告/魔法”则补
        h1 = soup.select_one("#firstHeading")
        if h1 and ("祷告" in h1.get_text() or "魔法" in h1.get_text()):
            lines.append(f"类型：{ '祷告' if '祷告' in h1.get_text() else '魔法'}")
        # FP / 记忆空位
        m_fp = re.search(r"(消耗专注值|消耗FP)[:：]?\s*([0-9（/()\-－—]+)", raw)
        m_ms = re.search(r"(记忆空位|记忆空间)[:：]?\s*([0-9.]+)", raw)
        if m_fp: lines.append(f"消耗专注值 {textnorm(m_fp.group(2))}")
        if m_ms: lines.append(f"记忆空位 {textnorm(m_ms.group(2))}")

        # 需求：智力/信仰/感应
        for k in ["智力","信仰","感应"]:
            m = re.search(k + r"[:：]?\s*([0-9]+)", raw)
            if m: kv_req.append((k, m.group(1)))

    blocks = parse_generic_blocks(soup)
    return {
        "lines": lines,
        "tables": [("需求能力值", kv_req)],
        "effect": blocks["effect"],
        "obtain": blocks["obtain"],
        "intro": blocks["intro"],
    }

def parse_ash(soup, name):
    lines = []
    tbl = find_first_wikitable(soup)
    if tbl:
        raw = tbl.get_text("\n", strip=True)
        # 战技名一般在标题“战灰：xxx”，逐行里直接展示战技名
        h1 = soup.select_one("#firstHeading")
        if h1:
            nm = textnorm(h1.get_text()).replace("战灰：", "").strip()
            if nm:
                lines.append(f"战技：{nm}")
        m_fp = re.search(r"(消耗专注值|消耗FP)[:：]?\s*([0-9（/()\-－—]+)", raw)
        if m_fp:
            lines.append(f"消耗专注值 {textnorm(m_fp.group(2))}")
        # “能在…使用” 信息
        m_use = re.search(r"能在.+?使用", raw)
        if m_use:
            lines.append(m_use.group(0))

    blocks = parse_generic_blocks(soup)
    return {
        "lines": lines,
        "tables": [],
        "effect": blocks["effect"],
        "obtain": blocks["obtain"],
        "intro": blocks["intro"],
    }

def parse_item(soup, name):
    lines = []
    tbl = find_first_wikitable(soup)
    if tbl:
        raw = tbl.get_text("\n", strip=True)
        m_w = re.search(r"重量[:：]?\s*([0-9.]+)", raw)
        if m_w: lines.append(f"重量 {m_w.group(1)}")
    blocks = parse_generic_blocks(soup)
    return {
        "lines": lines,
        "tables": [],
        "effect": blocks["effect"],
        "obtain": blocks["obtain"],
        "intro": blocks["intro"],
    }

# ========== 渲染 .md ==========
def render_markdown(name, img_path_rel, parsed, category):
    md = []
    md.append(md_h1(name))
    if img_path_rel:
        md.append(f'![icon]({img_path_rel})\n\n')
    # 逐行
    md.append(md_kv_lines(parsed["lines"]))
    # 表格们
    for title, kv in parsed["tables"]:
        if kv:
            md.append(md_table(title, kv))
    # 说明 / 效果 / 获取
    if parsed.get("intro"):
        md.append(md_h2("简介"))
        md.append(md_quote(parsed["intro"]))
    if parsed.get("effect"):
        md.append(md_h2("效果 / 用途"))
        md.append(md_quote(parsed["effect"]))
    if parsed.get("obtain"):
        md.append(md_h2("获取地点 / 入手方法"))
        md.append(md_quote(parsed["obtain"]))

    # 署名（遵循 CC BY-NC-SA；强烈建议保留）
    md.append("\n---\n*来源：公开可访问的游戏百科页面（建议在仓库公开版面保留准确出处与链接，以遵守许可条款）。*\n")
    return "".join(md)

# ========== 索引扫描 & 选 3 个 ==========
def collect_detail_links(index_url):
    soup = get_soup(index_url)
    links = []
    for a in soup.select(".mw-parser-output a[href]"):
        href = a["href"]
        title = textnorm(a.get_text())
        if not title or not href.startswith("/eldenring/"): 
            continue
        # 排除索引/文件/编辑等
        if "特殊:" in href or "File:" in href or "文件:" in href or "action=" in href:
            continue
        links.append((title, BASE + href))
    # 去重保持顺序
    seen = set(); out=[]
    for t,u in links:
        if u in seen: continue
        seen.add(u); out.append((t,u))
    return out

def page_kind(soup, name):
    title = textnorm(soup.select_one("#firstHeading").get_text()) if soup.select_one("#firstHeading") else name
    txt = textnorm(soup.get_text(" ", strip=True))

    # 战灰：标题以“战灰：”
    if title.startswith("战灰：") or "道具效用" in txt and "战技" in txt:
        return "战灰"
    # 护符：标题或正文出现“护符”，且有道具效用
    if "护符" in title and "道具效用" in txt:
        return "护符"
    # 法术：正文包含“记忆空位/消耗FP/祷告/魔法”
    if any(k in txt for k in ["记忆空位","记忆空间","祷告","魔法"]) and any(k in txt for k in ["消耗FP","消耗专注值"]):
        return "法术"
    # 武器：常有“武器品质/攻击力/防御时减伤率/能力加成”
    if any(k in txt for k in ["武器品质","攻击力","防御时减伤率","能力加成","必需能力值"]):
        return "武器"
    # 物品：有“道具效用/获取途径”但不属于上述
    if "道具效用" in txt:
        return "物品"
    return "其他"

PARSERS = {
    "武器": parse_weapon,
    "护符": parse_talisman,
    "法术": parse_spell,
    "战灰": parse_ash,
    "物品": parse_item,
}

INDEX = {
    "武器": f"{BASE}/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88",
    "护符": f"{BASE}/%E6%8A%A4%E7%AC%A6%E4%B8%80%E8%A7%88",
    "法术": f"{BASE}/%E6%B3%95%E6%9C%AF%E4%B8%80%E8%A7%88",
    "战灰": f"{BASE}/%E6%88%98%E7%81%B0%E4%B8%80%E8%A7%88",
    "物品": f"{BASE}/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88",
}

def pick_three_from_category(cat):
    index_url = INDEX[cat]
    links = collect_detail_links(index_url)
    out = []
    seen_names = set()
    for title, url in links:
        try:
            soup = get_soup(url)
        except Exception:
            continue
        kind = page_kind(soup, title)
        if kind != cat:
            continue
        # 解析
        parser = PARSERS[cat]
        parsed = parser(soup, title)
        name = title
        if cat == "战灰":
            name = title.replace("战灰：", "", 1).strip() or title
        if name in seen_names:
            continue
        seen_names.add(name)
        img_url = pick_main_image(soup)
        img_path = save_image(img_url, IMG_ROOT / cat, slugify(name))
        md = render_markdown(name, img_path, parsed, cat)
        out.append((name, md))
        if len(out) >= 3:
            break
        time.sleep(0.6)
    return out

def write_markdowns():
    summary = {}
    for cat in ["武器","护符","法术","战灰","物品"]:
        items = pick_three_from_category(cat)
        dst = OUT_ROOT / cat
        dst.mkdir(parents=True, exist_ok=True)
        names = []
        for name, md in items:
            fp = dst / f"{slugify(name)}.md"
            with open(fp, "w", encoding="utf-8") as f:
                f.write(md)
            names.append(name)
        summary[cat] = names
    return summary

def write_readme(summary):
    lines = ["# 艾尔登法环物品样例（各类各 3 件）\n"]
    for cat, names in summary.items():
        lines.append(f"## {cat}（样例）\n")
        for n in names:
            slug = slugify(n)
            lines.append(f"- [{n}](items/{cat}/{slug}.md)")
        lines.append("")
    lines.append("\n> *注：建议在公开仓库保留准确来源与链接（BWIKI 页面遵循 CC BY-NC-SA 许可）。*")
    Path("README.md").write_text("\n".join(lines), encoding="utf-8")

def main():
    summary = write_markdowns()
    write_readme(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
