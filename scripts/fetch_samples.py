# -*- coding: utf-8 -*-
"""
抓取 BWIKI（艾尔登法环）若干样例条目，生成：
- items/<类别>/<物品名>.md
- assets/images/<类别>/<物品名>.png

重点修复：
- 防具页“右侧大图”抓不到的问题（新增更智能的主图选择器）
- 顶部信息换行样式（武器/防具分流）
- FP/重量重复、错位问题（只保留解析到的最终值）
- 表格字段标准化（武器：攻击力/减伤率/加成/需求；防具：减伤率/抵抗力）
"""
import os, re, json, time, html
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.biligame.com"
GAME = "eldenring"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9"
}

OUT_MD = "items"
OUT_IMG = "assets/images"
os.makedirs(OUT_MD, exist_ok=True)
os.makedirs(OUT_IMG, exist_ok=True)

# 出于合规考虑，默认在文末保留一行“数据来自公开资料”
ADD_SOURCE_FOOTER = True

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text

def soup_of(url):
    return BeautifulSoup(get(url), "lxml")

def sanitize_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "", name.strip())
    return name

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def is_elden_image(src: str) -> bool:
    return "/images/eldenring/" in src

def score_img(img, item_name):
    """给图片打分，优先：装备图（class/img-equip）、文件大、alt含物品名、尺寸>=80"""
    score = 0
    src = img.get("src") or ""
    if not is_elden_image(src): 
        return -1
    alt = (img.get("alt") or "").strip()
    cls = " ".join(img.get("class", []))
    w = img.get("width")
    h = img.get("height")
    try:
        w = int(w) if w else 0
        h = int(h) if h else 0
    except:
        w = h = 0

    if "img-equip" in cls: score += 5
    if item_name and (item_name in alt): score += 3
    if w >= 80 or h >= 80: score += 2
    # thumb 路径一般是真图：/thumb/.../120px-xxx.png 加分
    if "/thumb/" in src: score += 1
    return score

def pick_main_image(soup, item_name):
    # 1) 优先任何装备图类；2) alt 含名；3) 尺寸过滤小图标；4) 否则取第一张 eldenring 图
    imgs = soup.select("img")
    candidates = []
    for img in imgs:
        src = img.get("src") or ""
        if not src: 
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE, src)
        img["src_abs"] = src
        s = score_img(img, item_name)
        if s >= 0:
            candidates.append((s, img))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1].get("src_abs")
    # 规范成 120px 缩略：把 thumb 尺寸替换为 120px
    if "/thumb/" in best and re.search(r"/\d+px-", best):
        best = re.sub(r"/\d+px-", "/120px-", best)
    return best

def dl_image(url, save_path):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print("下载图片失败：", url, e)
        return False

def textize(el):
    if el is None: return ""
    for br in el.find_all("br"):
        br.replace_with("\n")
    return html.unescape(el.get_text("\n").strip())

def extract_block_values(block_text):
    # 把“物理121\n魔力0\n火0..”转成 dict
    out = {}
    for line in block_text.splitlines():
        line = line.strip(" ：:").replace("\u3000","")
        if not line: continue
        m = re.match(r"(.+?)[：:]\s*([^\s].*)$", line)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            out[k] = v
        else:
            # 尾部是数值的行（如：物理   121）
            m2 = re.match(r"(.+?)\s+([\-–—]?\d+(\.\d+)?)$", line)
            if m2:
                out[m2.group(1).strip()] = m2.group(2)
    return out

def parse_weapon_page(soup):
    """
    适配“模板:武器图鉴”结构：
    - 顶部左列：类型/动作/战技 + FP + 重量
    - 表格：攻击力 / 防御时减伤率
    - 能力加成 / 必需能力值
    - 附加效果、简介、获取地点、战技说明、强化石类型
    """
    data = {
        "name": soup.select_one("#firstHeading").get_text(strip=True),
        "quality": "",
        "image": "",
        "type_info": {"lines": [], "fp": "", "weight": ""},
        "attack": {}, "guard": {},
        "scaling": {}, "requirements": {},
        "extra_effect": "", "intro": "", "location": "",
        "ash_of_war": "", "ash_desc": "", "upgrade": ""
    }

    # 主图
    data["image"] = pick_main_image(soup, data["name"]) or ""

    table = soup.select_one("table.wikitable")
    if not table:
        return data

    ttxt = textize(table)

    # 品质
    mq = re.search(r"武器品质[:：]\s*([^\s\n]+)", ttxt)
    if mq: data["quality"] = mq.group(1).strip()

    # 左列块（包含类型/动作/战技/FP/重量）
    # 找到“攻击力/减伤率”所在行，把之前那一格作为左列内容
    cells = table.select("td")
    left_block = ""
    for i, td in enumerate(cells):
        if "攻击力" in textize(td):
            # 左边那格 + 其上方的类型行
            if i-1 >= 0:
                left_block = textize(cells[i-1])
            break
    if not left_block and cells:
        left_block = textize(cells[0])

    # 拆出行：先抓战技（若出现），再抓 FP / 重量，剩余行当作类型/动作
    lines = [l.strip() for l in left_block.splitlines() if l.strip()]
    # FP
    for l in list(lines):
        mfp = re.search(r"消耗?专注值?\s*([0-9]+（.*?）)", l)
        if mfp:
            data["type_info"]["fp"] = mfp.group(1)
            lines.remove(l)
            break
    # 重量
    for l in list(lines):
        mw = re.search(r"重量\s*([0-9.]+)", l)
        if mw:
            data["type_info"]["weight"] = mw.group(1)
            lines.remove(l)
            break
    # 战技名（若行中独立出现）
    # 经验上，战技会作为单独一行（比如“碎步”“王朝剑技”），这里不过分猜测，留给下面战技段落再补充
    data["type_info"]["lines"] = lines

    # 攻击力 / 防御时减伤率 这两块通常成对出现
    atk_block, grd_block = "", ""
    for i in range(len(cells) - 1):
        if "攻击力" in textize(cells[i]):
            atk_block = textize(cells[i+1])
        if "防御时减伤率" in textize(cells[i]):
            grd_block = textize(cells[i+1])
    data["attack"] = extract_block_values(atk_block)
    data["guard"]  = extract_block_values(grd_block)

    # 能力加成 / 必需能力值
    scl_block, req_block = "", ""
    for i in range(len(cells) - 1):
        if "能力加成" in textize(cells[i]):
            scl_block = textize(cells[i+1])
        if "必需能力值" in textize(cells[i]):
            req_block = textize(cells[i+1])
    data["scaling"] = extract_block_values(scl_block)
    data["requirements"] = extract_block_values(req_block)

    # 附加效果
    mfx = re.search(r"附加效果\s*\n+(.+?)\n{1,}", ttxt, re.S)
    if mfx:
        data["extra_effect"] = mfx.group(1).strip()

    # 其它段落（简介 / 获取地点 / 战技 / 强化）
    # 直接按标题关键字在整张表里截断
    def grab_after(title):
        m = re.search(title + r"\s*\n+(.+?)(?:\n{2,}|$)", ttxt, re.S)
        return m.group(1).strip() if m else ""

    data["intro"] = grab_after("简介")
    data["location"] = grab_after("获取地点")
    # 战技（标题通常像“专属战技-王朝剑技” 或 “战技-黄金刀刃”）
    mash = re.search(r"(?:专属)?战技[-－](.+?)\n", ttxt)
    if mash:
        data["ash_of_war"] = mash.group(1).strip()
        data["ash_desc"] = grab_after(re.escape(mash.group(0).strip()))
    data["upgrade"] = grab_after("武器使用强化石类型")
    return data

def parse_armor_page(soup):
    """
    适配“模板:防具图鉴”结构（以‘居民头巾’、‘雪魔女尖帽’为例）：
    - 顶部：重量
    - 两列表：减伤率 / 抵抗力
    - 获取途径 / 说明
    """
    name = soup.select_one("#firstHeading").get_text(strip=True)
    table = soup.select_one("table.wikitable")
    data = {
        "name": name,
        "image": pick_main_image(soup, name) or "",
        "weight": "",
        "defense": {},    # 减伤率
        "resist": {},     # 抵抗力
        "location": "",
        "intro": ""
    }
    if not table:
        return data

    ttxt = textize(table)
    mw = re.search(r"重量\s*([\d.]+)", ttxt)
    if mw: data["weight"] = mw.group(1)

    # 找到“减伤率 / 抵抗力”所在两格
    tds = table.select("td")
    left, right = "", ""
    for i in range(len(tds) - 1):
        t = textize(tds[i])
        if "减伤率" in t:
            left  = textize(tds[i+1])
        if "抵抗力" in t:
            right = textize(tds[i+1])
    data["defense"] = extract_block_values(left)
    data["resist"]  = extract_block_values(right)

    # 获取途径 / 文案说明（右侧大块描述）
    def grab_after(title):
        m = re.search(title + r"\s*\n+(.+?)(?:\n{2,}|$)", ttxt, re.S)
        return m.group(1).strip() if m else ""
    data["location"] = grab_after("获取途径")
    # 说明块通常在右侧描述，这里直接去主内容里抓“简介风格”的段落
    # 保险起见，也把整页正文再扫一遍
    page_body = soup.select_one("#mw-content-text")
    body_txt = textize(page_body)
    mintro = re.search(r"(?:简介|说明)\s*\n+(.+?)(?:\n{2,}|$)", body_txt, re.S)
    if mintro:
        data["intro"] = mintro.group(1).strip()
    else:
        # 右侧描述通常紧跟图片，这里作为备选抓表格右侧块
        # 如果没有，就留空
        pass
    return data

def is_weapon_page(soup):
    box = soup.select_one("table.wikitable")
    if not box: return False
    t = textize(box)
    return ("武器品质" in t) or ("攻击力" in t and "防御时减伤率" in t)

def is_armor_page(soup):
    box = soup.select_one("table.wikitable")
    if not box: return False
    t = textize(box)
    return ("减伤率" in t and "抵抗力" in t) or ("重量" in t and "获取途径" in t)

def write_weapon_md(d, out_dir):
    ensure_dir(out_dir)
    name = sanitize_name(d["name"])
    img_dir = os.path.join(OUT_IMG, "武器")
    ensure_dir(img_dir)
    img_rel = ""
    if d["image"]:
        img_path = os.path.join(img_dir, f"{name}.png")
        if dl_image(d["image"], img_path):
            img_rel = "/" + img_path.replace("\\", "/")

    lines = []
    if d["quality"]: lines.append(f"武器品质: {d['quality']}")
    for l in d["type_info"].get("lines", []):
        if l and "消耗专注" not in l and "重量" not in l:
            lines.append(l)
    if d["ash_of_war"]:
        lines.append(d["ash_of_war"])
    if d["type_info"].get("fp"):
        lines.append(f"消耗专注值 {d['type_info']['fp']}")
    if d["type_info"].get("weight"):
        lines.append(f"重量 {d['type_info']['weight']}")

    md = [f"# {d['name']}", ""]
    if img_rel: md.append(f"![icon]({img_rel})")
    md += ["", *[f"{s}" for s in lines], ""]
    # 简介 / 获取地点 / 战技说明放段落
    if d["intro"]:
        md += ["> " + d["intro"].replace("\n", "\n> "), ""]
    if d["location"]:
        md += [f"**获取地点**：{d['location']}", ""]
    if d["ash_desc"]:
        md += ["**战技说明**", "", d["ash_desc"], ""]
    # 表格
    def mk_table(title, kv):
        if not kv: return []
        rows = [f"### {title}", "", "| 项目 | 数值 |", "|---|---|"]
        for k,v in kv.items():
            rows.append(f"| {k} | {v} |")
        rows.append("")
        return rows
    md += mk_table("攻击力", d["attack"])
    md += mk_table("防御时减伤率", d["guard"])
    md += mk_table("能力加成", d["scaling"])
    md += mk_table("必需能力值", d["requirements"])

    if d["extra_effect"]:
        md += [f"**附加效果**：{d['extra_effect']}", ""]
    if d["upgrade"]:
        md += [f"**强化石类型**：{d['upgrade']}", ""]
    if ADD_SOURCE_FOOTER:
        md += ["\n<sub>数据来自公开资料整理</sub>"]

    with open(os.path.join(out_dir, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

def write_armor_md(d, out_dir):
    ensure_dir(out_dir)
    name = sanitize_name(d["name"])
    img_dir = os.path.join(OUT_IMG, "防具-头部")
    ensure_dir(img_dir)
    img_rel = ""
    if d["image"]:
        img_path = os.path.join(img_dir, f"{name}.png")
        if dl_image(d["image"], img_path):
            img_rel = "/" + img_path.replace("\\", "/")

    md = [f"# {d['name']}", ""]
    if img_rel: md.append(f"![icon]({img_rel})")
    md.append("")
    if d["weight"]:
        md += [f"重量 {d['weight']}", ""]

    def mk_table(title, kv):
        if not kv: return []
        rows = [f"### {title}", "", "| 项目 | 数值 |", "|---|---|"]
        for k,v in kv.items():
            rows.append(f"| {k} | {v} |")
        rows.append("")
        return rows

    md += mk_table("减伤率", d["defense"])
    md += mk_table("抵抗力", d["resist"])

    if d["location"]:
        md += [f"**获取途径**：{d['location']}", ""]
    if d["intro"]:
        md += ["> " + d["intro"].replace("\n", "\n> "), ""]
    if ADD_SOURCE_FOOTER:
        md += ["\n<sub>数据来自公开资料整理</sub>"]

    with open(os.path.join(out_dir, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

def fetch_one(url, out_root):
    sp = soup_of(url)
    if is_weapon_page(sp):
        d = parse_weapon_page(sp)
        write_weapon_md(d, os.path.join(out_root, "武器"))
        print("武器：", d["name"])
    elif is_armor_page(sp):
        d = parse_armor_page(sp)
        write_armor_md(d, os.path.join(out_root, "防具-头部"))
        print("防具：", d["name"])
    else:
        print("跳过（非武器/防具模板）：", url)

def main():
    # —— 样例（武器 3 个 + 头部防具 3 个）——
    weapon_samples = [
        # 匕首系 / 直剑系里挑 3 个具有代表性的页面
        f"{BASE}/{GAME}/%E4%BD%BF%E5%91%BD%E7%9F%AD%E5%88%80",  # 使命短刀
        f"{BASE}/{GAME}/%E4%BA%94%E6%8C%87%E5%89%91",          # 五指剑
        f"{BASE}/{GAME}/%E9%B2%9C%E8%A1%80%E6%97%8B%E6%B5%81",  # 鲜血旋流
    ]
    head_armor_samples = [
        f"{BASE}/{GAME}/%E5%B1%85%E6%B0%91%E5%A4%B4%E5%B7%BE",              # 居民头巾
        f"{BASE}/{GAME}/%E9%9B%AA%E9%AD%94%E5%A5%B3%E5%B0%96%E5%B8%BD",      # 雪魔女尖帽
        f"{BASE}/{GAME}/%E5%B0%8F%E6%81%B6%E9%AD%94%E5%A4%B4%E7%BD%A9%EF%BC%88%E9%95%BF%E7%94%9F%E8%80%85%EF%BC%89",  # 小恶魔头罩（长生者）
    ]

    # 清空旧样例（可选）
    for sub in ["武器", "防具-头部"]:
        p = os.path.join(OUT_MD, sub)
        if os.path.isdir(p):
            for fn in os.listdir(p):
                if fn.endswith(".md"):
                    os.remove(os.path.join(p, fn))

    for url in weapon_samples + head_armor_samples:
        try:
            fetch_one(url, OUT_MD)
            time.sleep(0.8)
        except Exception as e:
            print("抓取失败：", url, e)

if __name__ == "__main__":
    main()
