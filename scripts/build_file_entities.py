"""Построение канонических file-entities + relations touches_file / renamed.

Принцип: каждый файл репозитория представлен ОДНИМ каноническим file-entity.
Rename-цепочки (status='R', old_path->path) объединяются: все пути одного файла
через переименования указывают на один канонический путь (самый поздний).

touches_file: (commit_id -> file_entity_id) — всегда на КАНОНИЧЕСКИЙ file-entity,
чтобы история файла НЕ обрывалась на переименовании.
renamed: (file_entity_id -> file_entity_id) между каноническими путями-соседями
по rename-цепочке.

Запуск:
    python scripts/build_file_entities.py
"""

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PG_DSN

import psycopg
from psycopg.types.json import Json


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class UnionFind:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def main() -> None:
    conn = psycopg.connect(PG_DSN)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 1. собрать все пути и rename-пары
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT path FROM files WHERE path IS NOT NULL AND path <> ''")
        all_paths = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT DISTINCT old_path, path FROM files "
            "WHERE status='R' AND old_path IS NOT NULL AND old_path <> ''"
        )
        rename_pairs = [(o, n) for o, n in cur.fetchall()]
    log(f"путей: {len(all_paths)}, rename-пар: {len(rename_pairs)}")

    # 2. объединить в канонические группы
    uf = UnionFind()
    for p in all_paths:
        uf.find(p)
    for o, n in rename_pairs:
        uf.union(o, n)
    canon = {}
    for p in all_paths:
        root = uf.find(p)
        # канонический путь — «самый поздний» в цепочке = тот, что НЕ является
        # old_path ни для кого (конечная точка); иначе лексически последний.
        if root not in canon:
            canon[root] = p
    # уточнение: предпочесть путь, который не встречается как old_path (конечный)
    old_set = {o for o, _ in rename_pairs}
    by_root: dict[str, list[str]] = defaultdict(list)
    for p in all_paths:
        by_root[uf.find(p)].append(p)
    final_canon: dict[str, str] = {}
    for root, paths in by_root.items():
        ends = [p for p in paths if p not in old_set]
        final_canon[root] = ends[0] if ends else sorted(paths)[-1]
    # map каждого пути -> канонический
    path_to_canon = {p: final_canon[uf.find(p)] for p in all_paths}
    canon_paths = sorted(set(final_canon.values()))
    log(f"канонических файлов: {len(canon_paths)}")

    # 3. создать file-entities
    with conn.cursor() as cur:
        for cp in canon_paths:
            cur.execute(
                "INSERT INTO entities (kind, native_id, title, url) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (kind, native_id) DO NOTHING",
                ("file", cp, cp, None),
            )
        cur.execute("SELECT id, native_id FROM entities WHERE kind='file'")
        file_id = {nid: eid for eid, nid in cur.fetchall()}
    conn.commit()
    log(f"file-entities в БД: {len(file_id)}")

    # 4. touches_file relations (commit_id -> canonical file_id)
    with conn.cursor() as cur:
        cur.execute("SELECT entity_id, path FROM files WHERE path IS NOT NULL AND path <> ''")
        rows = cur.fetchall()
    rels_touches: set[tuple[int, int]] = set()
    missing = 0
    for cid, p in rows:
        cp = path_to_canon.get(p)
        fid = file_id.get(cp) if cp else None
        if fid is None:
            missing += 1
            continue
        rels_touches.add((cid, fid))
    log(f"touches_file пар: {len(rels_touches)} (пропущено: {missing})")

    with conn.cursor() as cur:
        for i, (cid, fid) in enumerate(rels_touches):
            cur.execute(
                "INSERT INTO relations (src_id,dst_id,kind,source,meta) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (src_id,dst_id,kind) DO NOTHING",
                (cid, fid, "touches_file", "api", Json({})),
            )
            if i % 20000 == 0:
                conn.commit()
        conn.commit()

    # 5. renamed relations между каноническими соседями по rename-цепочке
    #    (старый канон -> новый канон; мета хранит конкретную пару путей)
    renamed: dict[tuple[int, int], str] = {}
    for o, n in rename_pairs:
        co = path_to_canon.get(o)
        cn = path_to_canon.get(n)
        if co == cn:
            continue
        a, b = file_id.get(co), file_id.get(cn)
        if a and b and a != b:
            key = (a, b)
            renamed[key] = f"{o} -> {n}"
    log(f"renamed пар (между канонами): {len(renamed)}")

    with conn.cursor() as cur:
        for (a, b), detail in renamed.items():
            cur.execute(
                "INSERT INTO relations (src_id,dst_id,kind,source,meta) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (src_id,dst_id,kind) DO NOTHING",
                (a, b, "renamed", "api", Json({"pair": detail})),
            )
        conn.commit()

    # отчёт
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.kind, count(*) FROM relations r "
            "WHERE r.kind IN ('touches_file','renamed') GROUP BY r.kind ORDER BY r.kind"
        )
        log("разбивка новых relations:")
        for k, c in cur.fetchall():
            log(f"  {k}: {c}")
    conn.close()


if __name__ == "__main__":
    main()