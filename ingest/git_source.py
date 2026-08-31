"""Выгрузка коммитов из локального bare-клона целевого репозитория."""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from config import REPO_GIT_DIR, TARGET_REPO
from ingest.storage import append_jsonl, load_ckpt, save_ckpt

FIELD_SEP = "\x1f"
REC_SEP = "\x00"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_git(*args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(REPO_GIT_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return res.stdout


def ensure_bare_clone() -> None:
    if REPO_GIT_DIR.exists():
        log(f"bare-клон уже есть: {REPO_GIT_DIR}")
        return
    url = f"https://github.com/{TARGET_REPO}.git"
    log(f"клонирую {url} -> {REPO_GIT_DIR} (это может занять пару минут)")
    subprocess.run(
        ["git", "clone", "--bare", url, str(REPO_GIT_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )
    log("клон готов")


def detect_main_ref() -> str:
    for ref in ("main", "master"):
        try:
            run_git("rev-parse", "--verify", ref)
            return ref
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError("не нашёл ни main, ни master в клоне")


def parse_meta(out: str) -> list[tuple[str, str, str, str, str, str]]:
    records = []
    for rec in out.split(REC_SEP):
        if not rec.strip():
            continue
        f = rec.split(FIELD_SEP)
        if len(f) < 6:
            continue
        sha, parents, author, email, date, subject = f[0], f[1], f[2], f[3], f[4], f[5]
        body = FIELD_SEP.join(f[6:])
        records.append((sha, parents, author, email, date, (subject + "\n\n" + body).strip()))
    return records


def parse_stat_pass(out: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    cur = None
    for line in out.splitlines():
        if SHA_RE.match(line):
            cur = line
            buckets[cur] = []
        elif cur is not None and line.strip():
            buckets[cur].append(line)
    return buckets


def extract() -> int:
    ref = detect_main_ref()
    log(f"реф: {ref}")

    meta_fmt = "%H%x1f%P%x1f%an%x1f%ae%x1f%cI%x1f%s%x1f%b"
    log("проход 1/3: метаданные коммитов")
    metas = parse_meta(run_git("log", ref, f"--pretty=format:{meta_fmt}", "-z"))
    log(f"  коммитов: {len(metas)}")

    log("проход 2/3: numstat (пути + счётчики строк)")
    numstat = parse_stat_pass(
        run_git("log", ref, "--root", "--numstat", "--no-renames", "--pretty=format:%H")
    )
    log("проход 3/3: name-status (статусы A/M/D/R с rename-детекцией)")
    namestat = parse_stat_pass(
        run_git("log", ref, "--root", "--name-status", "--find-renames", "--pretty=format:%H")
    )

    records = []
    for sha, parents_str, author, email, date, message in metas:
        parents = parents_str.split() if parents_str else []
        files = []
        if len(parents) <= 1:
            counts: dict[str, tuple[int | None, int | None]] = {}
            for line in numstat.get(sha, []):
                p = line.split("\t")
                if len(p) >= 3:
                    counts["\t".join(p[2:])] = (
                        None if p[0] == "-" else int(p[0]),
                        None if p[1] == "-" else int(p[1]),
                    )
            for line in namestat.get(sha, []):
                p = line.split("\t")
                if len(p) >= 2:
                    status = p[0]
                    if status.startswith("R") and len(p) >= 3:
                        old_path, new_path = p[1], p[2]
                        add, dele = counts.get(new_path, (None, None))
                        files.append({
                            "path": new_path, "status": "R",
                            "old_path": old_path, "add": add, "del": dele,
                        })
                    else:
                        path = "\t".join(p[1:])
                        add, dele = counts.get(path, (None, None))
                        files.append({"path": path, "status": status, "add": add, "del": dele})
        records.append(
            {
                "kind": "commit",
                "native_id": sha,
                "parents": parents,
                "merge": len(parents) > 1,
                "author": author,
                "email": email,
                "committed_at": date,
                "message": message,
                "files": files,
            }
        )

    append_jsonl("commits.jsonl", records)

    cp = load_ckpt(TARGET_REPO)
    cp["git"]["commits_done"] = True
    save_ckpt(cp)
    log(f"готово: {len(records)} коммитов -> data/raw/commits.jsonl")
    return len(records)


if __name__ == "__main__":
    ensure_bare_clone()
    extract()
