import json
import os
from pathlib import Path

RAW_DIR = Path("data") / "raw"
CKPT_PATH = Path("ingest") / "checkpoints.json"

CKPT_DEFAULT = {
    "repo": None,
    "updated_at": None,
    "git": {"commits_done": False},
    "inventory": {"done": False, "issue_cursor": None, "pr_cursor": None},
    "detail": {"idx_issues": 0, "idx_pr_merged": 0, "idx_pr_other": 0, "batches_done": 0},
}


def append_jsonl(name: str, records: list[dict]) -> None:
    path = RAW_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def count_lines(name: str) -> int:
    path = RAW_DIR / name
    if not path.exists():
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)


def load_ckpt(repo: str) -> dict:
    if CKPT_PATH.exists():
        with CKPT_PATH.open(encoding="utf-8") as f:
            cp = json.load(f)
    else:
        cp = {}
    for k, v in CKPT_DEFAULT.items():
        cp.setdefault(k, {})
        if isinstance(v, dict):
            for kk, vv in v.items():
                cp[k].setdefault(kk, vv)
        else:
            cp.setdefault(k, v)
    cp["repo"] = cp["repo"] or repo
    return cp


def save_ckpt(cp: dict) -> None:
    import time
    from datetime import datetime, timezone

    import os

    cp["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = CKPT_PATH.with_suffix(f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=1)
    for attempt in range(5):
        try:
            os.replace(tmp, CKPT_PATH)
            return
        except PermissionError:
            time.sleep(0.3 * (attempt + 1))
    os.replace(tmp, CKPT_PATH)
