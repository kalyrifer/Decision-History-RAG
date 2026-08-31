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


# Дата релизов крупных версий pydantic (доменные данные, НЕ из evidence).
# Используются date_relevance для построения окна релевантности по версиям.
RELEASE_DATES = {
    "1.0": "2019-06-21",
    "1.8": "2021-10-19",
    "1.9": "2022-04-25",
    "1.10": "2023-03-15",
    "2.0": "2023-06-30",
    "2.1": "2023-08-08",
    "2.2": "2023-09-14",
    "2.5": "2024-02-13",
    "2.6": "2024-04-02",
    "2.7": "2024-05-28",
    "2.8": "2024-07-05",
    "2.9": "2024-09-09",
    "2.10": "2024-11-20",
}


def signal_terms(query: str, target_repo: str) -> list[str]:
    """Вернуть термины-сигналы для запроса, если репозиторий известен."""
    rules = _ADAPTERS.get(target_repo, [])
    out: list[str] = []
    for rule in rules:
        if rule["pattern"].search(query):
            out.extend(rule["terms"])
    return out
