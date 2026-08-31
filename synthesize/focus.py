"""Анализ фокуса вопроса: лёгкий сигнал для ретрива, НЕ диктатор формата.

Определяет доминирующую тему вопроса (structure / why / what-changed / timeline),
что позволяет:
- поднять вес файлового канала для структурных вопросов;
- передать LLM подсказку о желаемых секциях без жёсткого шаблона.

Важно: это эвристика-подсказка, а не классификатор, жёстко навязывающий формат.
"""

import re

_STRUCT = re.compile(
    r"структур|structure|layout|каталог|directory|folder|модул|module|пакет|package|"
    r"файл|file|tree|располож|раскладк|monorepo|переимен|rename|move|перенос|вынес|"
    r"куда|где лежит|_internal|_core|/",
    re.I,
)
_WHY = re.compile(
    r"почему|зачем|причин|для чего|почему именно|мотивац|reasons?|why\b|rationale|motivation",
    re.I,
)
_WHAT = re.compile(
    r"что измен|какие измен|что появил|что убрал|что добав|как измени|changes?|what (was|were)|"
    r"перепис|заменен|replac|removed?|added?|new (feature|api)|миграц|v1→v2|v1.*v2|различи",
    re.I,
)
_TIMELINE = re.compile(
    r"хронологи|timeline|когда|даты|последовательн|последовательно|в каком порядке|history",
    re.I,
)


def focus_of(question: str) -> dict:
    q = question.lower()
    scores = {
        "structure": len(_STRUCT.findall(q)),
        "why": len(_WHY.findall(q)),
        "what_changed": len(_WHAT.findall(q)),
        "timeline": len(_TIMELINE.findall(q)),
    }
    primary = max(scores, key=scores.get)
    # если совпадений нет вовсе — считаем why (дефолтный decision-reconstruction)
    if sum(scores.values()) == 0:
        primary = "why"
    return {
        "primary": primary,
        "scores": scores,
        "suggested_sections": _sections_for(primary),
    }


def _sections_for(primary: str) -> list[str]:
    return {
        "structure": [
            "Ключевые изменения в структуре репозитория",
            "Вынесенные/новые пакеты и модули",
            "Внутренняя структура (приватные слои, ядро)",
            "Хронология структурных изменений",
            "Источники",
        ],
        "what_changed": [
            "Что изменилось",
            "Ключевые новые/удалённые API",
            "Причины (кратко)",
            "Хронология",
            "Источники",
        ],
        "timeline": [
            "Ключевые события в хронологическом порядке",
            "Что было до / что после",
            "Источники",
        ],
        "why": [
            "Главные причины решения",
            "Хронология",
            "Источники",
        ],
    }[primary]


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    q = "Какие изменения были внесены в структуру репозитория pydantic в v2?"
    print(focus_of(q))