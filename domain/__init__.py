"""Domain plugins: per-repo vocabulary adapters (не generic-логика).

См. domain/pydantic.py — словарь терминов для pydantic/pydantic.
"""

from config import TARGET_REPO

from .pydantic import signal_terms as _pydantic_signal_terms

_PLUGINS = {
    "pydantic/pydantic": _pydantic_signal_terms,
}


def signal_terms(query: str, target_repo: str | None = None) -> list[str]:
    fn = _PLUGINS.get(target_repo or TARGET_REPO)
    if fn is None:
        return []
    return fn(query, target_repo or TARGET_REPO)
