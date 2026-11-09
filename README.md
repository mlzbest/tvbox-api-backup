# 海量接口搬运备份站

一个基于 GitHub Actions 自动化 + 静态托管的 TVBox 接口搬运备份项目。三套脚本每日定时抓取、解析、聚合各类 TVBox 接口与直播源，产物自动提交回仓库，前端页面实时读取展示，支持一键复制、搜索、多域名备份。

---

## 目录

- [项目简介](#项目简介)
- [在线访问](#在线访问)
- [仓库结构](#仓库结构)
- [自动化工作流](#自动化工作流)
- [三个脚本详解](#三个脚本详解)
- [前端页面](#前端页面)
- [配置文件说明](#配置文件说明)
- [本地手动运行](#本地手动运行)
- [部署到 Cloudflare Pages](#部署到-cloudflare-pages)
- [常见问题与避坑](#常见问题与避坑)
- [配置修改速查](#配置修改速查)
- [一键校验清单](#一键校验清单)
- [声明](#声明)

---

## 项目简介

本项目把互联网上公开的 TVBox 接口资源进行批量搬运、备份与聚合，通过 GitHub Actions 定时抓取并提交到仓库，再由静态托管前端页面，实现：

- **接口备份**：90+ 条点播接口，每条接口提供 1~5 个镜像线路，自动选通。
- **直播聚合**：从所有点播接口的 lives 字段提取直播源，穿透套壳后生成播放列表。
- **采集站聚合**：从所有接口的 sites 中提取 type 0/1 的采集站，生成独立聚合接口。
- **Py 站聚合**：提取以 .py 结尾的 Py 站接口，补全字段后生成聚合接口。
- **下载资源**：批量抓取本地包/下载资源，落到 ry/ 目录。
- **多域名备份**：前端提供三个互为备份的访问域名，任一可用。
- **静态托管**：纯前端页面，无需后端，部署简单，访问快。

---

## 在线访问

| 域名 | 用途 |
|------|------|
| https://lubin776.github.io/tvbox-api-backup/ | GitHub Pages 主站 |
| https://0.12yue.de5.net | 备份一线 |
| https://0.cdz.qzz.io | 备份二线 |
| https://0.wdzb.eu.cc | 备份三线 |

域名指向同一份静态站点，互为备份。

---

## 仓库结构

```
tvbox-api-backup/
├── .github/
│   └── workflows/
│       ├── ry下载.yml                     ⭐ 工作流A：每日解析 ry
│       ├── api下载.yml                    ⭐ 工作流B：抓取 tvbox 接口
│       └── 接口处理扫描工具.yml           ⭐ 工作流C：全量扫描处理
│
├── python/                                ⭐ 脚本统一目录
│   ├── ry下载.py                         ⭐ 脚本A
│   ├── api下载.py                        ⭐ 脚本B
│   └── 接口扫描处理工具.py               ⭐ 脚本C
│
├── rylinks.txt                            ⭐ 脚本A 配置（名称,URL，文本格式）
├── apilinks.txt                           ⭐ 脚本B 配置（新格式：名称,URL1,URL2,...）
├── api_list.json                          ⭐ 脚本B 老格式兜底（可选）
│
├── ry/                                    ← 脚本A 产物
├── tvbox/                                 ← 脚本B 产物
│   ├── *.json                             ← 各线路接口文件
│   ├── 海量直播聚合接口.json               ← 脚本C 聚合锚点
│   ├── 海量采集聚合接口.json               ← 脚本C 聚合锚点
│   ├── 海量py聚合接口.json                 ← 脚本C 聚合锚点
│   └── live/                              ← 脚本C 播放列表输出
│       ├── xxx.m3u
│       └── xxx.txt
├── livelist.txt                           ← 脚本C 直播源索引
├── cjlist.txt                             ← 脚本C 采集站列表
├── pylist.txt                             ← 脚本C Py 站列表
├── list.txt                               ← 脚本B 接口清单
├── SUMMARY.txt                            ← 脚本B 运行摘要
│
├── config.json                            → 前端内容配置（内容展示纳入 config）
├── index.html                             → 前端主页面
├── style.css / js/                        → 前端资源
└── README.md                              → 本文件
```

---

## 自动化工作流

三个工作流位于 `.github/workflows/`，推送到 main 分支后 GitHub 自动识别。三者共用 `concurrency: tvbox-repo-write`，串行执行，避免并发 push 冲突。

| 工作流 | Cron（UTC） | 北京时间 | 功能 | 产物 |
|--------|-------------|---------|------|------|
| `ry下载.yml` | `0 17 * * *` | 每日 01:00 | 每日解析 ry | `ry/` |
| `api下载.yml` | `5 16 * * *` | 每两日 00:05 | 抓取接口（奇偶日 gate） | `tvbox/*.json`、`list.txt`、`SUMMARY.txt` |
| `接口处理扫描工具.yml` | `0 18 * * *` | 每日 02:00 | 全量扫描处理 | `tvbox/live/`、`livelist.txt`、`cjlist.txt`、`pylist.txt`、三个聚合 JSON |

### 工作流特性

- **奇偶日 gate**：`api下载.yml` 只在 UTC 奇日运行，偶日自动跳过，实现"两日一更"。
- **手动触发**：所有工作流支持 `workflow_dispatch`，可勾选 `force` 强制跳过日期判断，勾选 `debug` 输出详细日志。
- **依赖安装**：`pip install requests urllib3`。
- **提交产物**：`git pull --rebase --autostash` → `git add` → `git commit` → `git push`，只用 `git add` 明确指定目录，绝不 `git add -A`。

---

## 三个脚本详解

### 脚本 A：ry下载.py

**作用**：批量下载「本地包/下载资源」类接口，解析 JSON，把结果落到 `ry/` 目录。每次运行强制覆盖旧产物。

**配置：`rylinks.txt`（仓库根，文本格式）**

```
潇洒下载, https://9877.kstore.space/single.json
奇奇下载, http://bd.qiqiv.cn/666.json
菠菜园下载, https://0.12yue.de5.net/tvbox/x/lib/菠菜园下载.json
柒豪下载, https://raw.gitcode.com/qihao/qihaoyyds/raw/main/版本.json
```

**语法规则：**
- 每行一条，格式 `名称, URL`（英文逗号分隔，两边可有空格）
- `#` 开头 = 注释
- 名称可省略，脚本自动取域名作文件名
- 支持 `file://`（本地）和 `raw:base64`（内联）用于调试

**运行方式**

```bash
python3 python/ry下载.py
python3 python/ry下载.py -c /path/to/other.txt   # 指定配置
python3 python/ry下载.py -l "URL" --name 测试      # 单链接调试
```

---

### 脚本 B：api下载.py

**作用**：抓取 TVBox 的「接口线路」（`饭太硬`、`嗷呜`、`肥猫` 等 90+ 线路），每个接口可带多镜像（同一行多个 URL），自动选通，生成 `tvbox/*.json` + `list.txt` + `SUMMARY.txt`。

**配置加载优先级**

| 优先级 | 入口 | 格式 | 说明 |
|--------|------|------|------|
| 1 | `--config <路径>` | 按扩展名判断 | `.json` 走老格式，其余走新 txt |
| 2 | `apilinks.txt` | 新 txt 列表 | 优先，存在即用 |
| 3 | `api_list.json` | 老 JSON | 兜底，新文件不存在时才用 |
| 4 | `api_list.py` | Python 模块 | 最老式兜底 |

**新格式：`apilinks.txt`（仓库根，推荐）**

每行一条接口，名称,URL1,URL2,...，多 URL 用逗号分隔，`#` 开头为注释。

```
# 单 URL
更新专用接口, https://0.12yue.de5.net/tvbox/更新专用接口.json
菠菜pro, https://0.12yue.de5.net/5/x4pro.json

# 多 URL（同一名称多个镜像，脚本会自动分组去重）
饭太硬, http://www.饭太硬.net/tv, http://www.饭太硬.art/tv, http://fty.xxooo.cf/tv, http://fty.888484.xyz/tv, http://fty.333232.xyz/tv
嗷呜, http://www.英格里希嗷呜.top/tv, https://9763.kstore.vip/aowu.json, http://itv666.cc/aowu/config.webp
潇洒, https://9877.kstore.space/single.json, https://9877.kstore.space/AnotherD/api.json, https://9877.kstore.space/one.json, https://9877.kstore.space/ONE/one.json
```

**规则：**
- 第一个字段是名称，后面全部是 URL
- URL 数量不限，1 个或多个都行
- 空行、`#` 开头的行会被忽略

**老格式：`api_list.json`（兜底）**

新文件不存在时才会读它，格式与原来完全一致：

```json
{
  "API_LIST": [
    ["更新专用接口", "https://0.12yue.de5.net/tvbox/更新专用接口.json"],
    ["菠菜pro", "https://0.12yue.de5.net/5/x4pro.json"]
  ],
  "API_MIRRORS": {
    "饭太硬": [
      "http://www.饭太硬.net/tv",
      "http://www.饭太硬.art/tv"
    ]
  }
}
```

**运行方式**

```bash
python3 python/api下载.py
python3 python/api下载.py --debug
python3 python/api下载.py --check-config
python3 python/api下载.py --config my_links.txt
python3 python/api下载.py --config my_api_list.json
```

---

### 脚本 C：接口扫描处理工具.py

**作用**：TVBox 接口全量扫描与聚合处理工具，覆盖**直播源下载**、**采集站提取**、**Py 站提取**三大模块，模拟 TVBox 客户端环境，生成播放列表文件及多种聚合索引。

**核心原则**：对外文件名锚定原始条目（不受套壳跳转影响），保证外部访问路径长期稳定。

#### 模块一：直播源（lives）

1. **扫描提取**：遍历 `tvbox/*.json` → 读取 `lives` → 提取有 name 且有 url 的条目（URL 去重）
2. **聚合命名**：名称冲突自动编号 `xxx → xxx2线 → xxx3线`，写入 `tvbox/海量直播聚合接口.json` 锚点
3. **批量下载**：模拟 TVBox 请求，套壳检测（最多递归 5 层），写出播放列表到 `tvbox/live/`
4. **生成索引**：更新 `livelist.txt`（旧记录保留，新记录覆盖）

#### 模块二：采集站（sites type 0/1）

5. **扫描提取**：遍历 `tvbox/*.json` → 读取 `sites` → 提取 `type` 为 0 或 1 的条目（URL 去重）
6. **输出列表**：写入 `cjlist.txt`，格式：`标识|名称|type: x|api: http...`
7. **生成聚合接口**：转换为标准站点格式，插入模板，输出 `tvbox/海量采集聚合接口.json`（旧记录保留，新记录覆盖）

#### 模块三：Py 站（sites api 以 .py 结尾）

8. **扫描提取**：遍历 `tvbox/*.json` → 读取 `sites` → 提取 `api` 以 `.py` 结尾的条目（URL 去重）
9. **输出列表**：写入 `pylist.txt`，格式同上
10. **生成聚合接口**：补全字段后输出 `tvbox/海量py聚合接口.json`（旧记录保留，新记录覆盖）

**运行方式**

```bash
python3 python/接口扫描处理工具.py
python3 python/接口扫描处理工具.py --debug
python3 python/接口扫描处理工具.py --force
```

---

## 前端页面

前端为纯静态页面，位于仓库根目录（`index.html`、`style.css`、`js/`、`config.json`），部署在静态托管平台。

### 页面结构

页面顶部提供模式切换按钮，右上角是搜索框，下方是导航标签（点播 / 直播 / 下载 / 关于），主体区根据标签展示对应面板。

**1. 点播面板**

读取 `list.txt`，按接口权重排序，每条接口展示：
- 原始线路：接口的原生 URL
- 备份一线 / 备份二线 / 备份三线：基于三个备份域名拼出的 URL

每条 URL 右侧提供「复制」按钮，一键复制到剪贴板。

**2. 直播面板**

顶部是「海量直播聚合多线路专用接口」聚合块，提供聚合接口的复制按钮。

下方读取 `livelist.txt`，每条直播源展示：
- 来源、UA 信息
- 原始线路
- 备份一线 / 二线 / 三线

**3. 采集站面板**

读取 `cjlist.txt`，展示所有提取到的采集站（标识、名称、type、api 地址）。

**4. Py 站面板**

读取 `pylist.txt`，展示所有提取到的 Py 站接口。

**5. 下载面板**

读取 `config.json` 中配置的 `downloadSources`，逐个拉取并渲染下载卡片（图标、名称、版本、跳转链接）。

**6. 关于面板**

展示备份域名、声明与联系方式（QQ 群、Telegram 群）。

### 模式切换

| 模式 | 说明 |
|------|------|
| 简洁模式（默认） | 只显示点播面板，隐藏导航标签与直播/下载/关于面板，搜索框置于顶部 |
| 全能模式 | 显示完整导航标签与所有面板，搜索框置于标签下方 |

模式选择保存在 localStorage，刷新后保持。

### 搜索功能

搜索框对所有面板生效，按接口/直播/下载卡片的 `data-name` 做模糊匹配，实时过滤。

### 复制功能

全局事件委托，所有 `.copy-btn` 点击后复制 `data-url`，优先使用 `navigator.clipboard`，降级到 `execCommand('copy')`，复制成功后按钮变绿显示「✅ 已复制」。

---

## 配置文件说明

| 文件 | 位置 | 格式 | 归属 | 说明 |
|------|------|------|------|------|
| `rylinks.txt` | 仓库根 | 文本 | 脚本A | ry 下载源 |
| `apilinks.txt` | 仓库根 | 文本 | 脚本B | 接口列表（新格式，优先） |
| `api_list.json` | 仓库根 | JSON | 脚本B | 接口列表（老格式，兜底） |
| `config.json` | 仓库根 | JSON | 前端 | 下载资源源配置 + 内容展示 |

`config.json` 示例：

```json
{
  "downloadSources": [
    {
      "name": "应用市场",
      "description": "TVBox 应用合集",
      "url": "https://cdn.jsdelivr.net/gh/SimonWang911/simonwangsub@main/appupdate/appupdate.json"
    }
  ]
}
```

> 内容展示部分已纳入 `config.json`，自由增减修改无需改动 HTML。

---

## 本地手动运行

```bash
# Step 1: 克隆并进入仓库根
git clone https://github.com/lubin776/tvbox-api-backup.git
cd tvbox-api-backup

# Step 2: 安装依赖
pip3 install -r requirements.txt

# Step 3-5: 依次运行三个脚本
python3 python/ry下载.py --debug
python3 python/api下载.py --debug
python3 python/接口扫描处理工具.py --debug

# Step 6: 验证并提交
ls ry/ tvbox/ tvbox/live/ list.txt SUMMARY.txt livelist.txt cjlist.txt pylist.txt
git add . && git commit -m "test: 本地验证" && git push
```

注意：脚本靠 CWD = 仓库根定位配置和产物，务必在仓库根执行。

---

## 部署到 Cloudflare Pages

1. 登录 Cloudflare Dashboard，进入 Pages。
2. 创建项目，关联 GitHub 仓库 `lubin776/tvbox-api-backup`。
3. 构建设置：
   - Framework preset：None
   - Build command：留空
   - Build output directory：`/`（仓库根）
4. 保存并部署，Cloudflare 会分配一个 `*.pages.dev` 域名。
5. 在 Custom domains 中绑定 `0.12yue.de5.net`、`0.cdz.qzz.io`、`0.wdzb.eu.cc` 三个域名。
6. 每次 GitHub Actions 提交产物后，Cloudflare Pages 会自动重新部署（或配置 Webhook 触发）。

---

## 常见问题与避坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 所有条目失败 | 网络不通 / UA 被封 | 加 `--debug`，检查 UA 池 |
| 套壳未展开 | 返回内容 URL > 1 条 | 正常行为，多条视为播放列表本身 |
| 文件名含特殊字符 | 净化规则未覆盖 | 检查 `sanitize_stem()` 正则 |
| livelist 旧记录丢失 | 名称匹配失败 | 旧记录按 stem 匹配，大改名会丢失 |
| Actions 页面看不到工作流 | `.yml` 不在 `.github/workflows/` | 检查路径 |
| `requests` ImportError | 依赖未装 | 检查 `pip install requests` 步骤日志 |
| 读不到新配置 | `apilinks.txt` 不在仓库根 | 确认路径与文件名拼写 |
| YAML 缩进错误 | 缩进不一致 | `uses`/`run` 与 `name` 同级对齐 |
| 并发 push 冲突 | 三工作流同时 push | 共享锁 + `git pull --rebase` |
| 产物互相覆盖 | `git add -A` 误用 | 严格按协作矩阵 `git add` |

---

## 配置修改速查

| 想做什么 | 改哪里 | 需重新部署 |
|---------|--------|-----------|
| 增删 ry 下载链接 | 编辑 `rylinks.txt` | ❌ 自动生效 |
| 改 ry 超时/UA | `python/ry下载.py` 常量区 | ✅ push 后 |
| 增删 tvbox 接口 | 编辑 `apilinks.txt` | ❌ 自动生效 |
| 调整镜像 | 在 `apilinks.txt` 对应行追加 URL | ❌ 自动生效 |
| 改调度时间 | 对应 `.yml` 的 `cron` | ✅ push 后 |
| 改产物目录 | 脚本 `OUTPUT_DIR` + 工作流 `git add` | ✅ push 后 |
| 改下载资源源 | 编辑 `config.json` | ❌ 自动生效 |
| 改前端内容展示 | 编辑 `config.json` | ❌ 无需改 HTML |
| 改前端样式 | 编辑 `index.html` / `style.css` | ✅ push 后 |

---

## 一键校验清单

```bash
# 在仓库根执行
echo "=== 1. 结构检查 ==="
ls .github/workflows/ry下载.yml .github/workflows/api下载.yml .github/workflows/接口处理扫描工具.yml
ls python/ry下载.py python/api下载.py python/接口扫描处理工具.py
ls rylinks.txt apilinks.txt config.json index.html

echo "=== 2. YAML 语法校验 ==="
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ry下载.yml')); print('ry OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/api下载.yml')); print('api OK')"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/接口处理扫描工具.yml')); print('scan OK')"

echo "=== 3. 配置校验 ==="
python3 python/api下载.py --check-config

echo "=== 4. 本地试跑 ==="
python3 python/ry下载.py
python3 python/api下载.py
python3 python/接口扫描处理工具.py

ls ry/ tvbox/ tvbox/live/ list.txt SUMMARY.txt livelist.txt cjlist.txt pylist.txt
```

全部通过 → 部署成功。

---

## 声明

本站接口资源由【误道者】整理。所有资源均来自互联网，版权归原作者所有。仅供测试学习使用，请勿用于违法及商业用途，请勿付费购买。如涉及侵权，请联系删除。

- QQ 交流群：1067685939
- Telegram 群组：https://t.me/tg6603

---

🌐 **项目访问地址**：https://lubin776.github.io/tvbox-api-backup/
📦 **仓库地址**：https://github.com/lubin776/tvbox-api-backup


## 更新日志

> 📅 最后更新：2025年9月30日（v2 最新模板）

---

### ✨ 新增

| 项目 | 说明 |
|------|------|
| **新配置格式 `apilinks.txt`** | 仓库根目录，每行 `名称,URL1,URL2,...`，多 URL 逗号分隔，`#` 开头为注释。脚本B优先读取此格式。 |
| **脚本C 升级为「接口全量扫描处理工具」** | 覆盖三大模块：① 直播源下载（lives 扫描 + 套壳穿透）② 采集站提取（sites type 0/1 → `cjlist.txt` + `海量采集聚合接口.json`）③ Py 站提取（.py 接口 → `pylist.txt` + `海量py聚合接口.json`） |
| **备份域名列表** | 新增三个互为备份的访问域名，任一可用。 |
| **GitHub Pages 主站** | 项目访问地址：https://lubin776.github.io/tvbox-api-backup/ |

---

### 🔄 更改

| **前端 `config.json` 载入逻辑** | 内容展示部分完全纳入 config，自由增减修改无需改动 HTML |
| **缓存刷新逻辑** | 已更新，确保前端内容实时同步 |
| **脚本与文件名对齐真实仓库** | `ry下载.py` / `api下载.py` / `接口扫描处理工具.py`（修正旧文档中的 `rydownload`/`apidownload`/`livedownload` 等错误命名） |


---

### 📢 其他

| 项目 | 说明 |
|------|------|
| Telegram 链接 | 已更新为 https://t.me/tg6603 |
| 项目定位 | 更新为「TVBox 接口与直播源自动备份项目」，明确备份、聚合、自动化、防冲突四大核心 |
| 文档版本 | 升级至 v2（9月30日最新模板），重写项目说明与 README，消除旧版混乱格式 |
