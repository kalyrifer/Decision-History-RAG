"""Фаза 6: оценка golden set на двух уровнях + абляции.

Уровень retrieval (метрики по якорям):
  recall@10 после fusion, recall@10 после expansion (разница = польза графа), MRR якорей.
  Абляции одним флагом: --mode {hybrid,dense,fts}, --no-expand.

Уровень ответов (рубрика 0-2, оценку ставит человек):
  python eval/run_eval.py answers          # сгенерировать ответы LLM -> eval/answers.yaml
  # человек проставляет score (0/1/2) в eval/answers.yaml
  python eval/run_eval.py score            # свести таблицу и метрики

Примеры:
  python eval/run_eval.py retrieval
  python eval/run_eval.py retrieval --mode dense
  python eval/run_eval.py retrieval --mode fts --no-expand
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from config import PG_DSN, FTS_WEIGHT
from retrieve.hybrid import get_model

GOLDEN_PATH = Path("eval") / "golden_set.yaml"
ANSWERS_PATH = Path("eval") / "answers.yaml"
MODES = ("hybrid", "dense", "fts")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_golden() -> list[dict]:
    return yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# Level 1: retrieval metrics
# ----------------------------------------------------------------------------

def _anchor_map(cur, golden) -> dict[str, list[int | None]]:
    """id вопроса -> список entity-id якорей (None, если якорь не в БД)."""
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


def _split_rows(res: dict) -> tuple[list[dict], list[dict]]:
    fused = [r for r in res["rows"] if r["hop"] == 0]
    expanded = [r for r in res["rows"] if r["hop"] > 0]
    return fused, expanded


def _mrr(anchor_ids: list[int], ranked_ids: list[int]) -> float:
    seen = set()
    for rank, eid in enumerate(ranked_ids, 1):
        if eid in anchor_ids and eid not in seen:
            return 1.0 / rank
    return 0.0


def run_retrieval(mode: str, no_expand: bool, json_out: bool, w_fts: float = 1.0) -> dict:
    import psycopg

    golden = load_golden()
    get_model()
    log(f"прогрев модели, режим={mode}, no_expand={no_expand}")

    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            amap = _anchor_map(cur, golden)

    per_q = []
    a_fusion_top10 = 0
    a_fusion_all = 0
    a_evidence = 0
    a_total = 0
    hit_fusion_q = 0
    hit_evidence_q = 0
    mrrs = []
    rescued_q = []
    total_anchors_below_top10 = 0

    for item in golden:
        qid = item["id"]
        q = item["question"]
        anchor_ids = [a for a in amap[qid] if a is not None]
        if not anchor_ids:
            log(f"[{qid}] ВАЖНО: ни один якорь не найден в БД")
        a_set = set(anchor_ids)
        a_total += len(a_set)

        t0 = time.time()
        res = pipeline_search(q, mode=mode, no_expand=no_expand, w_fts=w_fts)
        elapsed = time.time() - t0
        fused, expanded = _split_rows(res)
        fused_rank = [r["entity_id"] for r in fused]
        fusion_top10_ids = set(fused_rank[:10])
        fusion_all_ids = set(fused_rank)
        expanded_ids = {r["entity_id"] for r in expanded}
        evidence_ids = fusion_all_ids | expanded_ids

        in_top10 = a_set & fusion_top10_ids
        in_rest = (a_set & fusion_all_ids) - fusion_top10_ids
        in_expanded = (a_set & expanded_ids) - fusion_all_ids
        in_evidence = a_set & evidence_ids

        a_fusion_top10 += len(in_top10)
        a_fusion_all += len(a_set & fusion_all_ids)
        a_evidence += len(in_evidence)
        hit_fusion_q += 1 if in_top10 else 0
        hit_evidence_q += 1 if in_evidence else 0
        mrrs.append(_mrr(a_set, fused_rank))
        total_anchors_below_top10 += len(in_rest)
        if in_expanded:
            rescued_q.append((qid, sorted(in_expanded)))

        per_q.append({
            "id": qid, "elapsed_s": round(elapsed, 2),
            "top10": sorted(in_top10), "fusion_rest": sorted(in_rest),
            "rescued": sorted(in_expanded), "evidence": sorted(in_evidence),
            "anchors": sorted(a_set), "n_anchors": len(a_set),
            "fusion_size": res["fusion_size"], "expanded_size": res["expanded_size"],
        })

    n = len(golden)
    r_fusion_top10 = a_fusion_top10 / a_total if a_total else 0.0
    r_fusion_all = a_fusion_all / a_total if a_total else 0.0
    r_evidence = a_evidence / a_total if a_total else 0.0
    mrr = sum(mrrs) / n if n else 0.0

    print("\n" + "=" * 78)
    print(f"RETRIEVAL (mode={mode}, no_expand={no_expand}, w_fts={w_fts})")
    print("=" * 78)
    print(f"{'id':<5} {'anchor#':>3} {'top10':>5} {'fused>10':>8} {'rescued':>8} {'evidence':>9}  "
          f"t,с  fsz exsz")
    for p in per_q:
        print(
            f"{p['id']:<5} {p['n_anchors']:>3} {len(p['top10']):>5} "
            f"{len(p['fusion_rest']):>8} {len(p['rescued']):>8} {len(p['evidence']):>9}  "
            f"{p['elapsed_s']:>4} {p['fusion_size']:>3} {p['expanded_size']:>3}"
        )
    print("-" * 78)
    print(f"recall@10 fusion(top10) = {r_fusion_top10:.0%}  ({a_fusion_top10}/{a_total})")
    print(f"recall fused(любой ранг)= {r_fusion_all:.0%}  ({a_fusion_all}/{a_total})")
    print(f"recall@10 evidence       = {r_evidence:.0%}  ({a_evidence}/{a_total})")
    print(f"якоря в fusion ниже top-10: {total_anchors_below_top10}")
    print(f"спасено графом (только expansion): {sum(len(p['rescued']) for p in per_q)}")
    print(f"вопросов с якорем в fusion top10: {hit_fusion_q}/{n}")
    print(f"вопросов с якорем в evidence     : {hit_evidence_q}/{n}")
    print(f"MRR якорей (по fusion)           = {mrr:.3f}")
    print(f"вопросы, где граф вытащил якорь: {[q for q, _ in rescued_q] or '-'}")
    if rescued_q:
        for qid, ids in rescued_q:
            print(f"   {qid}: entity_ids={ids}")
    print(f"среднее время поиска: {sum(p['elapsed_s'] for p in per_q) / n:.2f}с")

    result = {
        "mode": mode, "no_expand": no_expand,
        "recall_fusion_top10": round(r_fusion_top10, 4),
        "recall_fusion_all": round(r_fusion_all, 4),
        "recall_evidence": round(r_evidence, 4),
        "mrr": round(mrr, 4), "hit_fusion_q": hit_fusion_q, "hit_evidence_q": hit_evidence_q,
        "anchors_below_top10": total_anchors_below_top10,
        "per_question": per_q,
    }
    if json_out:
        print("\n--- JSON ---")
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


def pipeline_search(query, mode="hybrid", no_expand=False, w_fts=1.0):
    from retrieve import pipeline

    return pipeline.search(query, no_expand=no_expand, rewrite_query=True, mode=mode, w_fts=w_fts)


# ----------------------------------------------------------------------------
# Level 2: answer metrics (LLM answers + manual 0-2 rubric)
# ----------------------------------------------------------------------------

def cmd_answers(limit: int | None) -> None:
    golden = load_golden()
    if limit:
        golden = golden[:limit]
    log(f"генерация ответов LLM для {len(golden)} вопросов (OpenRouter :free)...")

    from retrieve import pipeline
    from synthesize import answer as synth_answer

    recs = []
    for item in golden:
        qid = item["id"]
        q = item["question"]
        log(f"[{qid}] поиск + синтез...")
        res = pipeline.search(q, rewrite_query=True)
        ans = synth_answer.answer(res, q)
        recs.append({
            "id": qid,
            "question": q,
            "expected_summary": item.get("expected_answer_summary", ""),
            "answer": ans["answer"],
            "sources": ans.get("sources", []),
            "confidence": ans.get("confidence", ""),
            "timeline": ans.get("timeline", []),
            "score": None,
            "notes": "",
        })

    ANSWERS_PATH.write_text(
        yaml.safe_dump(recs, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    log(f"ответы сохранены в {ANSWERS_PATH}")
    print("\nПроставьте score (0/1/2) в eval/answers.yaml, затем запустите:")
    print("  python eval/run_eval.py score")


def cmd_score(json_out: bool) -> dict:
    if not ANSWERS_PATH.exists():
        print(f"нет {ANSWERS_PATH}; сначала: python eval/run_eval.py answers")
        return {}
    recs = yaml.safe_load(ANSWERS_PATH.read_text(encoding="utf-8"))
    total = 0
    cnt = {0: 0, 1: 0, 2: 0}
    rows = []
    cite_mismatch = 0
    for r in recs:
        sc = r.get("score")
        if sc is None:
            print(f"  пропуск {r['id']}: score не проставлен")
            continue
        sc = int(sc)
        total += sc
        cnt[sc] += 1
        rows.append((r["id"], sc, r.get("confidence", ""), r.get("notes", "")))
        cited = {s.get("number") for s in r.get("sources", []) if s.get("role") == "cited"}
        expected = set()
        for a in next(
            (it["expected_anchors"] for it in load_golden() if it["id"] == r["id"]), []
        ):
            expected.add(a["number"])
        if cited - expected:
            cite_mismatch += 1

    n = len(rows)
    print("\n" + "=" * 78)
    print("ОТВЕТЫ (рубрика 0-2: 0 неверно/галлюцинация, 1 частично, 2 верно + ссылки)")
    print("=" * 78)
    print(f"{'id':<5} {'score':>5} {'conf':>8}  примечание")
    for qid, sc, conf, notes in rows:
        print(f"{qid:<5} {sc:>5} {conf:>8}  {notes}")
    print("-" * 78)
    if n:
        print(f"средний балл: {total / n:.2f} из 2  ({n} оценено)")
        print(f"распределение: 0={cnt[0]}, 1={cnt[1]}, 2={cnt[2]}")
        print(f"вопросов с корректным ответом (score>=1): {cnt[1] + cnt[2]}/{n}")
        print(f"с полными 2 баллами: {cnt[2]}/{n}")
        print(f"вопросов с цитатами вне ожидаемых якорей: {cite_mismatch}")
    result = {
        "n": n, "mean": round(total / n, 3) if n else None,
        "distribution": cnt, "cite_mismatch": cite_mismatch,
    }
    if json_out:
        print("\n--- JSON ---")
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return result


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Фаза 6: оценка golden set")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_r = sub.add_parser("retrieval", help="retrieval-метрики + абляции")
    p_r.add_argument("--mode", choices=MODES, default="hybrid")
    p_r.add_argument("--no-expand", action="store_true")
    p_r.add_argument("--w-fts", type=float, default=FTS_WEIGHT)
    p_r.add_argument("--json", action="store_true")
    p_r.set_defaults(func=lambda a: run_retrieval(a.mode, a.no_expand, a.json, a.w_fts))

    p_a = sub.add_parser("answers", help="сгенерировать ответы LLM по golden set")
    p_a.add_argument("--limit", type=int, default=None)
    p_a.set_defaults(func=lambda a: cmd_answers(a.limit))

    p_s = sub.add_parser("score", help="свести таблицу оценок из eval/answers.yaml")
    p_s.add_argument("--json", action="store_true")
    p_s.set_defaults(func=lambda a: cmd_score(a.json))

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
