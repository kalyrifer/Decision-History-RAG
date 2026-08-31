"""Файловый канал ретрива: поиск по путям в files (структура репозитория).

Для вопросов вида «как изменилась структура репозитория» обычный hybrid по тексту
чанков не находит файлы/модули. Этот канал ищет совпадения по files.path
(с учётом old_path ренеймов), находит коммиты, затем через pr_commit — PR,
и через closes/references — issues. Возвращает (eid, score).
"""

import re
import sys

sys.path.insert(0, ".")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_./-]+")
# слова-маркеры «структурных» вопросов: без них канал не активен
_STRUCT_KEYWORDS = {
    "структур", "structure", "layout", "каталог", "directory", "folder",
    "модуль", "module", "пакет", "package", "файл", "file", "tree",
    "располож", "раскладк", "monorepo", "mono-repo", "репозитори", "repo",
    "переимен", "rename", "move", "перенос",
}


def extract_path_tokens(query: str) -> list[str]:
    """Выделить токены-кандидаты, похожие на пути/идентификаторы."""
    tokens = set()
    has_slash = False
    for m in _TOKEN_RE.findall(query.lower()):
        t = m.strip("./-")
        if len(t) < 3:
            continue
        if "/" in t:
            has_slash = True
            tokens.add(t)
        elif t.endswith(".py") or "_" in t:
            tokens.add(t)
    # без явного пути/структурного слова канал молчит (не шумим)
    if not has_slash and not any(k in query.lower() for k in _STRUCT_KEYWORDS):
        return []
    return sorted(tokens)[:12]


def file_path_search(cur, query: str, k: int = 25, min_hits: int = 1) -> list[tuple[int, float]]:
    """Найти issue/pr, связанные с коммитами, трогавшими совпадающие файлы."""
    tokens = extract_path_tokens(query)
    if not tokens:
        return []
    # точный подпуть важнее общего совпадения
    patterns = [f"%{t}%" for t in tokens]
    cur.execute(
        """
        WITH hits AS (
            SELECT entity_id AS cid, count(*) AS hits
            FROM files
            WHERE path ILIKE ANY(%(pats)s) OR old_path ILIKE ANY(%(pats)s)
            GROUP BY entity_id
        )
        SELECT entity_id AS eid, sum(h) AS score FROM (
            SELECT r.src_id AS entity_id, h.hits AS h
            FROM relations r JOIN hits h ON r.dst_id = h.cid
            WHERE r.kind = 'pr_commit'
            UNION ALL
            SELECT h.cid AS entity_id, h.hits AS h FROM hits h
        ) t
        GROUP BY entity_id
        ORDER BY score DESC
        LIMIT %(k)s
        """,
        {"pats": patterns, "k": max(k * 2, 20)},
    )
    out = []
    for eid, score in cur.fetchall():
        if score >= min_hits:
            out.append((eid, float(score)))
    return out
