"""回填存量文章：重建正文文本、净化 HTML 正文、附件。

用法：python backfill_content.py [--missing]
"""
import json
import re
import sys
import warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from classifier import make_summary
from crawler import _fetch, extract_detail, _clean_lines


def main():
    only_missing = "--missing" in sys.argv
    db.init_db()
    conn = db.get_conn()
    sql = "SELECT id,url,title,content FROM articles"
    if only_missing:
        sql += " WHERE content_html IS NULL"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    print(f"共 {len(rows)} 条待处理")

    def work(r):
        html = _fetch(r["url"])
        if not html:
            return None
        title, date, text, atts, content_html = extract_detail(html, r["url"])
        # 标题以库内为准（库内已修复过截断问题）
        title = r["title"]
        text = re.sub(r"\n{3,}", "\n\n",
                      _clean_lines(text, title=title,
                                   attach_names=[a["name"] for a in atts]))
        return {
            "id": r["id"],
            "content": text[:50000],
            "summary": make_summary(text),
            "publish_date": date,
            "attachments": json.dumps(atts, ensure_ascii=False) if atts else "[]",
            "content_html": content_html,
        }

    done = fail = with_html = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, r): r for r in rows}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
            except Exception as e:
                res = None
                print("  处理失败:", futs[fut]["url"][:60], e)
            if res:
                conn.execute(
                    """UPDATE articles SET content=?, summary=?, attachments=?,
                       content_html=?, publish_date=COALESCE(?, publish_date)
                       WHERE id=?""",
                    (res["content"], res["summary"], res["attachments"],
                     res["content_html"], res["publish_date"], res["id"]))
                done += 1
                if res["content_html"]:
                    with_html += 1
            else:
                fail += 1
            if i % 30 == 0:
                conn.commit()
                print(f"  进度 {i}/{len(rows)}  成功 {done} 富文本 {with_html} 失败 {fail}")
    conn.commit()
    print(f"\n完成：成功 {done}，其中富文本 {with_html}，失败 {fail}")


if __name__ == "__main__":
    main()
