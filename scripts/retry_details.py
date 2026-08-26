"""Дозагрузка сущностей, отсутствующих в augmentations.jsonl."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import TARGET_REPO
from ingest.github_source import (
    _build_batch_query,
    _parse_detail,
    append_jsonl,
    gql,
    load_ckpt,
    log,
    make_client,
    save_ckpt,
)
from ingest.storage import RAW_DIR


def main() -> None:
    have = set()
    aug_path = RAW_DIR / "augmentations.jsonl"
    if aug_path.exists():
        with aug_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    have.add((r["kind"], r["number"]))
                except json.JSONDecodeError:
                    print("битая строка в augmentations.jsonl (пропускаю)")

    cp = load_ckpt(TARGET_REPO)
    inv = cp["inventory"]
    missing_issues = sorted(n for n in set(inv["issue_numbers"]) if ("issue", n) not in have)
    merged_map = {}
    with (RAW_DIR / "prs.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            merged_map[r["number"]] = bool(r.get("merged_at"))
    missing_pm = sorted(n for n in set(inv["pr_numbers"]) if ("pr", n) not in have and merged_map.get(n))
    missing_pu = sorted(n for n in set(inv["pr_numbers"]) if ("pr", n) not in have and not merged_map.get(n))
    log(f"к дозагрузке: issues={len(missing_issues)}, pr_merged={len(missing_pm)}, pr_other={len(missing_pu)}")

    client = make_client()
    stages = [
        ("issue", missing_issues, 12),
        ("pr_merged", missing_pm, 8),
        ("pr_other", missing_pu, 8),
    ]
    done = 0
    total = len(missing_issues) + len(missing_pm) + len(missing_pu)
    for stage_name, queue, batch_size in stages:
        gql_kind = "issue" if stage_name == "issue" else "pullRequest"
        kind_for_rec = "issue" if stage_name == "issue" else "pr"
        still_missing = []
        for i in range(0, len(queue), batch_size):
            chunk = [n for n in queue[i:i + batch_size] if (kind_for_rec, n) not in have]
            if not chunk:
                continue
            entries = [(f"a{j}", kind_for_rec, n, stage_name == "pr_merged") for j, n in enumerate(chunk)]
            q = _build_batch_query(entries)
            try:
                data = gql(client, q)
                repo_node = data["repository"]
                recs = []
                failed = []
                for alias, _k, number, merged in entries:
                    node = repo_node.get(alias)
                    if node is None:
                        failed.append((number, "null"))
                        still_missing.append(number)
                        continue
                    try:
                        recs.append(_parse_detail(kind_for_rec, number, merged, node, client))
                        have.add((kind_for_rec, number))
                    except Exception as e:
                        failed.append((number, f"{type(e).__name__}: {str(e)[:60]}"))
                        still_missing.append(number)
                if recs:
                    append_jsonl("augmentations.jsonl", recs)
                if failed:
                    append_jsonl("errors_retry.jsonl", [{"stage": stage_name, "failed": failed}])
            except Exception as e:
                log(f"батч целиком упал {chunk[:3]}: {str(e)[:120]}")
                still_missing.extend(chunk)
                append_jsonl("errors_retry.jsonl", [{"stage": stage_name, "numbers": chunk, "error": str(e)[:200]}])
            done += len(chunk)
            if done % 96 < batch_size:
                log(f"{stage_name}: {done}/{total}")
        if still_missing:
            log(f"{stage_name}: НЕ ДОСТАЛИ {len(still_missing)}: {still_missing[:10]}")
    log("дозагрузка завершена")


if __name__ == "__main__":
    t0 = time.time()
    main()
    log(f"время: {(time.time() - t0) / 60:.1f} мин")
