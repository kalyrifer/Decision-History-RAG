import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ingest.github_source import _build_batch_query

q = _build_batch_query([("a0", "issue", 7, False), ("a1", "issue", 8, False)])
print("длина:", len(q))
depth = 0
bad = []
instr = False
i = 0
while i < len(q):
    ch = q[i]
    if ch == '"':
        instr = not instr
    elif not instr:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                bad.append(i)
                depth = 0
    i += 1
print("финальная глубина:", depth, "| ранние закрытия в позициях:", bad[:5])
for pos in (4640, 4660, 4680):
    print(f"--- вокруг {pos}: {q[pos-60:pos+40]!r}")
