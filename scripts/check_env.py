"""Проверка окружения Фазы 0: ключи API, GitHub-токен, LLM-провайдер, Postgres + pgvector."""

import sys

import httpx
import psycopg

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(__file__).removesuffix("scripts\\check_env.py").removesuffix("scripts/check_env.py"))
from config import (
    GEMINI_API_KEY,
    GITHUB_TOKEN,
    LLM_FREE_SUFFIX,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE,
    PG_DSN,
)


def main() -> int:
    results: list[tuple[str, bool, str, bool]] = []  # (name, ok, detail, required)

    results.append(
        ("GitHub токен задан", bool(GITHUB_TOKEN), "впиши GITHUB_TOKEN в .env", True)
    )
    if GITHUB_TOKEN:
        try:
            r = httpx.get(
                "https://api.github.com/rate_limit",
                headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
                timeout=15,
            )
            remaining = r.json()["resources"]["core"]["remaining"]
            results.append(
                ("GitHub API", r.status_code == 200, f"status={r.status_code}, осталось запросов: {remaining}", True)
            )
        except Exception as e:
            results.append(("GitHub API", False, str(e), True))

    results.append(
        ("OpenRouter ключ задан", bool(OPENROUTER_API_KEY),
         "зарегистрируйся на openrouter.ai → Keys → создай ключ → впиши OPENROUTER_API_KEY в .env", True)
    )
    if OPENROUTER_API_KEY:
        try:
            r = httpx.get(
                f"{OPENROUTER_BASE}/models",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                timeout=20,
            )
            free = []
            if r.status_code == 200:
                free = [m["id"] for m in r.json().get("data", []) if m["id"].endswith(LLM_FREE_SUFFIX)]
            results.append(
                ("OpenRouter API", r.status_code == 200,
                 f"status={r.status_code}, бесплатных моделей: {len(free)}, например: {', '.join(free[:3])}",
                 True)
            )
        except Exception as e:
            results.append(("OpenRouter API", False, str(e), True))

    if GEMINI_API_KEY:
        try:
            r = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": GEMINI_API_KEY},
                timeout=15,
            )
            ok = r.status_code == 200
            detail = "работает" if ok else f"status={r.status_code}: {r.json().get('error', {}).get('message', '?')}"
            results.append(("Gemini API (необязательно)", ok, detail, False))
        except Exception as e:
            results.append(("Gemini API (необязательно)", False, str(e), False))

    try:
        with psycopg.connect(PG_DSN, connect_timeout=5) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            ver = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
        results.append(("Postgres + pgvector", True, f"pgvector {ver[0]}", True))
    except Exception as e:
        results.append(
            ("Postgres + pgvector", False, f"{e} (контейнер запущен? docker compose up -d)", True)
        )

    print()
    failed = False
    for name, ok, detail, required in results:
        if ok:
            mark = "PASS"
        elif required:
            mark, failed = "FAIL", True
        else:
            mark = "WARN"
        print(f"[{mark}] {name}: {detail}")
    print()
    print("Окружение готово." if not failed else "Есть проблемы — исправь и перезапусти проверку.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
