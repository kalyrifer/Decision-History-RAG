import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingest.github_source import (
    _build_batch_query,
    _conn,
    _detail_fields,
    make_client,
    gql,
)

errs = [json.loads(l) for l in open("data/raw/errors.jsonl", encoding="utf-8")]
bad_prs = sorted({n for e in errs if e["stage"] == "pr_merged" for n in e["numbers"]})
print("упавших merged PR:", len(bad_prs), "первые:", bad_prs[:5])

n = bad_prs[0]
print("\n=== полный запрос для 1 merged PR ===")
entries = [("a0", "pullRequest", n, True)]
q = _build_batch_query(entries)
print(q)
print("\n=== отправка ===")
client = make_client()
try:
    data = gql(client, q)
    print("OK, ключи a0:", list(data["repository"]["a0"].keys()))
except Exception as e:
    print("FAIL:", str(e)[:400])

for parts_name, fields in [
    ("только comments", ["comments"]),
    ("comments+timeline", ["comments", "timeline"]),
    ("comments+commits", ["comments", "commits"]),
    ("comments+reviews", ["comments", "reviews"]),
]:
    body = f"a0: pullRequest(number:{n})" + "{" + " ".join(_conn(p, "")[0] for p in fields) + "}"
    qq = "query{rateLimit{cost remaining resetAt} " + f'repository(owner:"pydantic",name:"pydantic")' + "{" + body + "}}"
    try:
        d2 = gql(client, qq)
        print(f"{parts_name}: OK")
    except Exception as e:
        print(f"{parts_name}: FAIL {str(e)[:150]}")
