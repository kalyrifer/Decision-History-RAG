"""Сеялка переводов золотых вопросов в дисковый кэш (терпеливо, с паузами против 429)."""

import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from retrieve import query_rewrite as qr

qr.DEADLINE_S = 60.0

gs = yaml.safe_load(open("eval/golden_set.yaml", encoding="utf-8"))
pending = [item["question"] for item in gs]

for attempt in range(1, 6):
    still = []
    for q in pending:
        r = qr.rewrite(q)
        if r["en"] != q:
            print(f"OK: {q[:40]}... -> {r['en'][:70]}")
        else:
            still.append(q)
    if not still:
        break
    pending = still
    print(f"осталось {len(pending)}, жду 45с перед новой попыткой...")
    time.sleep(45)

print("\nитог:", "все вопросы переведены и закэшированы" if not pending else f"не удалось: {len(pending)}")
