# scripts/fetch_samples_all_categories.py
# -*- coding: utf-8 -*-
"""
抓取 BWIKI（艾尔登法环）各大类各 3 个物品，生成 Markdown + 图片（逐行信息 + 两张表格）。
- 目录页 -> 子类页 -> 详情页 两级递归；过滤掉“一览/目录/分类/特殊/编辑/purge”等。
- 输出：
    items/<Category>/<slug>.md
    images/<Category>/<slug>.png
- 需要 Python 3.9+ 和：requests, beautifulsoup4
使用：
  python scripts/fetch_samples_all_categories.py --per 3 --delay 0.6 --clean
  只跑部分类别：
  python scripts/fetch_samples_all_categories.py --categories 武器 护符 法术 --per 3 --clean
"""
import os, re, time, argparse, hashlib, shutil
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.biligame.com"
GAME = "eldenring"
HDRS = {"User-Agent": "Mozilla/5.0 (ER-Sampler)", "Accept-Language": "zh-CN,zh;q=0.9"}

# 顶层类别索引页（选更稳定/内容更全的入口）
CATEGORY_INDEX = {
    "武器": f"/{GAME}/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88/%E8%BF%91%E6%88%98%E6%AD%A6%E5%99%A8%E7%B1%BB",   # 近战武器类
    "防具": f"/{GAME}/%E9%98%B2%E5%85%B7%E4%B8%80%E8%A7%88",
    "护符": f"/{GAME}/%E6%8A%A4%E7%AC%A6%E4%B8%80%E8%A7%88",
    "物品": f"/{GAME}/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88",
    "法术": f"/{GAME}/%E6%B3%95%E6%9C%AF%E4%B8%80%E8%A7%88",
    # 战灰目录分散，直接从“战灰：××文本”类条目反向采集
    "战灰": f"/{GAME}/%E6%88%98%E7%81%B0%EF%BC%9A%E5%B2%A9%E7%9F%B3%E5%89%91%E6%96%87%E6%9C%AC",
}

BAD_PATTERNS = (
    "一览", "目录", "分类", "特殊", "模板", "文件:", "MediaWiki:",
    "action=edit", "action=history", "action=purge", "redlink=1", "javascript:"
)

S = requests.Session()
S.headers.update(HDRS)

def full(url: str) -> str:
    if url.startswith("//"): return "https:" + url
    if url.startswith("/"):  return BASE + url
    if url.startswith("http"): return url
    return urljoin(BASE, url)

def bad(href_or_title: str) -> bool:
    h = (href_or_title or "").lower()
    return any(p.lower() in h for p in BAD_PATTERNS)

def soup_get(url: str) -> BeautifulSoup:
    r = S.get(url, timeout=25)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def slugify(t: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5\-]+", "-", t).strip("-")
    return s or hashlib.md5(t.encode("utf-8")).hexdigest()[:8]

def pick_icon(soup: BeautifulSoup) -> str | None:
    # 优先找装备/物品小图；统一放大到 120px（若有）
    cands = []
    for im in soup.select(".img-equip, .infobox img, .mw-parser-output img"):
        src = im.get("src") or ""
        if "images/eldenring/" in src and src.endswith(".png"):
            cands.append(src)
    if not cands:
        return None
    cands.sort(key=len, reverse=True)
    best = cands[0].replace("/80px-", "/120px-")
    return full(best)

def page_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("#firstHeading")
    return (h1.get_text(strip=True) if h1 else "").replace("（页面不存在）", "")

def extract_lines(soup: BeautifulSoup) -> list[str]:
    """
    抽“逐行信息块”：如
      武器品质: 白
      短剑
      斩击/突刺
      碎步
      消耗专注值 3（-/-）
      重量 3.5
    - 过滤孤立纯数值行（避免 3（-/-）/3.5 重复）。
    - 控制在前 ~8 行，避免把“简介/获取”正文段带进来。
    """
    block_txt = ""
    for tb in soup.select(".mw-parser-output table, .wikitable"):
        t = tb.get_text("\n", strip=True)
        if t and len(t) > 20:
            block_txt = t; break
    if not block_txt:
        blk = soup.select_one(".mw-parser-output")
        if blk: block_txt = blk.get_text("\n", strip=True)[:400]

    lines = [ln.strip() for ln in (block_txt or "").splitlines() if ln.strip()]
    head = []
    for ln in lines:
        if any(x in ln for x in ["攻击力", "防御时减伤率", "能力加成", "必需能力值", "附加效果"]):
            break
        head.append(ln)
    def looks_num(s: str) -> bool:
        return bool(re.fullmatch(r"[0-9]+(\.[0-9])?\s*(（.*）)?", s))
    cleaned = [ln for ln in head if not looks_num(ln)]
    return cleaned[:8]

def extract_tables(soup: BeautifulSoup) -> dict:
    out = {"attack": [], "guard": [], "scaling": [], "req": []}
    tbs = soup.select("table.wikitable")
    if len(tbs) >= 2:
        left = tbs[0].get_text("\n", strip=True).splitlines()
        right = tbs[1].get_text("\n", strip=True).splitlines()
        def kv(lines):
            res=[]
            for ln in lines:
                if "项目" in ln or "数值" in ln: continue
                m = re.match(r"(.+?)\s+([A-Za-z0-9\.\-]+)$", ln)
                if m: res.append((m.group(1).strip(), m.group(2).strip()))
            return res
        out["attack"] = kv(left)
        out["guard"]  = kv(right)

    blk = soup.select_one(".mw-parser-output")
    if blk:
        txt = blk.get_text("\n", strip=True)
        sc = re.search(r"能力加成(.+?)必需能力值", txt, flags=re.S)
        rq = re.search(r"必需能力值(.+?)(附加效果|简介|获取|$)", txt, flags=re.S)
        def parse(seg):
            res=[]; 
            if not seg: return res
            for ln in seg.splitlines():
                ln=ln.strip()
                m=re.match(r"(力气|灵巧|智力|信仰|感应)\s*[:：]?\s*([A-E\-0-9]+)", ln)
                if m: res.append((m.group(1), m.group(2)))
            return res
        out["scaling"] = parse(sc.group(1) if sc else "")
        out["req"]     = parse(rq.group(1) if rq else "")
    return out

def md_render(category, title, url, icon_url, lines, tables) -> str:
    md = [f"# {title}", ""]
    if icon_url:
        md += [f"![]({icon_url})", ""]
    for ln in lines: md.append(ln)
    md.append("")
    def t2md(name, rows):
        if not rows: return
        md.append(f"## {name}\n\n| 项目 | 数值 |\n| --- | --- |")
        for k,v in rows: md.append(f"| {k} | {v} |")
        md.append("")
    t2md("攻击力", tables.get("attack"))
    t2md("防御时减伤率", tables.get("guard"))
    t2md("能力加成", tables.get("scaling"))
    t2md("必需能力值", tables.get("req"))
    md.append("> 来源：该条目的公开百科页面（保留署名以符合 CC BY-NC-SA 4.0）。")
    md.append(f"> {url}\n")
    return "\n".join(md)

def collect_detail_links_from_page(url: str, want: int) -> list[tuple[str,str]]:
    sp = soup_get(url)
    links=[]
    for a in sp.select(".mw-parser-output a[href]"):
        href = a.get("href") or ""
        title = (a.get("title") or a.get_text() or "").strip()
        if not href or not title: continue
        if bad(href) or bad(title): continue
        if href.startswith("#"): continue
        fullu = full(href)
        if f"/{GAME}/" not in fullu: continue
        if 1 < len(title) <= 20:
            links.append((title, fullu))
    # 去重并截取
    seen=set(); uniq=[]
    for t,u in links:
        if t in seen: continue
        seen.add(t); uniq.append((t,u))
        if len(uniq)>=want: break
    return uniq

def collect_detail_links(index_url: str, want: int) -> list[tuple[str,str]]:
    got = collect_detail_links_from_page(index_url, want)
    if len(got)>=want: return got[:want]
    sp = soup_get(index_url)
    subs=[]
    for a in sp.select(".mw-parser-output a[href]"):
        href=a.get("href") or ""; title=(a.get("title") or a.get_text() or "").strip()
        if not href or not title: continue
        if bad(href) or bad(title): continue
        fu = full(href)
        if f"/{GAME}/" in fu: subs.append(fu)
    for sub in subs[:12]:
        more = collect_detail_links_from_page(sub, want-len(got))
        for t,u in more:
            if t not in {x[0] for x in got}:
                got.append((t,u))
                if len(got)>=want: return got
    return got

def download(url: str, outpath: str):
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    r = S.get(url, timeout=25); r.raise_for_status()
    with open(outpath, "wb") as f: f.write(r.content)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=3)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--categories", nargs="*", default=list(CATEGORY_INDEX.keys()))
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    if args.clean:
        shutil.rmtree("items", ignore_errors=True)
        shutil.rmtree("images", ignore_errors=True)

    summary = {}
    for cate in args.categories:
        idx = CATEGORY_INDEX.get(cate)
        if not idx: 
            print(f"[跳过] 未知类别 {cate}")
            continue
        index_url = full(idx)
        print(f"[{cate}] 索引：{index_url}")
        pairs = collect_detail_links(index_url, args.per)
        if not pairs:
            print("  !! 没抓到详情链接"); continue

        saved=[]
        for title, url in pairs[:args.per]:
            try:
                sp = soup_get(url)
                real_title = page_title(sp) or title
                icon = pick_icon(sp)
                lines = extract_lines(sp)
                tables = extract_tables(sp)

                slug = slugify(real_title)
                md_path = os.path.join("items", cate, f"{slug}.md")
                os.makedirs(os.path.dirname(md_path), exist_ok=True)

                icon_rel = None
                if icon:
                    icon_rel = os.path.join("images", cate, f"{slug}.png")
                    download(icon, icon_rel)
                    icon_md = "/" + icon_rel.replace("\\", "/")
                else:
                    icon_md = None

                md = md_render(cate, real_title, url, icon_md, lines, tables)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md)

                saved.append((real_title, md_path))
                print(f"  ✓ {real_title}")
                time.sleep(args.delay)
            except Exception as e:
                print(f"  ✗ {title} -> {e}")
        summary[cate]=saved

    # 生成首页索引
    lines=["# 物品索引（样例，每类 3 个）", ""]
    for cate, items in summary.items():
        lines.append(f"## {cate}（{len(items)}）\n")
        for t, p in items:
            lines.append(f"- [{t}]({p.replace('\\', '/')})")
        lines.append("")
    with open("README.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n完成。")

if __name__ == "__main__":
    main()
