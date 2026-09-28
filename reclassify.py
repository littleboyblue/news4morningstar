"""对存量文章重新分类。

用法：
    python reclassify.py            # LLM 分类（默认，失败回退关键词）
    python reclassify.py --keyword  # 纯关键词分类
"""
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import db


def main():
    use_llm = "--keyword" not in sys.argv
    if use_llm:
        from llm_classifier import classify_smart as classify
    else:
        from classifier import classify

    db.init_db()
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,title,category,content FROM articles").fetchall()]
    print(f"共 {len(rows)} 条，模式: {'LLM' if use_llm else '关键词'}")

    def work(r):
        return r["id"], r["title"], r["category"], classify(r["title"], r["content"] or "")

    changed, after = 0, Counter()
    changes = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            aid, title, old, new = fut.result()
            after[new] += 1
            if new != old:
                changed += 1
                changes.append((title, old, new))
                conn.execute("UPDATE articles SET category=? WHERE id=?", (new, aid))
            if i % 20 == 0:
                print(f"  进度 {i}/{len(rows)}")
    conn.commit()

    before = Counter(r["category"] for r in rows)
    print(f"\n变更 {changed} 条\n{'分类':<12} {'前':>4} {'后':>4}")
    for c in sorted(after, key=lambda x: -after[x]):
        print(f"  {c:<12} {before.get(c,0):>4} {after[c]:>4}")
    if changes:
        print("\n变更明细：")
        for t, o, n in changes:
            print(f"  [{o} -> {n}] {t[:44]}")


if __name__ == "__main__":
    main()
