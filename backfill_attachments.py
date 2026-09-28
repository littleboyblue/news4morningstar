"""回填存量文章的附件链接：重新抓取详情页，仅提取附件。

用法：python backfill_attachments.py [--missing]   # --missing 只处理未回填过的
"""
import json
import sys
import warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

import db
from crawler import _fetch, extract_attachments


def main():
    only_missing = "--missing" in sys.argv
    db.init_db()
    conn = db.get_conn()
    sql = "SELECT id,url,title FROM articles"
    if only_missing:
        sql += " WHERE attachments IS NULL"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    print(f"共 {len(rows)} 条待扫描")

    def work(r):
        html = _fetch(r["url"])
        if not html:
            return r["id"], None
        soup = BeautifulSoup(html, "lxml")
        return r["id"], extract_attachments(soup, r["url"])

    found = done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            aid, atts = fut.result()
            if atts is not None:
                conn.execute("UPDATE articles SET attachments=? WHERE id=?",
                             (json.dumps(atts, ensure_ascii=False) if atts else "[]", aid))
                done += 1
                if atts:
                    found += 1
            if i % 30 == 0:
                conn.commit()
                print(f"  进度 {i}/{len(rows)}，含附件 {found} 条")
    conn.commit()
    print(f"\n完成：扫描 {done} 条，含附件 {found} 条")
    for r in conn.execute(
            "SELECT title, attachments FROM articles WHERE attachments IS NOT NULL AND attachments != '[]' LIMIT 8").fetchall():
        atts = json.loads(r["attachments"])
        print(f"  📎 {r['title'][:38]}")
        for a in atts:
            print(f"      {a['name'][:50]}")


if __name__ == "__main__":
    main()
