"""爬虫引擎：抓取政府网站列表页 -> 详情页 -> 分类入库"""
import json
import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import db
from classifier import is_power_related, make_summary
from llm_classifier import analyze_guidance, classify_smart as classify

log = logging.getLogger("crawler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 20

# 站点配置：
#   urls: 列表页；power_site: True 表示电力专门网站（不做相关性过滤）
SITES = [
    {
        "name": "国家发改委",
        "urls": [
            "https://www.ndrc.gov.cn/xxgk/zcfb/tz/",      # 通知
            "https://www.ndrc.gov.cn/xxgk/zcfb/ghxwj/",   # 规划与规范性文件
            "https://www.ndrc.gov.cn/xwdt/xwfb/",         # 新闻发布
        ],
        "power_site": False,
    },
    {
        "name": "国家能源局",
        "urls": [
            "https://www.nea.gov.cn/",                    # 首页要闻
            "https://www.nea.gov.cn/xwzx/nyyw.htm",       # 能源要闻（JSON 数据源）
        ],
        "power_site": True,
    },
    {
        "name": "国家能源局电力司",
        "urls": ["http://www.nea.gov.cn/sjzz/dls/index.htm"],
        "power_site": True,
    },
    {
        "name": "国家能源局新能源司",
        "urls": ["http://www.nea.gov.cn/sjzz/xny/index.htm"],
        "power_site": True,
    },
    {
        "name": "国家能源局安全司",
        "urls": ["http://www.nea.gov.cn/sjzz/aqs/index.htm"],
        "power_site": True,
    },
    {
        "name": "华东能监局",
        "urls": ["http://hdj.nea.gov.cn/"],
        "power_site": True,
    },
    {
        "name": "上海市政府",
        "urls": ["https://www.shanghai.gov.cn/nw2407/index.html"],
        "power_site": False,
    },
    {
        "name": "上海市发改委",
        "urls": ["https://fgw.sh.gov.cn/fgw_zwgk/index.html"],
        "power_site": False,
    },
    {
        "name": "上海市经信委",
        "urls": ["https://www.sheitc.sh.gov.cn/"],
        "power_site": False,
    },
    {
        "name": "上海市住建委",
        "urls": ["https://zjw.sh.gov.cn/zwgk/index.html"],
        "power_site": False,
    },
    {
        "name": "上海环交所",
        "urls": [
            "https://www.cneeex.com/",
            "https://www.cneeex.com/ljsdt/",   # 交易集团动态
        ],
        "power_site": False,
    },
    {
        "name": "工业和信息化部",
        "urls": ["https://www.miit.gov.cn/zwgk/index.html"],
        "power_site": False,
    },
]

DATE_RE = re.compile(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})")
MIN_TITLE_LEN = 8
MAX_PER_SITE = 25

# 状态（内存）——供前端轮询
_status = {"running": False, "progress": "", "done": False, "result": None}
_status_lock = threading.Lock()
_crawl_lock = threading.Lock()  # 保证同一时刻只有一个抓取任务


def get_status():
    with _status_lock:
        return dict(_status)


def _set_status(**kw):
    with _status_lock:
        _status.update(kw)


def _looks_mojibake(text):
    """UTF-8 被误读为 latin-1 的特征：大量 Latin-1 增补区字符"""
    if not text:
        return False
    sample = text[:2000]
    return sum(1 for c in sample if "" <= c <= "ÿ") >= 5


def _decode_response(r):
    """正确解码响应：未声明/ISO-8859-1 时优先尝试 UTF-8 严格解码，再退回 apparent_encoding"""
    enc = (r.encoding or "").lower()
    if enc in ("", "iso-8859-1"):
        try:
            return r.content.decode("utf-8")
        except UnicodeDecodeError:
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
    text = r.text
    # 声明了编码但仍是乱码（如 header 写了 ISO-8859-1 实为 UTF-8）
    if _looks_mojibake(text):
        try:
            return r.content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return text


def _fetch(url, retries=2):
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
            if r.status_code == 200:
                return _decode_response(r)
            # 限流/服务端错误重试；其余状态（如 404）直接放弃
            if r.status_code not in (429, 500, 502, 503, 504):
                log.warning("fetch %s -> HTTP %s", url, r.status_code)
                return None
        except Exception as e:
            if attempt == retries:
                log.warning("fetch fail %s: %s", url, e)
        time.sleep(1)
    return None


def _norm_date(m):
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


# 标题尾部的拼接物：省略号+日期 / 纯日期（列表页 CSS 截断+日期粘连）
TITLE_TAIL_RE = re.compile(
    r"(\s*[.…]{2,3}\s*\d{2,4}[-/]\d{1,2}([-/]\d{1,2})?$|\s*20\d{2}[-/]\d{1,2}[-/]\d{1,2}$"
    r"|\s*\d{2}-\d{2}$|\s*[.…]{2,3}$)")


def _clean_title(t):
    t = re.sub(r"\s+", " ", t or "").strip()
    return TITLE_TAIL_RE.sub("", t).strip()


def _title_truncated(t):
    """标题疑似被列表页截断"""
    return "..." in (t or "") or "…" in (t or "")


# URL 中的日期：/20260923/ 或 /2026-09/23/ 或 /2026/09/23/
URL_DATE_RES = [
    re.compile(r"/(20\d{2})(\d{2})(\d{2})/"),
    re.compile(r"/(20\d{2})-(\d{2})-(\d{2})/"),
    re.compile(r"/(20\d{2})-(\d{2})/(\d{2})/"),
    re.compile(r"/(20\d{2})/(\d{2})/(\d{2})/"),
]


def _date_from_url(url):
    for rx in URL_DATE_RES:
        m = rx.search(url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def extract_links(html, base_url):
    """通用列表页链接提取：取含日期的文章链接或新闻特征链接"""
    soup = BeautifulSoup(html, "lxml")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        title = _clean_title(a.get("title") or a.get_text())
        href = a["href"].strip()
        if len(title) < MIN_TITLE_LEN:
            continue
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        url = urljoin(base_url, href)
        if not url.startswith("http") or url in seen:
            continue
        # 必须是“文章型”链接（路径含日期段，排除栏目导航页）
        if not _date_from_url(url):
            if not re.search(r"(/t\d{8}_\d+\.html|[?&]id=\d+|art/\d+)", url):
                continue
            if not re.search(r"\.s?html?(\?.*)?$", url):
                continue
        # 在附近文本里找日期，找不到则看 URL
        context = a.parent.get_text(" ", strip=True) if a.parent else title
        if a.parent and a.parent.parent:
            context += " " + a.parent.parent.get_text(" ", strip=True)
        date = _norm_date(DATE_RE.search(context)) or _date_from_url(url)
        seen.add(url)
        items.append({"url": url, "title": title, "publish_date": date})
    # 去重后按“有日期优先”排序
    items.sort(key=lambda x: (x["publish_date"] or ""), reverse=True)
    return items


def extract_nea_json(html, base_url):
    """新华社云平台(国家能源局新版)列表：页面中的 data=\"datasource:<id>\" -> ds_<id>.json"""
    items, seen = [], set()
    for ds_id in re.findall(r'data="datasource:([0-9a-f]{16,})"', html):
        js_url = urljoin(base_url, f"ds_{ds_id}.json")
        try:
            r = requests.get(js_url, headers=HEADERS, timeout=TIMEOUT, verify=False)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception as e:
            log.warning("nea json fail %s: %s", js_url, e)
            continue
        rows = data.get("datasource") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for it in rows:
            if it.get("contentType") == "Link":
                continue
            url = urljoin(base_url, it.get("publishUrl") or "")
            title = _clean_title(it.get("showTitle") or it.get("title"))
            if not url.startswith("http") or len(title) < MIN_TITLE_LEN or url in seen:
                continue
            date = (it.get("publishTime") or "")[:10] or _date_from_url(url)
            summary = (it.get("summary") or "").strip().strip('"') or None
            seen.add(url)
            items.append({"url": url, "title": title, "publish_date": date,
                          "summary": summary})
    items.sort(key=lambda x: (x["publish_date"] or ""), reverse=True)
    return items


CONTENT_SELECTORS = [
    ".TRS_Editor", "#zoom", "#zoomcon", ".zoomcon", "#ivs_content",
    ".article-content", ".article_content", ".article", ".content_detail",
    ".detail_content", ".conTxt", "#content", ".content", ".text", ".news_content",
]
NOISE_TAGS = ["script", "style", "iframe", "form", "nav", "header", "footer"]
# 行首命中即丢弃（页面按钮/导航/页脚等功能性文字）
NOISE_LINE_RE = re.compile(
    r"^(首页|设为首页|加入收藏|收藏本站|工作邮箱|机构概览|动态要闻|信息公开|在线办事|"
    r"互动回应|专题专栏|字号|字体|大 中 小|小 中 大|大中小|来源[:：]|发布日期[:：]|"
    r"发布时间[:：]|分享到|打印|纠错|关闭|扫一扫|微博|微信|返回顶部|无障碍|长者版|"
    r"繁體|English|简体|登录|注册|网站地图|联系我们|办公地址|地址[:：]|邮编[:：]|"
    r"总机|传真|主办单位|承办单位|技术支持|"
    r"网站标识码|转载请注明|上一篇|下一篇|相关阅读|相关新闻|相关信息|相关政策与解读|手机版|客户端|公众号|"
    r"目录项|索引号|制发日期|成文日期|发文机关|发文字号|主题分类|公开事项|公文名称|"
    r"有效性[:：]|标 题|主题词)"
)
# 行内包含即丢弃（备案/版权等页脚碎片，可能挂在单位名后面）
NOISE_SUBSTR_RE = re.compile(r"(ICP备|公网安备|网站标识码|版权所有|转载请注明)")
# 附件清单行：「附件：」「附表：」「附件：1.xxx」或以文件扩展名结尾的文件名行
ATTACH_LINE_RE = re.compile(
    r"^((附件|附表|附录)\s*[:：]|(\d+[.、．]\s*)?\S.*\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|wps|et|ofd|txt|csv)$)",
    re.I)
# 纯日期/时间行（正文里的发布元信息）
DATE_ONLY_RE = re.compile(
    r"^20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}日?(\s+\d{1,2}:\d{2}(:\d{2})?)?$")
# 索引号/发文字号等纯编号行
INDEX_NO_RE = re.compile(r"^[\dA-Za-z]\d{4,}/\d{4}-\d{3,}$")


def _clean_lines(text, title="", attach_names=()):
    lines = []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln or NOISE_LINE_RE.search(ln) or NOISE_SUBSTR_RE.search(ln) \
                or DATE_ONLY_RE.match(ln) or INDEX_NO_RE.match(ln) \
                or ATTACH_LINE_RE.match(ln):
            continue
        # 丢弃无标点的短行（多为导航菜单/栏目标题）
        if len(ln) <= 6 and not re.search(r"[。！？；：]", ln):
            continue
        # 丢弃与标题重复的行（正文区常包含一遍 h1 标题；标题可能带日期后缀）
        if title and (ln in title or title in ln):
            continue
        # 丢弃附件清单残留行（无扩展名的附件名行，如“2.第四监管周期区域电网输电价格表”）
        bare = ln.lstrip("0123456789.、．　 ").strip()
        if any(bare == n or ln == n for n in attach_names if n):
            continue
        lines.append(ln)
    return "\n".join(lines)


FILE_EXT_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|wps|et|csv|ofd|txt)(\?.*)?$", re.I)


def extract_attachments(soup, base_url, node=None):
    """提取附件链接：正文区/全文中的文件链接（pdf/doc/xls/zip 等）"""
    atts, seen = [], set()
    scope = [node] if node else []
    scope.append(soup)  # 正文没扫到再看全页（有些站附件在正文节点外）
    for sc in scope:
        if sc is None:
            continue
        for a in sc.find_all("a", href=True):
            href = a["href"].strip()
            name = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if not FILE_EXT_RE.search(href):
                continue
            url = urljoin(base_url, href)
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            # 清理名称：去掉“下载”“点击下载”等多余词
            name = re.sub(r"^(下载|点击下载|查看)[:：]?\s*", "", name) or url.split("/")[-1]
            atts.append({"name": name[:80], "url": url})
        if atts:
            break
    return atts[:20]


# 正文 HTML 白名单：保留结构/强调标签，其余拆壳
ALLOWED_HTML_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "strong", "b", "em", "br",
                     "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th",
                     "blockquote", "hr"}


def _build_content_html(node, title="", attach_names=()):
    """把正文节点转为净化 HTML：保留标题/加粗/列表/表格，剔除噪音块与所有属性"""
    if node is None:
        return None
    work = BeautifulSoup(str(node), "lxml")
    root = work.body or work
    # div/section 当作段落，span/font/a 等拆壳只留文本
    for t in root.find_all(["div", "section", "article"]):
        t.name = "p"
    for t in root.find_all(True):
        if t.name not in ALLOWED_HTML_TAGS:
            t.unwrap()
        else:
            t.attrs = {}
    # 剔除噪音块/空块/标题重复块/附件清单行（只处理“叶子块”，容器块留给子块处理）
    BLOCKS = ["p", "h1", "h2", "h3", "h4", "h5", "li", "td", "th"]
    for t in list(root.find_all(BLOCKS)):
        if t.find(BLOCKS):
            continue  # 容器块：跳过，避免误杀整个正文
        txt = re.sub(r"\s+", " ", t.get_text(" ", strip=True))
        bare = txt.lstrip("0123456789.、．　 ").strip()
        if (not txt
                or NOISE_LINE_RE.search(txt) or NOISE_SUBSTR_RE.search(txt)
                or DATE_ONLY_RE.match(txt) or INDEX_NO_RE.match(txt)
                or ATTACH_LINE_RE.match(txt)
                or (title and (txt == title or (len(txt) > 8 and txt in title)))
                or any(bare == n or txt == n for n in attach_names if n)):
            t.decompose()
    # 剔除正文开头处的“信息公开元数据表”行（首个实质段落之前、单元格合计≤20字的 tr）
    seen_content = False
    for t in root.find_all(BLOCKS + ["tr"]):
        if t.name != "tr" and not t.find(BLOCKS):
            if len(re.sub(r"\s+", "", t.get_text())) >= 30:
                seen_content = True
            continue
        if t.name == "tr" and not seen_content and not t.find("tr"):
            cells = re.sub(r"\s+", "", t.get_text())
            if len(cells) <= 20 or NOISE_LINE_RE.search(cells):
                t.decompose()
    # 清理被清空的容器（如整个 td 被删后的 table）
    for _ in range(3):
        for t in root.find_all(["table", "p", "ul", "ol", "blockquote", "tr"]):
            if not t.get_text(strip=True):
                t.decompose()
    html = root.decode_contents() if hasattr(root, "decode_contents") else str(root)
    html = re.sub(r"(<p>\s*(<br\s*/?>)?\s*</p>)+", "", html)
    return html.strip() or None


def extract_detail(html, url):
    """从详情页提取标题、日期、正文、附件、正文HTML"""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(NOISE_TAGS):
        t.decompose()
    # 常见功能组件（分享/字号/打印工具条等）直接移除
    for sel in ["[class*=share]", "[class*=Share]", "[class*=print]", "[class*=font]",
                "[class*=tool]", "[class*=zoom-btn]", "[id*=share]", "[id*=print]",
                "button", "select"]:
        for t in soup.select(sel):
            t.decompose()
    title = ""
    h1 = soup.find(["h1", "h2"])
    if h1:
        title = h1.get_text(strip=True)
    if not title and soup.title:
        title = re.split(r"[-_|]", soup.title.get_text())[0].strip()

    date = None
    m = DATE_RE.search(soup.get_text(" ", strip=True)[:2000])
    if m:
        date = _norm_date(m)

    node, best_len = None, 0
    for sel in CONTENT_SELECTORS:
        n = soup.select_one(sel)
        if n:
            ln = len(n.get_text(strip=True))
            if ln > best_len:
                node, best_len = n, ln
    if node is None:
        # fallback: 文本最多的 div
        for n in soup.find_all("div"):
            ln = len(n.get_text(strip=True))
            if ln > best_len:
                node, best_len = n, ln
    attachments = extract_attachments(soup, url, node)
    attach_names = [a["name"] for a in attachments]
    text = ""
    if node:
        text = _clean_lines(node.get_text("\n", strip=True), title=title,
                            attach_names=attach_names)
        text = re.sub(r"\n{3,}", "\n\n", text)
    content_html = _build_content_html(node, title=title, attach_names=attach_names)
    return title, date, text[:50000], attachments, content_html


def _process_article(item, site):
    html = _fetch(item["url"])
    if not html:
        return None
    title, date, text, attachments, content_html = extract_detail(html, item["url"])
    # 列表标题被截断时优先用详情页 h1 标题
    if _title_truncated(item["title"]) and title:
        item["title"] = title
    title = _clean_title(item["title"]) or _clean_title(title)
    if not title:
        return None
    publish_date = item["publish_date"] or date or _date_from_url(item["url"])
    if not site["power_site"] and not is_power_related(title, text):
        return None
    category = classify(title, text)
    g_level, g_reason = analyze_guidance(title, text)
    return {
        "url": item["url"],
        "title": title,
        "source": site["name"],
        "category": category,
        "publish_date": publish_date,
        "summary": item.get("summary") or make_summary(text),
        "content": text,
        "guidance_level": g_level,
        "guidance_reason": g_reason,
        "attachments": json.dumps(attachments, ensure_ascii=False) if attachments else None,
        "content_html": content_html,
    }


def crawl_site(site):
    """抓取单个站点，返回 (新增数, 发现数)"""
    links, seen = [], set()
    for lu in site["urls"]:
        html = _fetch(lu)
        log.info("%s list %s -> %s", site["name"], lu,
                 "FAIL" if not html else f"{len(html)}B")
        if not html:
            continue
        n_json = extract_nea_json(html, lu)
        n_html = extract_links(html, lu)
        log.info("%s parsed: json=%d html=%d", site["name"], len(n_json), len(n_html))
        for it in n_json + n_html:
            if it["url"] not in seen:
                seen.add(it["url"])
                links.append(it)
    links = links[:MAX_PER_SITE]
    new_count = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_process_article, it, site): it for it in links}
        for fut in as_completed(futs):
            try:
                art = fut.result()
                if art and db.insert_article(art):
                    new_count += 1
            except Exception as e:
                log.warning("process fail: %s", e)
    return new_count, len(links)


def run_crawl(trigger="manual"):
    """执行一次完整抓取（可在后台线程/调度器中调用）"""
    if not _crawl_lock.acquire(blocking=False):
        return {"ok": False, "message": "已有抓取任务进行中"}
    log_id = db.log_start()
    _set_status(running=True, done=False, progress="开始抓取…", result=None)
    total_new, total_found, errors = 0, 0, []
    try:
        for i, site in enumerate(SITES, 1):
            _set_status(progress=f"({i}/{len(SITES)}) 正在抓取：{site['name']}")
            try:
                new, found = crawl_site(site)
                total_new += new
                total_found += found
                log.info("%s: %d new / %d found", site["name"], new, found)
            except Exception as e:
                errors.append(f"{site['name']}: {e}")
                log.exception("site fail %s", site["name"])
            time.sleep(0.5)  # 礼貌间隔
        status = "success" if not errors else "partial"
        msg = f"新增 {total_new} 条，共发现 {total_found} 条"
        if errors:
            msg += "；部分站点失败：" + "；".join(errors)
        db.log_finish(log_id, status, total_new, total_found, msg)
        result = {"ok": True, "new": total_new, "found": total_found,
                  "errors": errors, "message": msg}
        _set_status(running=False, done=True, progress="完成", result=result)
        _crawl_lock.release()
        return result
    except Exception as e:
        db.log_finish(log_id, "failed", total_new, total_found, str(e))
        _set_status(running=False, done=True, progress="失败",
                    result={"ok": False, "message": str(e)})
        _crawl_lock.release()
        return {"ok": False, "message": str(e)}


def run_crawl_async(trigger="manual"):
    th = threading.Thread(target=run_crawl, args=(trigger,), daemon=True)
    th.start()
    return th
