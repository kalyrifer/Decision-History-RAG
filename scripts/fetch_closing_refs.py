"""Докачка closingIssuesReferences для всех merged PR (каноничные closes-связи GitHub)."""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import TARGET_REPO
from ingest.github_source import ensure_budget, gql, log, make_client, _update_rate
from ingest.storage import RAW_DIR

OUT = RAW_DIR / "closing_refs.jsonl"
BATCH = 16


def main() -> None:
    merged = []
    seen = set()
    for line in (RAW_DIR / "prs.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["number"] not in seen and r.get("merged_at"):
            seen.add(r["number"])
            merged.append(r["number"])

    done = set()
    if OUT.exists():
        for line in OUT.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["number"])
            except json.JSONDecodeError:
                pass

    todo = [n for n in sorted(merged) if n not in done]
    log(f"merged PR: {len(merged)}, уже есть: {len(done)}, к выгрузке: {len(todo)}")
    if not todo:
        return

    client = make_client()
    out_f = OUT.open("a", encoding="utf-8")
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        body = " ".join(
            f"a{j}: pullRequest(number:{n}){{closingIssuesReferences(first:25){{nodes{{number}}}}}}"
            for j, n in enumerate(chunk)
        )
        q = (
            "query{rateLimit{cost remaining resetAt} "
            f'repository(owner:"pydantic",name:"pydantic"){{{body}}}}}'
        )
        ensure_budget(len(chunk) + 3)
        data = gql(client, q)
        _update_rate(data.get("rateLimit"))
        repo = data["repository"]
        for j, n in enumerate(chunk):
            node = repo.get(f"a{j}")
            nums = []
            if node and node.get("closingIssuesReferences"):
                nums = [x["number"] for x in node["closingIssuesReferences"]["nodes"]]
            out_f.write(json.dumps({"number": n, "closing": nums}, ensure_ascii=False) + "\n")
        out_f.flush()
        if (i // BATCH) % 25 == 0:
            log(f"выгружено {min(i + BATCH, len(todo))}/{len(todo)}, rem={data['rateLimit']['remaining']}")
    out_f.close()
    log("closing_refs готовы")


if __name__ == "__main__":
    main()
