"""Переписывание пользовательского запроса: перевод на английский + ключевые слова (free LLM)."""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

import httpx

from config import OPENROUTER_API_KEY, OPENROUTER_BASE

CACHE_PATH = Path("data") / "rewrite_cache.json"
DEADLINE_S = 6.0

_cache = {}
_model_id = None
PREFERRED = ("gemma-4-31b", "glm", "minimax", "gemma")

PROMPT = """Translate the user question into a short English search query.
The search runs over GitHub issues/PRs/commits of a Python library.
Reply with ONLY compact JSON, no explanations:
{"en": "<concise english question>", "kw": ["3-6 english keywords"]}
Question: {q}"""


def _candidates(client: httpx.Client) -> list[str]:
    global _model_id
    if _model_id:
        return [_model_id]
    r = client.get(f"{OPENROUTER_BASE}/models", headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"}, timeout=20)
    r.raise_for_status()
    free = [m["id"] for m in r.json()["data"] if m["id"].endswith(":free")]
    ranked = []
    for pref in PREFERRED:
        for mid in free:
            if pref in mid.lower() and mid not in ranked and "reasoning" not in mid:
                ranked.append(mid)
    for mid in sorted(free):
        if mid not in ranked and "reasoning" not in mid and "safety" not in mid:
            ranked.append(mid)
    return ranked


def _extract_json(text: str) -> dict | None:
    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.insert(0, brace.group(0))
    for cand in candidates:
        try:
            d = json.loads(cand)
            en = None
            for k, v in d.items():
                kk = k.strip().strip('"').lower()
                if kk == "en" and isinstance(v, str):
                    en = v
            if en:
                kw = []
                for v in d.values() if isinstance(d, dict) else []:
                    if isinstance(v, list):
                        kw = [x.strip() for x in v if isinstance(x, str)]
                        break
                return {"en": en.strip(), "kw": kw[:6]}
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def rewrite(query: str) -> dict:
    disk = _load_cache()
    if query in disk:
        return disk[query]
    if query in _cache:
        return _cache[query]
    result = {"en": query, "kw": []}
    deadline = time.time() + DEADLINE_S
    try:
        with httpx.Client() as client:
            cands = _candidates(client)[:4]
            last_err = "нет кандидатов"
            for mid in cands:
                if time.time() > deadline:
                    last_err = f"дедлайн {DEADLINE_S}с исчерпан"
                    break
                for attempt in range(2):
                    if time.time() > deadline:
                        break
                    try:
                        r = client.post(
                            f"{OPENROUTER_BASE}/chat/completions",
                            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                            json={
                                "model": mid,
                                "messages": [{"role": "user", "content": PROMPT.replace("{q}", query)}],
                                "temperature": 0,
                                "max_tokens": 400,
                            },
                            timeout=40,
                        )
                        if r.status_code == 429 and attempt == 0:
                            time.sleep(2)
                            continue
                        r.raise_for_status()
                        payload = r.json()
                        msg = payload["choices"][0]["message"]
                        content = msg.get("content")
                        if not isinstance(content, str) or not content.strip():
                            fr = payload["choices"][0].get("finish_reason")
                            raise ValueError(f"пустой контент (finish_reason={fr})")
                        parsed = _extract_json(content)
                        if not parsed:
                            raise ValueError(f"неструктурированный ответ: {content[:100]!r}")
                        result["en"] = parsed["en"].strip()
                        result["kw"] = parsed.get("kw", [])
                        _model_id = mid
                        last_err = ""
                        break
                    except Exception as e:
                        last_err = f"{mid}: {type(e).__name__}: {str(e)[:90]}"
            if last_err:
                print(f"[rewrite] fallback на исходный запрос; последняя ошибка: {last_err}")
    except Exception as e:
        print(f"[rewrite] fallback на исходный запрос: {type(e).__name__}: {str(e)[:80]}")

    _cache[query] = result
    disk[query] = result
    _save_cache(disk)
    return result


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Почему pydantic не использует строгий режим по умолчанию?"
    print(rewrite(q))
