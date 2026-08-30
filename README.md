# Decision History RAG

RAG-система, восстанавливающая цепочку принятия инженерных решений из истории
GitHub-репозитория (issue → альтернативы → аргументы → решение → PR/commit)
по вопросу вида «Почему выбрали X?».

Целевой репозиторий: pydantic/pydantic. Стек и план: см. PLAN.md.

## Прогресс

- Фаза 1 (ингест): 5715 issues + 5494 PR + 5692 коммита + 39960 комментариев; 100% покрытие деталями.
- Фаза 2 (граф): 55278 связей (closes/references/pr_commit/parent) + 27055 файлов.
- Фаза 3 (эмбеддинги): 74080 чанков, bge-small-en-v1.5 на CPU, полный прогон за 45.3 мин (~11 чанков/с), HNSW + GIN построены.
- Фаза 4 (поиск): hybrid RRF + query rewrite через OpenRouter :free + graph expansion.
  Golden set: recall@10 = 7/8 (88%), граф вытягивает скрытые якоря в 3 вопросах из 8,
  среднее время поиска 0.68с. Кэш переводов: data/rewrite_cache.json.

## Быстрый старт

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # затем вписать GITHUB_TOKEN и GEMINI_API_KEY
docker compose up -d
python scripts\check_env.py
```
