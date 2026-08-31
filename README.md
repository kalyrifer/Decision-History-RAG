# Decision History RAG

RAG-система, восстанавливающая цепочку принятия инженерных решений из истории
GitHub-репозитория (issue → альтернативы → аргументы → решение → PR/commit)
по вопросу вида «Почему выбрали X?».

Целевой репозиторий: `pydantic/pydantic` (данные уже загружены в БД).
Подробный план и стек — в [PLAN.md](PLAN.md).

---

## Возможности

- **Поиск по истории репозитория** — гибридный: pgvector (dense) + PostgreSQL FTS,
  слияние Reciprocal Rank Fusion, расширение по графу связей (issue↔PR↔commit↔comments).
- **Синтез ответа LLM** — OpenRouter :free модели восстанавливают цепочку решения в
  удобочитаемом формате: стратегическое резюме → **Главные причины решения** →
  **Хронология** → **Источники** (сфокусированный список ссылок с описаниями), на языке
  вопроса.
- **Web-интерфейс (Streamlit)** — задаёшь вопрос, получаешь ответ, источники и timeline.
- **Оценка (eval)** — golden set из 8 вопросов, метрики retrieval (recall@10, MRR)
  и ответов (рубрика 0–2).
- **Полностью бесплатно и локально**: без GPU, без платных API (кроме бесплатных
  моделей OpenRouter :free), всё в Docker Postgres.

---

## Требования

- Windows 11 + PowerShell (проект делался под это)
- Docker Desktop (Postgres + pgvector)
- Python 3.11+ (venv в корне проекта)
- Интернет: для скачивания эмбеддинг-модели и вызовов OpenRouter :free

---

## Быстрый старт (за 5 минут)

### 1. Установка

```powershell
# клонируй/открой проект, затем:
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

### 2. Подними Postgres

```powershell
docker compose up -d
# дождись health: docker ps
```

### 3. Настрой ключи в .env

Открой `.env` и впиши **минимум**: `OPENROUTER_API_KEY` (для ответов LLM).
`GITHUB_TOKEN` нужен только если захочешь перезагрузить данные из GitHub (см. ниже).

### 4. Проверь окружение

```powershell
.venv\Scripts\python scripts\check_env.py
```

Должно быть `PASS` для OpenRouter API и Postgres + pgvector. (GitHub токен и Gemini
можно игнорировать — они не нужны для использования готовых данных.)

### 5. Запусти интерфейс

```powershell
.venv\Scripts\python -m streamlit run ui\app.py
```

Открой в браузере `http://localhost:8501`. Задай вопрос, например:
- «Почему ядро pydantic переписали с Cython на Rust?»
- «Почему pydantic ввел StrictBool?»
- «Зачем появились discriminated unions?»

---

## Использование из командной строки

Вся логика доступна и без UI:

```powershell
# Поиск (без LLM): таблица найденных сущностей + ссылки
.venv\Scripts\python cli.py search "почему появился StrictBool"

# Полный ответ с LLM (нарратив + источники + timeline)
.venv\Scripts\python cli.py ask "почему появился StrictBool"

# Только поиск, без расширения графом
.venv\Scripts\python cli.py search "почему X" --no-expand

# Ответ в JSON (для своих скриптов/интеграций)
.venv\Scripts\python cli.py ask "почему X" --json
```

---

## Структура репозитория

```
├─ cli.py                # команды: search / ask / status / ingest-* / normalize / chunks / embed
├─ config.py             # чтение .env, константы (FTS_WEIGHT, RERANK_MODEL и др.)
├─ docker-compose.yml    # postgres + pgvector
├─ .env.example          # шаблон ключей (копируй в .env)
├─ ingest/               # загрузка git + GitHub API → data/raw/*.jsonl
├─ normalize/            # сырые данные → entities/relations/files в Postgres
├─ represent/            # сущности → текстовые чанки + эмбеддинги (pgvector)
├─ retrieve/             # поиск: hybrid RRF, graph expansion, query rewrite, rerank (опц.)
├─ synthesize/           # LLM: провайдер с ротацией моделей, промпт, сборка ответа
├─ eval/                 # golden_set.yaml, run_eval.py, rerank_ablation.py
├─ ui/                   # Streamlit интерфейс (app.py)
├─ scripts/              # проверки окружения и фаз, вспомогательные скрипты
└─ data/                 # сырые JSONL + bare git-клон (в .gitignore)
```

---

## Оценка (eval) — как проверить качество

```powershell
# retrieval-метрики: recall@10 (fusion/evidence), MRR; абляции режимов
.venv\Scripts\python eval\run_eval.py retrieval
.venv\Scripts\python eval\run_eval.py retrieval --mode dense
.venv\Scripts\python eval\run_eval.py retrieval --mode fts
.venv\Scripts\python eval\run_eval.py retrieval --no-expand

# сгенерировать ответы LLM по golden set -> eval/answers.yaml (заполни score 0/1/2 вручную)
.venv\Scripts\python eval\run_eval.py answers
# свести таблицу ответов
.venv\Scripts\python eval\run_eval.py score
```

Текущие результаты (после правки `FTS_WEIGHT=0.1`):
- recall@10 fusion = 91% (10/11 якорей), MRR = 0.891
- Ответы LLM: 8/8 по 2 балла из 2

---

## Как перезагрузить данные из GitHub (необязательно)

Если данные в БД уже есть — **ничего делать не нужно**. Перезагрузка нужна только
если хочешь сменить репозиторий или подтянуть свежие issue/PR:

```powershell
# 1. GITHUB_TOKEN в .env (read-only)
# 2. Останови пересборку в правильном порядке (если менял TARGET_REPO в config.py — очисти БД):
docker exec -it decision-rag-db psql -U rag -d decision_rag -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 3. Загрузка (докачиваемая, продолжается с чекпоинтов)
.venv\Scripts\python cli.py ingest-git
.venv\Scripts\python cli.py ingest-inventory
.venv\Scripts\python cli.py ingest-details        # --max-batches N для батчами

# 4. Нормализация + граф
.venv\Scripts\python cli.py normalize

# 5. Чанки + эмбеддинги (долго: десятки минут на CPU)
.venv\Scripts\python cli.py chunks
.venv\Scripts\python cli.py embed

# 6. Проверка целостности
.venv\Scripts\python scripts\verify_all.py
```

---

## Известные замечания

- **Реранкер (bge-reranker-base) НЕ включён** — абляция показала деградацию
  (recall@10 94%→50%), см. `eval/rerank_ablation.py` и PLAN.md (Фаза 7).
- **GitHub токен** нужен только для ингеста; для ответов на готовых данных — нет.
- **Gemini API** не работает из РФ (region-block) — в стеке используется OpenRouter.
- Эмбеддинги: `BAAI/bge-small-en-v1.5` (384 dim), CPU. Реранкер-модель (~1.1 ГБ)
  скачивается в кэш HuggingFace автоматически при первом использовании.
