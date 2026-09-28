"""SQLite 数据层"""
import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "power_news.db")

_local = threading.local()


def get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '其他',
            publish_date TEXT,
            summary TEXT,
            content TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(publish_date DESC);

        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            new_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            message TEXT
        );
        """
    )
    conn.commit()
    # 兼容旧库：补充指导价值分析字段
    for col, ddl in [("guidance_level", "TEXT"), ("guidance_reason", "TEXT"),
                     ("attachments", "TEXT"), ("content_html", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()
    # 上次进程异常退出时会残留 running 状态的抓取日志，标记为 interrupted
    conn.execute(
        "UPDATE crawl_log SET status='interrupted', "
        "finished_at=COALESCE(finished_at, ?) WHERE status='running'",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()


def insert_article(article):
    """article: dict(url,title,source,category,publish_date,summary,content). 返回是否为新记录"""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO articles (url,title,source,category,publish_date,summary,content,guidance_level,guidance_reason,attachments,content_html,fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                article["url"],
                article["title"],
                article["source"],
                article["category"],
                article.get("publish_date"),
                article.get("summary"),
                article.get("content"),
                article.get("guidance_level"),
                article.get("guidance_reason"),
                article.get("attachments"),
                article.get("content_html"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def query_articles(category=None, source=None, keyword=None, date_from=None,
                   date_to=None, guidance=None, page=1, page_size=20):
    where, params = [], []
    if guidance:
        where.append("guidance_level = ?")
        params.append(guidance)
    if category and category != "全部":
        where.append("category = ?")
        params.append(category)
    if source and source != "全部":
        where.append("source = ?")
        params.append(source)
    if keyword:
        where.append("(title LIKE ? OR summary LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if date_from:
        where.append("publish_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("publish_date <= ?")
        params.append(date_to)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) c FROM articles {clause}", params).fetchone()["c"]
    rows = conn.execute(
        f"""SELECT id,url,title,source,category,publish_date,summary,guidance_level,guidance_reason,fetched_at
            FROM articles {clause}
            ORDER BY COALESCE(publish_date, fetched_at) DESC, id DESC
            LIMIT ? OFFSET ?""",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return total, [dict(r) for r in rows]


def get_article(article_id):
    row = get_conn().execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
    return dict(row) if row else None


def query_day_counts(month):
    """返回某月每天有记录的条数: {'2026-09-24': 15, ...}"""
    rows = get_conn().execute(
        "SELECT publish_date d, COUNT(*) c FROM articles "
        "WHERE publish_date LIKE ? GROUP BY publish_date",
        (month + "%",)).fetchall()
    return {r["d"]: r["c"] for r in rows if r["d"]}


def get_stats():
    conn = get_conn()
    by_cat = [dict(r) for r in conn.execute(
        "SELECT category, COUNT(*) count FROM articles GROUP BY category ORDER BY count DESC")]
    by_src = [dict(r) for r in conn.execute(
        "SELECT source, COUNT(*) count FROM articles GROUP BY source ORDER BY count DESC")]
    total = conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    last = conn.execute(
        "SELECT * FROM crawl_log ORDER BY id DESC LIMIT 1").fetchone()
    guidance = conn.execute(
        "SELECT COUNT(*) c FROM articles WHERE guidance_level='高'").fetchone()["c"]
    return {
        "total": total,
        "by_category": by_cat,
        "by_source": by_src,
        "guidance_high": guidance,
        "last_crawl": dict(last) if last else None,
    }


def log_start():
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO crawl_log (started_at,status) VALUES (?, 'running')",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    return cur.lastrowid


def log_finish(log_id, status, new_count, total_count, message=""):
    conn = get_conn()
    conn.execute(
        "UPDATE crawl_log SET finished_at=?, status=?, new_count=?, total_count=?, message=? WHERE id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, new_count, total_count, message, log_id),
    )
    conn.commit()
