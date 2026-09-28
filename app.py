"""电力行业资讯聚合平台 - Flask 后端"""
import os

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 政府站自签/弱证书

from flask import Flask, jsonify, request, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler

import db
import crawler

app = Flask(__name__, static_folder="static", static_url_path="/static")

# 每天定时抓取一次（默认 07:00，可用环境变量 CRAWL_HOUR 调整）
CRAWL_HOUR = int(os.environ.get("CRAWL_HOUR", 7))
scheduler = BackgroundScheduler()
scheduler.add_job(lambda: crawler.run_crawl(trigger="schedule"),
                  "cron", hour=CRAWL_HOUR, minute=0, id="auto_crawl")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/data/<path:p>")
def data_files(p):
    """本地预览静态导出的数据（先运行 python export_static.py 生成 public/）"""
    return send_from_directory("public/data", p)


def _int_arg(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


@app.route("/api/articles")
def api_articles():
    total, items = db.query_articles(
        category=request.args.get("category"),
        source=request.args.get("source"),
        keyword=request.args.get("keyword"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        guidance=request.args.get("guidance"),
        page=_int_arg("page", 1),
        page_size=min(_int_arg("page_size", 20), 100),
    )
    return jsonify({"total": total, "items": items})


@app.route("/api/articles/<int:aid>")
def api_article_detail(aid):
    art = db.get_article(aid)
    if not art:
        return jsonify({"error": "not found"}), 404
    return jsonify(art)


@app.route("/api/day_counts")
def api_day_counts():
    return jsonify({"days": db.query_day_counts(request.args.get("month", ""))})


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if crawler.get_status()["running"]:
        return jsonify({"ok": False, "message": "抓取任务正在进行中"}), 409
    crawler.run_crawl_async(trigger="manual")
    return jsonify({"ok": True, "message": "已开始后台抓取"})


@app.route("/api/refresh/status")
def api_refresh_status():
    return jsonify(crawler.get_status())


if __name__ == "__main__":
    db.init_db()
    scheduler.start()
    # HOST 默认仅本机访问（局域网开放请设 HOST=0.0.0.0）；PORT 默认 12333
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 12333))
    app.run(host=host, port=port, debug=False, threaded=True)
