"""把 SQLite 数据导出为静态站点（public/），供 Cloudflare Pages 托管。

用法：
    python export_static.py

产物：
    public/index.html          前端页面（资源路径改写为相对路径）
    public/app.js / style.css
    public/data/index.json     全部文章元数据（按发布时间倒序，客户端筛选/分页）
    public/data/articles/<id>.json  每篇文章详情（正文/富文本/附件）
    public/data/day_counts.json   每天有记录的条数（侧栏日历用）
    public/data/stats.json        分类/来源统计、上次抓取信息
"""
import json
import os
import shutil

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE_DIR, "public")

INDEX_FIELDS = ("id", "title", "source", "category", "publish_date",
                "summary", "guidance_level", "fetched_at")


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def main():
    db.init_db()
    conn = db.get_conn()

    # 重建 public/
    if os.path.exists(PUBLIC):
        shutil.rmtree(PUBLIC)
    os.makedirs(os.path.join(PUBLIC, "data", "articles"))

    # 拷贝前端，资源路径 /static/ 改写为相对路径
    for name in ("index.html", "app.js", "style.css"):
        with open(os.path.join(BASE_DIR, "static", name), encoding="utf-8") as f:
            text = f.read()
        text = text.replace("/static/", "./")
        with open(os.path.join(PUBLIC, name), "w", encoding="utf-8") as f:
            f.write(text)

    # 文章索引（排序与 db.query_articles 保持一致）
    rows = conn.execute(
        f"""SELECT {",".join(INDEX_FIELDS)} FROM articles
            ORDER BY COALESCE(publish_date, fetched_at) DESC, id DESC"""
    ).fetchall()
    write_json(os.path.join(PUBLIC, "data", "index.json"),
               [dict(r) for r in rows])

    # 每篇文章详情
    for r in conn.execute("SELECT * FROM articles").fetchall():
        write_json(os.path.join(PUBLIC, "data", "articles", f"{r['id']}.json"),
                   dict(r))

    # 全量日历计数：{"2026-09-24": 15, ...}
    day_counts = {r["d"]: r["c"] for r in conn.execute(
        "SELECT publish_date d, COUNT(*) c FROM articles "
        "WHERE publish_date IS NOT NULL GROUP BY publish_date").fetchall() if r["d"]}
    write_json(os.path.join(PUBLIC, "data", "day_counts.json"), day_counts)

    # 统计（分类/来源/上次抓取）
    write_json(os.path.join(PUBLIC, "data", "stats.json"), db.get_stats())

    # 把 WAL 内容合并回主库文件，保证单独提交 .db 文件即完整
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    total = os.path.getsize(os.path.join(PUBLIC, "data", "index.json"))
    print(f"导出完成：{len(rows)} 篇文章 → {PUBLIC}（索引 {total/1024:.0f} KB）")


if __name__ == "__main__":
    main()
