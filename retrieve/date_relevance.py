"""Date-relevance: извлечение версионных токенов из вопроса + окно релевантности.

Generic-механика: извлекает версионные токены (v1, v2, 1.0, 2.0, "pydantic 2", etc.)
и сопоставляет с датами релизов через доменный справочник (domain/...).
Возвращает вес для даты события: 1.0 внутри окна, меньше вне.
"""

import re

from config import TARGET_REPO

_VERS = re.compile(r"(?:v|version\s*)?(\d+(?:\.\d+)?)", re.I)
_ORD = re.compile(r"перв(?:ая|ой|ого|ый)|втор(?:ая|ой|ого|ой)|втор(?:ой|ая|ое)", re.I)
_VERBAL = {"перв": "1.0", "втор": "2.0", "первая": "1.0", "вторая": "2.0"}

# padding: расширяем окно на N дней до и после первой/последней даты
WINDOW_PAD_DAYS = 90


def _release_dates() -> dict:
    """Даты релизов из доменного справочника (pydantic-специфично)."""
    try:
        from domain.pydantic import RELEASE_DATES

        return RELEASE_DATES
    except Exception:
        return {}


def _resolve_domain_versions(tokens: set[str]) -> list[str]:
    """Сопоставить токены с версиями из доменного справочника."""
    release = _release_dates()
    matched = []
    for t in tokens:
        if t in release:
            matched.append(t)
        else:
            major = t.split(".")[0]
            for v in release:
                if v.split(".")[0] == major:
                    matched.append(v)
                    break
    unique = list(set(matched))
    # для одиночной версии — расширить до предыдущей мажорной
    if len(unique) == 1:
        this_major = int(unique[0].split(".")[0])
        for v in release:
            other_major = int(v.split(".")[0])
            if other_major < this_major:
                unique.append(v)
                break
    return sorted(set(unique), key=lambda x: float(x))


def _window_from_versions(versions: list[str]) -> tuple[str, str] | None:
    from datetime import datetime, timedelta

    release = _release_dates()
    dates = []
    for v in versions:
        d = release.get(v)
        if d:
            dates.append(datetime.strptime(d, "%Y-%m-%d"))
    if not dates:
        return None
    mn = min(dates) - timedelta(days=WINDOW_PAD_DAYS)
    mx = max(dates) + timedelta(days=WINDOW_PAD_DAYS)
    return (mn.strftime("%Y-%m-%d"), mx.strftime("%Y-%m-%d"))


def extract_window(question: str) -> tuple[str, str] | None:
    """Вернуть (start_date, end_date) окна релевантности или None."""
    tokens = set()
    for m in _VERS.finditer(question):
        t = m.group(1)
        if t:
            tokens.add(t)
    for m in _ORD.finditer(question):
        word = m.group(0).lower()
        mapped = _VERBAL.get(word, _VERBAL.get(word[:4]))
        if mapped:
            tokens.add(mapped)
    if not tokens:
        return None
    versions = _resolve_domain_versions(tokens)
    if not versions:
        return None
    return _window_from_versions(versions)


def date_weight(created_at: str | None, window: tuple[str, str] | None) -> float:
    """Вес для даты события: 1.0 в окне, 0.3 на границе, 0.05 вне."""
    if not created_at or not window:
        return 1.0
    from datetime import datetime

    try:
        dt = datetime.strptime(created_at[:10], "%Y-%m-%d")
        lo = datetime.strptime(window[0], "%Y-%m-%d")
        hi = datetime.strptime(window[1], "%Y-%m-%d")
    except (ValueError, IndexError):
        return 1.0
    from datetime import timedelta

    margin = timedelta(days=WINDOW_PAD_DAYS)
    if lo - margin <= dt <= hi + margin:
        return 1.0
    if lo - margin * 2 <= dt <= hi + margin * 2:
        return 0.3
    return 0.05