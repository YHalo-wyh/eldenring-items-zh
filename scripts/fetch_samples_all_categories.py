# scripts/fetch_samples_all_categories.py
# -*- coding: utf-8 -*-
"""
抓取 BWIKI（艾尔登法环）各大类各 3 个物品，生成 Markdown + 图片。
- 目录页 -> 子类页 -> 详情页 两级递归，避免把“一览/目录/分类/特殊/编辑/purge”等误收录
- 输出到 repo 根：
    items/<Category>/<Slug>.md
    images/<Category>/<slug>.png
- 逐行信息 + 表格（攻击/防御或主要属性）+ 图片
- 需要 Python 3.9+ 以及 requests, beautifulsoup4
使用：
  python scripts/fetch_samples_all_categories.py --per 3 --delay 0.6
  只跑部分类别：
  python scripts/fetch_samples_all_categories.py --categories 武器 护符 法术
"""

import os, re, time, argparse, hashlib, shutil
from urllib.parse import urljoin, unquote
import requests
from bs4 import BeautifulSoup

BASE = "https://wiki.biligame.com"
GAME = "eldenring"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ER-Sampler/1.0; +https://github.com/)",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 顶层类别与其索引页（优先选更接近具体列表的页）
CATEGORY_INDEX = {
    "武器": f"/{GAME}/%E6%AD%A6%E5%99%A8%E4%B8%80%E8%A7%88/%E8%BF%91%E6%88%98%E6%AD%A6%E5%99%A8%E7%B1%BB",  # 近战武器类 

---

如果你希望**只生成某几个大类**先看效果，比如“护符/法术/战灰”，可以改为：

```bash
python scripts/fetch_samples_all_categories.py --categories 护符 法术 战灰 --per 3 --clean
