"""OpenRouter free-model rotation for text generation."""

import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

import httpx

from config import OPENROUTER_API_KEY, OPENROUTER_BASE

DEADLINE_S = 90.0
MAX_TOKENS = 4096
TEMPERATURE = 0.1

MODELS = [
    "z-ai/glm-5.2:free",
    "minimax/minimax-m3:free",
    "minimax/minimax-m2.7:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]

_used_models: dict[str, float] = {}
_total_tokens = 0
_total_cost = 0.0


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [llm] {msg}", flush=True)


def _model_available(mid: str, now: float) -> bool:
    cooldown = _used_models.get(mid)
    if cooldown is None:
        return True
    return now >= cooldown


def generate(prompt: str, system: str = "", max_tokens: int = MAX_TOKENS) -> str:
    global _total_tokens
    deadline = time.time() + DEADLINE_S

    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    last_err = ""
    tried = set()

    log(f"модели в кулдауне: {list(_used_models.keys()) or 'нет'}")

    with httpx.Client() as client:
        for mid in MODELS:
            if mid in tried:
                continue
            tried.add(mid)
            if not _model_available(mid, time.time()):
                remaining = _used_models[mid] - time.time()
                log(f"пропуск {mid} (cooldown {remaining:.0f}с осталось)")
                continue
            if time.time() > deadline:
                last_err = f"дедлайн {DEADLINE_S}с"
                break

            log(f"пробую {mid}...")

            for attempt in range(2):
                if time.time() > deadline:
                    break
                try:
                    r = client.post(
                        f"{OPENROUTER_BASE}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "HTTP-Referer": "https://github.com/user/decision-rag",
                        },
                        json={
                            "model": mid,
                            "messages": msgs,
                            "temperature": TEMPERATURE,
                            "max_tokens": max_tokens,
                        },
                        timeout=90,
                    )
                    if r.status_code == 429:
                        wait = 3 * (attempt + 1)
                        log(f"429 от {mid}, пауза {wait}с (попытка {attempt+1}/2)")
                        _used_models[mid] = time.time() + 120
                        time.sleep(wait)
                        continue
                    if r.status_code in (500, 502, 503):
                        time.sleep(2 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    payload = r.json()
                    msg = payload["choices"][0]["message"]
                    content = msg.get("content") or ""
                    if not content.strip():
                        raise ValueError(f"пустой ответ (finish_reason={payload['choices'][0].get('finish_reason')})")
                    usage = payload.get("usage", {})
                    prompt_tok = usage.get("prompt_tokens", 0)
                    compl_tok = usage.get("completion_tokens", 0)
                    _total_tokens += prompt_tok + compl_tok
                    log(f"{mid}: {prompt_tok}+{compl_tok} токенов")
                    return content.strip()
                except httpx.HTTPStatusError as e:
                    last_err = f"{mid}: HTTP {e.response.status_code}"
                except Exception as e:
                    last_err = f"{mid}: {type(e).__name__}: {str(e)[:80]}"

    raise RuntimeError(f"Все модели исчерпаны. Последняя ошибка: {last_err}")


def stats() -> dict:
    return {"total_tokens": _total_tokens, "total_cost_usd": _total_cost}
