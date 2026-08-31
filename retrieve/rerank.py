"""Реранкер bge-reranker-base как ФИЛЬТР (не ре-ранк) + слабые prior-веса событий.

По итогам абляции (eval/rerank_ablation.py) реранкер в режиме «пересортировки»
деградирует recall — он опускает лексически далёкие исходные issue. Поэтому здесь
он используется только как ФИЛЬТР: отсекаем кандидатов ниже ОТНОСИТЕЛЬНОГО
(квантильного) порога, НЕ меняя порядок выживших.

Дополнительно применяются слабые prior-веса по типу события (commit/PR) — они
смещают порог, но НЕ являются жёстким правилом: высокий rerank-скор перевешивает.
"""

import re
import sys

sys.path.insert(0, ".")

from config import RERANK_MODEL, RERANK_DEVICE

_model = None

# Слабые prior-веса: множители на rerank-скор.
# upweight: архитектурные/релизные события; downweight: рядовые/технические.
_UP_RE = re.compile(r"breaking|architecture|architectural|release|v2\b|major|refactor|pydantic-core|strict\b|deprecat", re.I)
_DOWN_RE = re.compile(r"^(docs|doc|chore|ci|style|typo|fix link|bump|dependabot|readme|format|lint)", re.I)

UP_WEIGHT = 1.25
DOWN_WEIGHT = 0.75
NEUTRAL = 1.0


def prior_weight(kind: str, title: str, message: str = "") -> float:
    """Мягкий множитель важности события. Никогда не 0 — только коррекция порога."""
    text = f"{title or ''} {message or ''}"
    if _UP_RE.search(text):
        return UP_WEIGHT
    if kind == "commit" and _DOWN_RE.search((title or "").strip()):
        return DOWN_WEIGHT
    return NEUTRAL


def get_reranker():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(RERANK_MODEL, device=RERANK_DEVICE)
    return _model


def _score_pairs(query: str, texts: list[str]) -> list[float]:
    model = get_reranker()
    pairs = [(query, t or "") for t in texts]
    import numpy as np

    scores = model.predict(pairs, convert_to_numpy=True)
    return [float(s) for s in np.asarray(scores).ravel()]


def filter_candidates(query: str, candidates: list[dict],
                      keep_frac: float = 0.6, min_keep: int = 5) -> list[dict]:
    """Отфильтровать кандидатов по rerank-скору (с prior-весами), сохранив порядок.

    candidates: [{entity_id, kind, title, text, ...}] (уже ранжированы пайплайном).
    Возвращает тот же список без части элементов; у выживших добавлен rerank_score.
    Порог — квантиль (1-keep_frac) по распределению скоров вопроса, а не константа.
    """
    if not candidates:
        return []
    if len(candidates) <= min_keep:
        for c in candidates:
            c["rerank_score"] = 0.0
        return candidates

    texts = [c.get("text") or c.get("title") or "" for c in candidates]
    raw = _score_pairs(query, texts)
    weighted = []
    for c, s in zip(candidates, raw):
        w = prior_weight(c.get("kind", ""), c.get("title", ""), c.get("message", ""))
        weighted.append((c, s * w))
    weighted.sort(key=lambda x: -x[1])

    import numpy as np

    arr = np.array([v for _, v in weighted])
    threshold = float(np.quantile(arr, 1.0 - keep_frac))

    kept = [c for c, v in weighted if v >= threshold]
    if len(kept) < min_keep:
        kept = [c for c, _ in weighted[:min_keep]]
    for c, v in weighted:
        if c in kept:
            c["rerank_score"] = round(v, 4)
    return kept
