"""对存量文章批量评估指导价值（LLM）。

用法：python analyze_guidance.py [--missing]   # --missing 只处理未评估的
"""
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from llm_classifier import analyze_guidance


def main():
    only_missing = "--missing" in sys.argv
    db.init_db()
    conn = db.get_conn()
    sql = "SELECT id,title,content FROM articles"
    if only_missing:
        sql += " WHERE guidance_level IS NULL"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    print(f"共 {len(rows)} 条待评估")

    def work(r):
        level, reason = analyze_guidance(r["title"], r["content"] or "")
        return r["id"], r["title"], level, reason

    stats, samples = Counter(), []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            aid, title, level, reason = fut.result()
            if level:
                conn.execute(
                    "UPDATE articles SET guidance_level=?, guidance_reason=? WHERE id=?",
                    (level, reason, aid))
                stats[level] += 1
                if level == "高":
                    samples.append((title, reason))
            if i % 20 == 0:
                conn.commit()
                print(f"  进度 {i}/{len(rows)}  高:{stats['高']} 中:{stats['中']} 低:{stats['低']}")
    conn.commit()
    print(f"\n完成：高 {stats['高']} / 中 {stats['中']} / 低 {stats['低']} / 失败 {len(rows)-sum(stats.values())}")
    print("\n=== 高指导价值标识 ===")
    for t, r in samples:
        print(f"  ⭐ {t[:42]}\n     {r}")


if __name__ == "__main__":
    main()
