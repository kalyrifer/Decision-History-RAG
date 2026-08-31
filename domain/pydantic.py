"""Domain plugin: словарь per-repo для обогащения поискового запроса.

Это НЕ generic RAG-логика, а специфичный для конкретного репозитория адаптер.
Подключается только когда TARGET_REPO совпадает. Термины добавляются в поиск
как СИГНАЛЫ (кандидаты), а не как утверждения, чтобы не искажать ответ.

Структура словаря:
  {платформа-репозиторий: {шаблон-фразы: [термины-сигналы]}}
"""

import re

# Адаптеры по репозиторию. Ключ — TARGET_REPO, значение — список правил
# (regex-шаблон вопроса, список терминов для поиска).
_ADAPTERS = {
    "pydantic/pydantic": [
        {
            "pattern": re.compile(
                r"v1[^a-z0-9]?v2|переход.*v2|измен.*v2|v2.*измен|миграц|migration|"
                r"разниц.*v1.*v2|new in v2|deprecat",
                re.I,
            ),
            "terms": [
                "field_validator", "model_validator", "model_dump", "model_dump_json",
                "model_config", "TypeAdapter", "field_serializer", "model_serializer",
                "pydantic.v1", "computed_field", "validate_call", "ConfigDict",
                "pydantic-core", "strict mode", "deprecated",
            ],
        },
    ],
}


def signal_terms(query: str, target_repo: str) -> list[str]:
    """Вернуть термины-сигналы для запроса, если репозиторий известен."""
    rules = _ADAPTERS.get(target_repo, [])
    out: list[str] = []
    for rule in rules:
        if rule["pattern"].search(query):
            out.extend(rule["terms"])
    return out
