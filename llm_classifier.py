"""LLM 分类器：调用 OpenAI 兼容接口进行语义分类，失败时回退关键词分类

配置（环境变量）：
  LLM_BASE_URL   OpenAI 兼容接口地址，默认 https://api.cloudwise.ai/api/v1
  LLM_API_KEY    API Key，默认读取 ~/.pi/agent/auth.json 的 cloudwise-z
  LLM_MODEL      模型，默认 glm-5.3-flash
  CLASSIFY_MODE  llm(默认，LLM优先、失败回退关键词) / keyword(纯关键词)
"""
import json
import logging
import os
import re

import requests

from classifier import classify as keyword_classify

log = logging.getLogger("llm_classifier")

CATEGORIES = [
    "电价与市场交易",
    "碳市场与绿色低碳",
    "新能源与储能",
    "安全监管",
    "电网建设与运行",
    "煤电与电力保供",
    "产业与数字化",
    "政策法规与规划",
    "其他",
]

_SYSTEM = """你是电力行业资讯分类助手。从以下分类中为新闻选择最合适的一个：

- 电价与市场交易：电价政策与改革、电力市场建设、现货/中长期/绿电交易、辅助服务、售电
- 碳市场与绿色低碳：碳排放权交易、绿证、双碳目标、节能降碳、绿色低碳转型、排污权
- 新能源与储能：风电、光伏、储能、氢能、可再生能源装机/并网/消纳、地热、动力电池
- 安全监管：电力安全生产、事故通报、隐患排查、应急演练、防汛防灾、网络与信息安全、大坝安全
- 电网建设与运行：电网工程、特高压、输变电、配电网、电力调度、供电服务、用电负荷
- 煤电与电力保供：煤电、火电、核电、水电、气电、煤炭与天然气供应、迎峰度夏/度冬电力保供
- 产业与数字化：能源数字化、人工智能应用、充换电基础设施、虚拟电厂、智能电网、装备制造
- 政策法规与规划：以通知/意见/办法/规划/批复等公文形式发布、且不属于上述具体主题的综合政策文件
- 其他：会议活动、人事、党建、调研座谈、外事会见等与上述主题均不明显相关的内容

规则：只输出分类名称本身，不要输出理由、标点或任何其他内容。"""

# 企业画像：用于评估政策对公司的指导价值
COMPANY_PROFILE = os.environ.get(
    "COMPANY_PROFILE",
    "电力行业软硬一体化解决方案提供商，以软件为主，硬件与外部厂家合作")

_GUIDANCE_SYSTEM = f"""你是企业战略分析助手。企业背景：{COMPANY_PROFILE}。

请评估以下资讯/政策对该企业未来发展的指导价值。评级必须严格克制：

- 高（少数，一般不超过 15%）：满足以下任一条
  ① 新市场规则/强制建设要求，直接创造公司可参与的市场（如电力交易、调度、安全监测、碳管理软件系统的制度性需求）
  ② 含补贴、专项资金、试点示范申报等公司可行动的机会
  ③ 强制性合规要求/标准制修订，影响公司产品路线
- 中：行业趋势、数据发布、会议部署、领导调研等，有参考价值但无直接行动点
- 低：与公司业务基本无关（人事、党建、外事会见、无关领域）

输出格式（严格遵守两行）：
第一行：只输出 高 / 中 / 低 一个字
第二行：一句话理由（不超过 50 字，不要换行）"""

DEFAULT_BASE_URL = "https://api.cloudwise.ai/api/v1"
DEFAULT_MODEL = "glm-5.3-flash"
_cache = {}
_consecutive_failures = 0
_CIRCUIT_BREAKER_LIMIT = 5  # 连续失败5次后熔断，本次进程内直接用关键词回退


def _load_key():
    key = os.environ.get("LLM_API_KEY")
    if key:
        return key
    try:
        auth = json.load(open(os.path.expanduser("~/.pi/agent/auth.json")))
        return auth["cloudwise-z"]["key"]
    except Exception:
        return None


def classify_llm(title: str, text: str = "", retries: int = 2):
    """调用 LLM 分类，返回分类名；失败返回 None"""
    global _consecutive_failures
    if _consecutive_failures >= _CIRCUIT_BREAKER_LIMIT:
        return None  # 熔断中
    key = _load_key()
    if not key:
        log.warning("无 LLM_API_KEY，跳过 LLM 分类")
        return None
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    body = (text or "")[:600]
    user = f"标题：{title}\n正文：{body}" if body else f"标题：{title}"
    for attempt in range(retries + 1):
        try:
            r = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 2000,
                    "reasoning_effort": "low",
                    "temperature": 0,
                },
                timeout=60,
            )
            if r.status_code != 200:
                log.warning("LLM %s: %s", r.status_code, r.text[:120])
                continue
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            # 输出可能带粗体/理由，取最先出现的分类名
            best, best_pos = None, 10**9
            for cat in CATEGORIES:
                pos = content.find(cat)
                if 0 <= pos < best_pos:
                    best, best_pos = cat, pos
            if best:
                _consecutive_failures = 0
                return best
            log.warning("LLM 输出无法解析: %r", content[:80])
        except Exception as e:
            log.warning("LLM 调用失败(第%d次): %s", attempt + 1, e)
    _consecutive_failures += 1
    if _consecutive_failures == _CIRCUIT_BREAKER_LIMIT:
        log.warning("LLM 连续失败 %d 次，熔断并回退关键词分类", _CIRCUIT_BREAKER_LIMIT)
    return None


def analyze_guidance(title: str, text: str = ""):
    """评估对企业的指导价值，返回 (level, reason)；失败返回 (None, None)"""
    global _consecutive_failures
    if _consecutive_failures >= _CIRCUIT_BREAKER_LIMIT:
        return None, None
    key = _load_key()
    if not key:
        return None, None
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    body = (text or "")[:800]
    user = f"标题：{title}\n正文：{body}" if body else f"标题：{title}"
    for attempt in range(3):
        try:
            r = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _GUIDANCE_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 2000,
                    "reasoning_effort": "low",
                    "temperature": 0,
                },
                timeout=60,
            )
            if r.status_code != 200:
                log.warning("指导价值评估 %s: %s", r.status_code, r.text[:100])
                continue
            content = (r.json()["choices"][0]["message"].get("content") or "").strip()
            lines = [ln.strip().strip("*# ") for ln in content.splitlines() if ln.strip()]
            level = next((lv for lv in ("高", "中", "低")
                          if lines and lines[0].startswith(lv)), None)
            if level:
                reason = lines[1] if len(lines) > 1 else ""
                reason = re.sub(r"^(理由|原因)[:：]\s*", "", reason)[:100]
                _consecutive_failures = 0
                return level, reason
            log.warning("指导价值输出无法解析: %r", content[:80])
        except Exception as e:
            log.warning("指导价值评估失败(第%d次): %s", attempt + 1, e)
    _consecutive_failures += 1
    return None, None


def classify_smart(title: str, text: str = "") -> str:
    """CLASSIFY_MODE=llm 时 LLM 优先，失败回退关键词；keyword 时纯关键词"""
    if os.environ.get("CLASSIFY_MODE", "llm").lower() == "keyword":
        return keyword_classify(title, text)
    cache_key = (title or "") + "\0" + (text or "")[:200]
    if cache_key in _cache:
        return _cache[cache_key]
    result = classify_llm(title, text)
    if not result:
        result = keyword_classify(title, text)
        log.info("LLM 失败，回退关键词: %s -> %s", (title or "")[:30], result)
    if len(_cache) > 5000:  # 防止长进程内无限增长
        _cache.clear()
    _cache[cache_key] = result
    return result
