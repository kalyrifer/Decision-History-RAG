"""Реранкер bge-reranker-base (CrossEncoder) поверх evidence-set.

Используется опционально в Фазе 7: включаем в пайплайн только если абляция
показала прирост (см. eval/rerank_ablation.py).
"""

import sys

sys.path.insert(0, ".")

from config import RERANK_MODEL, RERANK_DEVICE

_model = None


def get_reranker():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(RERANK_MODEL, device=RERANK_DEVICE)
    return _model


def rerank(query: str, candidates: list[dict], top_k: int = 20) -> list[dict]:
    """candidates: [{entity_id, text, ...}]; возвращает те же dict с score."""
    if not candidates:
        return []
    model = get_reranker()
    pairs = [(query, c.get("text") or "") for c in candidates]
    scores = model.predict(pairs, convert_to_numpy=True)
    scored = sorted(zip(candidates, scores), key=lambda x: -float(x[1]))
    out = []
    for c, s in scored[:top_k]:
        c = dict(c)
        c["rerank_score"] = float(s)
        out.append(c)
    return out
