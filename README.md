# Elden Ring Items (中文 · BWIKI 来源)

> 从 **BWIKI · 艾尔登法环 WIKI** 抓取并整理中文物品资料与图标，仅作非商业整理与学习用途。  
> 主目录入口（BWIKI）：[物品一览](https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88)

## 样例（已抓取几条示例，含图片）

### 红露滴圣杯瓶
<img src="https://patchwiki.biligame.com/images/eldenring/thumb/9/92/lmz1g6ouy63li9t6sziabpfn7gbuqou.png/128px-MENU_ItemIcon_00018.png" width="64" />
- 分类：基础道具 / 消耗品  
- 简述：使用后**恢复生命值**，可在赐福处补充。来源与文本见条目页。  
- 来源：BWIKI 条目《红露滴圣杯瓶》。  
- 链接：https://wiki.biligame.com/eldenring/%E7%BA%A2%E9%9C%B2%E6%BB%B4%E5%9C%A3%E6%9D%AF%E7%93%B6

### 蓝露滴圣杯瓶
<img src="https://patchwiki.biligame.com/images/eldenring/thumb/6/65/fszmlq6vswbwvvnwy6usrysqkemv67j.png/128px-MENU_ItemIcon_00022.png" width="64" />
- 分类：基础道具 / 消耗品  
- 简述：使用后**恢复专注值（FP）**，在赐福处补充。  
- 链接：https://wiki.biligame.com/eldenring/%E8%93%9D%E9%9C%B2%E6%BB%B4%E5%9C%A3%E6%9D%AF%E7%93%B6

### 巨人圣印记
<img src="https://patchwiki.biligame.com/images/eldenring/thumb/8/85/6ojgicz8rzst0vgfg9nq3wxgmjlhgb8.png/128px-%E5%B7%A8%E4%BA%BA%E5%9C%A3%E5%8D%B0%E8%AE%B0.png" width="64" />
- 分类：武器 / 圣印记  
- 简述：**强化“巨人火焰”相关祷告（约+20%）**，条目页有具体数值说明与获取位置。  
- 链接：https://wiki.biligame.com/eldenring/%E5%B7%A8%E4%BA%BA%E5%9C%A3%E5%8D%B0%E8%AE%B0

> 以上图标与说明来源于 BWIKI 对应页面与“物品一览”目录。物品的目录入口在这里：**[物品一览](https://wiki.biligame.com/eldenring/%E7%89%A9%E5%93%81%E4%B8%80%E8%A7%88)**。该页囊括基础道具、角色增益、骨灰、强化材料、贵重物品等分栏与跳转。 :contentReference[oaicite:0]{index=0}

## 数据格式
- `data/samples.json`：结构化样例（`name`, `category`, `desc`, `image_url`, `source_url`）
- `content/items/*.md`：每个物品一页，适合静态站点渲染

## 版权与署名
- 本仓库**不包含原始图片文件**，仅引用 BWIKI 静态图床链接与必要片段说明，并附上来源链接。  
- BWIKI 站点常见授权为 **CC BY-NC-SA 4.0**（以目标站点的“关于/版权”说明为准；不同子站可能表述位置不同）。示例页对该协议有说明：  
  - “如无特殊说明，本 WIKI 内页面均采用 CC BY-NC-SA 4.0” 的 BWIKI 站示例。:contentReference[oaicite:1]{index=1}  
  - CC BY-NC-SA 4.0 协议中文要点示例。:contentReference[oaicite:2]{index=2}
- 严格**非商业性**使用，保留**署名与相同方式共享**；同时尊重 FromSoftware / 万代南梦宫对游戏素材的权利。

## 抓取脚本
见 `scripts/fetch_bwiki_samples.py`，使用 MediaWiki API 读取页面，输出 JSON/Markdown（仅演示若干样例）。
