# Decision History RAG

RAG-система, восстанавливающая цепочку принятия инженерных решений из истории
GitHub-репозитория (issue → альтернативы → аргументы → решение → PR/commit)
по вопросу вида «Почему выбрали X?».

Целевой репозиторий: pydantic/pydantic. Стек и план: см. PLAN.md.

## Быстрый старт

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # затем вписать GITHUB_TOKEN и GEMINI_API_KEY
docker compose up -d
python scripts\check_env.py
```
