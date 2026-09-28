"""电力行业资讯分类器

算法：主题加权评分优先 + 公文体裁兜底
- 强特征词(weight=2)：标题命中×3 + 正文前800字命中×1
- 泛词(weight=1)：仅计标题命中×2（避免正文噪声带偏）
- 得分最高的主题分类达到阈值(5)则胜出
- 若无主题达标，但标题为公文体裁（通知/意见/办法/批复…）→ 政策法规与规划
- 否则 → 其他
- 预处理将“电信网”中的干扰剔除，避免误命中“电网”
"""
import re

_OTHER = "其他"
_POLICY = "政策法规与规划"

# 主题分类 -> {关键词: 权重}（强特征词 2 分，泛词 1 分）
# 注意：字典顺序是同分时的最终裁决序，越具体的分类越靠前
TOPIC_CATEGORIES = {
    "电价与市场交易": {
        "电价": 2, "电力市场": 2, "电力交易": 2, "电力现货": 2, "现货市场": 2,
        "中长期交易": 2, "绿电交易": 2, "代理购电": 2, "输配电价": 2,
        "峰谷": 2, "分时电价": 2, "容量电价": 2, "上网电价": 2, "辅助服务": 2,
        "售电": 2, "竞价": 1, "交易中心": 1, "市场交易": 1,
    },
    "碳市场与绿色低碳": {
        "碳排放": 2, "碳交易": 2, "碳市场": 2, "绿证": 3, "双碳": 2,
        "碳达峰": 2, "碳中和": 2, "CCER": 2, "排污权": 2, "碳普惠": 2,
        "绿色低碳": 2, "绿色电力证书": 3, "非化石能源": 2, "降碳": 2,
        "低碳": 2, "减排": 1, "节能": 1, "能耗": 1,
    },
    "新能源与储能": {
        "新能源": 2, "风电": 2, "光伏": 2, "太阳能": 2, "储能": 2,
        "氢能": 2, "生物质": 2, "海上风电": 2, "抽水蓄能": 2, "新型储能": 2,
        "可再生能源": 2, "沙戈荒": 2, "源网荷储": 2, "绿电": 2, "地热": 2,
        "动力电池": 2, "分布式": 1, "装机": 1, "并网": 1, "消纳": 1,
    },
    "安全监管": {
        "安全生产": 2, "安全事故": 2, "电力安全": 2, "隐患": 2, "应急演练": 2,
        "应急预案": 2, "防汛": 2, "防灾": 2, "网络安全": 2, "安全检查": 2,
        "安全监管": 2, "事故通报": 2, "电力设施保护": 2, "安全": 2, "大坝": 2,
        "可靠性": 1, "应急": 1, "事故": 1, "督查": 1, "风险防": 1,
    },
    "电网建设与运行": {
        "特高压": 2, "输变电": 2, "配电网": 2, "电网工程": 2, "电网建设": 2,
        "输电": 2, "变电": 2, "电力调度": 2, "用电量": 2, "供电可靠": 2,
        "电网": 1, "供电": 1, "负荷": 1, "线路": 1, "用电": 1, "调度": 1,
    },
    "煤电与电力保供": {
        "煤电": 2, "火电": 2, "煤炭": 2, "保供": 2, "气电": 2, "核电": 2,
        "水电": 2, "热电": 2, "迎峰度夏": 2, "迎峰度冬": 2, "电力保供": 2,
        "天然气": 2, "机组": 1, "电厂": 1, "发电": 1,
    },
    "产业与数字化": {
        "数字化": 2, "人工智能": 2, "算力": 2, "虚拟电厂": 2, "车网互动": 2,
        "充电设施": 2, "充电基础": 2, "充电桩": 2, "智能电网": 2, "智能制造": 2,
        "充电": 1, "智能": 1, "装备": 1, "产业链": 1, "工业互联网": 1,
    },
}

# 公文体裁：标题出现这些词视为政策文件
POLICY_DOC_RE = re.compile(
    r"(通知|意见|办法|方案|规划|批复|公告|公示|函|令|规定|细则|指引|"
    r"指南|征求意见|决定|决议|条例|标准|目录|行动计划|实施方案|印发)"
)

SCORE_THRESHOLD = 5  # 主题分类达标分（强特征词命中标题一次即 2×3=6 分）

# 电力行业相关性过滤关键词（用于非电力专门网站，如市政府、工信部）
# 标题命中即相关；否则正文前500字需命中≥2次（防止“安全”等泛词误纳入）
POWER_KEYWORDS = {
    "电力", "能源", "电网", "发电", "供电", "用电", "电价", "风电",
    "光伏", "储能", "核电", "水电", "煤电", "碳排放", "碳交易", "碳市场",
    "绿证", "充电", "千瓦时", "低碳", "降碳", "新能源", "绿电",
    "特高压", "虚拟电厂", "输电",
}


def _normalize(s: str) -> str:
    """剔除易误命中的干扰子串：如“电信网”中的“电网”"""
    return (s or "").replace("电信网", "电信")


def is_power_related(title: str, text: str = "") -> bool:
    title = _normalize(title)
    if any(k in title for k in POWER_KEYWORDS):
        return True
    body = _normalize((text or "")[:500])
    return sum(body.count(k) for k in POWER_KEYWORDS) >= 2


def classify(title: str, text: str = "") -> str:
    title = _normalize(title)
    body = _normalize((text or "")[:800])
    best_key, best_cat, best_score = None, None, 0
    for order, (cat, kws) in enumerate(TOPIC_CATEGORIES.items()):
        score, title_pts = 0, 0
        # 长词优先匹配并移除，避免子串重复计数（如“绿色低碳”与“低碳”）
        t_work, b_work = title, body
        for k in sorted(kws, key=len, reverse=True):
            tc = t_work.count(k)
            bc = min(b_work.count(k), 2)  # 正文命中封顶2次，防导航/套话噪声
            if tc or bc:
                t_work = t_work.replace(k, " " * len(k))
                b_work = b_work.replace(k, " " * len(k))
            w = kws[k]
            if w >= 2:  # 强特征词：标题×3 + 正文×1
                score += w * (3 * tc + bc)
                title_pts += w * 3 * tc
            else:       # 泛词：仅标题×2
                score += w * 2 * tc
                title_pts += w * 2 * tc
        # 排序键：总分 > 标题得分 > 定义顺序靠前
        key = (score, title_pts, -order)
        if best_key is None or key > best_key:
            best_key, best_cat, best_score = key, cat, score
    if best_cat and best_score >= SCORE_THRESHOLD:
        return best_cat
    if POLICY_DOC_RE.search(title):
        return _POLICY
    return _OTHER


def make_summary(text: str, limit: int = 160) -> str:
    """从正文生成摘要：取前若干句"""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 尽量在句号处截断
    pos = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("！"))
    if pos > limit // 2:
        cut = cut[: pos + 1]
    return cut + "…"
