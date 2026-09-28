# 电力行业重点资讯聚合平台

定期抓取 12 家政府/监管机构网站的电力行业重点资讯，自动分类并生成摘要，提供网页浏览。

线上版本托管于 Cloudflare Pages：**https://news.washi.lol**（GitHub Actions 每天定时抓取并导出静态页面，见下文「部署架构」）。

## 功能

- **多源抓取**：国家发改委、国家能源局（含电力司/新能源司/安全司）、华东能监局、上海市政府/发改委/经信委/住建委、上海环交所、工信部
  （注：浦东新区信息公开目录页为纯导航页无文章链接，已从抓取源中移除）
- **智能分类**：LLM 语义分类（默认 glm-5.3-flash，失败自动回退关键词加权算法）；分为 电价与市场交易 / 新能源与储能 / 安全监管 / 电网建设与运行 / 煤电与电力保供 / 碳市场与绿色低碳 / 产业与数字化 / 政策法规与规划 / 其他
- **相关性过滤**：非电力专门网站（如市政府）只保留电力相关资讯
- **自动摘要 + 详情查看**：列表页看摘要，点击卡片看全文与原文链接
- **指导价值标识**：LLM 以企业画像（电力行业软硬一体化方案商、软件为主）评估每条资讯的指导价值（高/中/低+理由），高价值文章带 ⭐ 标识，支持一键过滤
- **附件下载**：自动提取政策文件的 PDF/OFD/Word 等附件链接，详情页仿政府网站样式展示，点击新窗口打开
- **富文本正文**：保留原文的章节标题、加粗、表格、列表等格式（服务端白名单净化防 XSS），段落首行缩进两字符
- **月历浏览**：左侧日历标注有记录的日期，点击即可查看当天资讯
- **定时抓取**：GitHub Actions 每天北京时间 07:00 自动抓取并发布静态站点

## 部署架构（Cloudflare Pages）

线上站点为**纯静态页面**，不依赖 Flask 服务：

```
GitHub Actions（每天 07:00 北京时间 / 可手动触发）
  → python crawler.py      抓取 + LLM 分类 + 摘要，写入 data/power_news.db（随仓库提交，增量累积）
  → python export_static.py  导出静态站点到 public/
  → git commit & push       触发 Cloudflare Pages 重新部署
```

- Pages 项目设置：构建命令留空，输出目录 `public`
- 自定义域名：`news.washi.lol`（与博客 `washi.lol` 同域不同子域）
- LLM 分类 Key 存于仓库 Secret `LLM_API_KEY`；未配置时自动回退关键词分类
- 手动触发抓取：GitHub 仓库 → Actions →「定时抓取并发布」→ Run workflow

## 本地预览（静态模式）

```bash
source .venv/bin/activate
python export_static.py        # 生成 public/
cd public && python3 -m http.server 12344
# 打开 http://localhost:12344
```

## 本地动态服务（Flask，可选）

保留原有用法，适合本地调试爬虫 / 手动触发抓取：

```bash
python app.py    # http://localhost:12333
```

Flask 模式下页面同样读取 `public/data/` 的静态导出（需先运行一次 `export_static.py`）。

## 本地开发环境准备

```bash
cd /Users/stan/program/Code/news4morningstar

# 首次运行才需要：创建虚拟环境 + 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 常见命令

```bash
# 查看运行日志
tail -f data/server.log

# 停止 Flask 服务
lsof -ti:12333 | xargs kill
```

## 项目结构

```
news4morningstar/
├── app.py            # Flask 服务（本地调试：API + 手动触发抓取 + 静态预览）
├── crawler.py        # 爬虫引擎（站点配置、列表/详情解析、抓取状态）
├── classifier.py     # 关键词分类器 + 摘要生成
├── export_static.py  # 导出静态站点到 public/（Pages 部署产物）
├── db.py             # SQLite 存储
├── static/           # 前端页面（原生 JS，纯静态数据驱动）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── public/           # 静态导出产物（export_static.py 生成，Pages 托管）
├── .github/workflows/crawl.yml  # 定时抓取 + 提交
└── data/             # SQLite 数据库（随仓库提交，增量累积）
```

## API（仅本地 Flask 模式）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/articles?category=&source=&keyword=&date_from=&date_to=&page= | 资讯列表 |
| GET | /api/articles/<id> | 资讯详情 |
| GET | /api/day_counts?month=YYYY-MM | 某月每天有记录的条数（侧栏日历用） |
| GET | /api/stats | 分类/来源统计、上次抓取信息 |
| POST | /api/refresh | 触发后台抓取 |
| GET | /api/refresh/status | 抓取进度轮询 |

## 说明

- 分类默认为 LLM 语义分类（`llm_classifier.py`），接口失败时自动回退到关键词加权算法（`classifier.py`）。
- 部分政府网站（如上海市政府首页）使用 JS 动态渲染，通用解析可能抓不到内容，可在 `crawler.py` 的 `SITES` 中为其替换为静态列表页 URL。
- 每个站点每次最多抓取 25 条最新链接，可在 `MAX_PER_SITE` 调整。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `12333` | 服务端口（仅本地 Flask 模式） |
| `HOST` | `127.0.0.1` | 监听地址（仅本地 Flask 模式） |
| `CRAWL_HOUR` | `7` | 本地 Flask 模式每天定时抓取的小时（线上由 Actions cron 控制） |
| `CLASSIFY_MODE` | `llm` | `llm`=LLM 优先、失败回退关键词；`keyword`=纯关键词 |
| `LLM_BASE_URL` | `https://api.cloudwise.ai/api/v1` | OpenAI 兼容接口地址 |
| `LLM_API_KEY` | 读 `~/.pi/agent/auth.json` | 分类用模型 API Key |
| `LLM_MODEL` | `glm-5.3-flash` | 分类用模型 |

## 重新分类存量数据

```bash
python reclassify.py            # LLM 重分类
python reclassify.py --keyword  # 关键词重分类
python analyze_guidance.py            # LLM 评估指导价值（全部）
python analyze_guidance.py --missing  # 只处理未评估的
python backfill_attachments.py            # 回填附件链接（全部）
python backfill_attachments.py --missing  # 只处理未回填的
python backfill_content.py            # 重建正文文本+富文本HTML+附件（全部）
python backfill_content.py --missing  # 只处理缺富文本的
```

企业画像可在环境变量 `COMPANY_PROFILE` 中自定义（用于指导价值评估）。
