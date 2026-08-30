"""Абляция реранкера: сравнение recall@10/MRR до и после bge-reranker-base.

Запуск:
    python eval/rerank_ablation.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
import psycopg

from config import PG_DSN, FTS_WEIGHT
from retrieve import pipeline
from retrieve.hybrid import get_model
from retrieve.rerank import get_reranker, rerank

GOLDEN = Path("eval/golden_set.yaml")


def load_golden() -> list[dict]:
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


def _anchor_map(cur, golden) -> dict[str, list[int | None]]:
    out = {}
    for item in golden:
        ids = []
        for a in item["expected_anchors"]:
            cur.execute(
                "SELECT id FROM entities WHERE kind=%s AND native_id=%s",
                (a["type"], str(a["number"])),
            )
            row = cur.fetchone()
            ids.append(row[0] if row else None)
        out[item["id"]] = ids
    return out


def _mrr(anchor_ids: list[int], ranked_ids: list[int]) -> float:
    seen = set()
    for rank, eid in enumerate(ranked_ids, 1):
        if eid in anchor_ids and eid not in seen:
            return 1.0 / rank
    return 0.0


def _fetch_texts(cur, entity_ids: list[int]) -> dict[int, str]:
    if not entity_ids:
        return {}
    cur.execute(
        "SELECT entity_id, body FROM chunks WHERE entity_id = ANY(%s) ORDER BY entity_id, idx",
        (entity_ids,),
    )
    chunks = {}
    for eid, text in cur.fetchall():
        chunks.setdefault(eid, []).append(text)
    return {eid: "\n\n".join(texts) for eid, texts in chunks.items()}


def main() -> None:
    golden = load_golden()
    get_model()
    get_reranker()
    print(f"прогрев моделей, абляция реранкера на {len(golden)} вопросах...\n")

    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            amap = _anchor_map(cur, golden)

    results = []
    for item in golden:
        qid = item["id"]
        q = item["question"]
        anchors = [a for a in amap[qid] if a is not None]
        a_set = set(anchors)

        # baseline (no rerank)
        res = pipeline.search(q, no_expand=False, rewrite_query=True, mode="hybrid", w_fts=FTS_WEIGHT)
        fused_rank_no = [r["entity_id"] for r in res["rows"] if r["hop"] == 0]
        baseline_top10 = set(fused_rank_no[:10])
        baseline_recall = len(a_set & baseline_top10) / len(a_set) if a_set else 0.0
        baseline_mrr = _mrr(a_set, fused_rank_no)

        # rerank: get evidence texts, rerank, measure
        all_ids = [r["entity_id"] for r in res["rows"]]
        with psycopg.connect(PG_DSN) as conn2:
            with conn2.cursor() as cur2:
                texts = _fetch_texts(cur2, all_ids)
        candidates = [
            {"entity_id": r["entity_id"], "kind": r["kind"], "number": r.get("number"),
             "text": texts.get(r["entity_id"], r.get("title") or "")}
            for r in res["rows"]
        ]
        rr_start = time.time()
        reranked = rerank(q, candidates, top_k=len(candidates))
        rr_time = time.time() - rr_start
        rr_rank = [c["entity_id"] for c in reranked]
        rr_top10 = set(rr_rank[:10])
        rr_recall = len(a_set & rr_top10) / len(a_set) if a_set else 0.0
        rr_mrr = _mrr(a_set, rr_rank)

        results.append({
            "id": qid, "n_anchors": len(a_set),
            "baseline": {"recall@10": baseline_recall, "mrr": baseline_mrr},
            "reranked": {"recall@10": rr_recall, "mrr": rr_mrr,
                         "time_s": round(rr_time, 2)},
        })

    # таблица
    print(f"{'id':<5} {'anchor#':>3}  {'baseline':>20}  {'reranked':>20}")
    print(f"{'':5} {'':3}  {'recall@10':>8} {'MRR':>6}  {'recall@10':>8} {'MRR':>6} {'t,с':>5}")
    print("-" * 70)
    b_recall_sum = 0
    b_mrr_sum = 0
    r_recall_sum = 0
    r_mrr_sum = 0
    n = len(results)
    for r in results:
        b = r["baseline"]
        rr = r["reranked"]
        b_recall_sum += b["recall@10"]
        b_mrr_sum += b["mrr"]
        r_recall_sum += rr["recall@10"]
        r_mrr_sum += rr["mrr"]
        print(f"{r['id']:<5} {r['n_anchors']:>3}  "
              f"{b['recall@10']:>8.0%} {b['mrr']:>6.3f}  "
              f"{rr['recall@10']:>8.0%} {rr['mrr']:>6.3f} {rr['time_s']:>5.1f}")
    print("-" * 70)
    print(f"среднее:          {b_recall_sum/n:>8.0%} {b_mrr_sum/n:>6.3f}  "
          f"{r_recall_sum/n:>8.0%} {r_mrr_sum/n:>6.3f}")
    delta_recall = (r_recall_sum - b_recall_sum) / n * 100
    delta_mrr = (r_mrr_sum - b_mrr_sum) / n * 100
    print(f"дельты:                {delta_recall:+.1f}%         {delta_mrr:+.1f}%")
    print(f"среднее время реранка: {sum(r['reranked']['time_s'] for r in results)/n:.1f}с")
    gain = delta_recall > 0
    print(f"\nВЕРДИКТ: реранкер {'ПОКАЗАЛ ПРИРОСТ' if gain else 'НЕ ПОКАЗАЛ ПРИРОСТА'}")
    print(f"Включать в пайплайн: {'ДА' if gain else 'НЕТ (по условию плана)'}")


if __name__ == "__main__":
    main()