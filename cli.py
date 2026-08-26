import argparse
import json
import sys
from datetime import datetime

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_ingest_git(_args) -> None:
    from ingest.git_source import ensure_bare_clone, extract

    ensure_bare_clone()
    extract()


def cmd_ingest_inventory(_args) -> None:
    from ingest.github_source import make_client, run_inventory

    run_inventory(make_client())


def cmd_ingest_details(args) -> None:
    from ingest.github_source import make_client, run_details

    run_details(make_client(), max_batches=args.max_batches)


def cmd_status(_args) -> None:
    import pathlib

    from config import TARGET_REPO
    from ingest.storage import CKPT_PATH, count_lines, load_ckpt

    print(f"репозиторий: {TARGET_REPO}")
    for name in ("commits.jsonl", "issues.jsonl", "prs.jsonl", "augmentations.jsonl", "errors.jsonl"):
        print(f"  data/raw/{name}: {count_lines(name)} строк")
    if not CKPT_PATH.exists():
        print("  чекпоинтов ещё нет")
        return
    cp = load_ckpt(TARGET_REPO)
    inv = cp["inventory"]
    d = cp["detail"]
    qi = len(inv.get("issue_numbers", []))
    qpr = len(inv.get("pr_numbers", []))
    seen = set()
    merged_count = 0
    prs_path = pathlib.Path("data/raw/prs.jsonl")
    if prs_path.exists():
        with prs_path.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["number"] in seen:
                    continue
                seen.add(r["number"])
                if r.get("merged_at"):
                    merged_count += 1
    print(
        f"  инвентаризация: {'готова' if inv['done'] else 'в процессе'} "
        f"(issues={qi}, prs={qpr}, из них merged={merged_count})"
    )
    print(
        f"  детали: issues {d['idx_issues']}/{qi}, "
        f"pr_merged {d['idx_pr_merged']}/{merged_count}, "
        f"pr_other {d['idx_pr_other']}/{qpr - merged_count}, батчей: {d['batches_done']}"
    )
    print(f"  обновлён: {cp['updated_at']}")


def cmd_chunks(_args) -> None:
    from represent import to_text

    to_text.main()


def cmd_embed(_args) -> None:
    from represent import embed

    embed.main()


def cmd_normalize(_args) -> None:
    from normalize import extract_relations, parse_raw

    parse_raw.main()
    extract_relations.main()


def main() -> None:
    ap = argparse.ArgumentParser(prog="cli.py", description="Decision History RAG")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest-git", help="клон репо + выгрузка коммитов").set_defaults(func=cmd_ingest_git)
    sub.add_parser("ingest-inventory", help="GraphQL-инвентаризация issues/PR").set_defaults(func=cmd_ingest_inventory)
    p_det = sub.add_parser("ingest-details", help="комментарии/timeline/ревью по батчам")
    p_det.add_argument("--max-batches", type=int, default=None)
    p_det.set_defaults(func=cmd_ingest_details)
    sub.add_parser("normalize", help="сырые данные -> entities/relations/files").set_defaults(func=cmd_normalize)
    sub.add_parser("chunks", help="сущности -> чанки текста").set_defaults(func=cmd_chunks)
    sub.add_parser("embed", help="эмбеддинги чанков + индексы").set_defaults(func=cmd_embed)
    sub.add_parser("status", help="прогресс ингеста").set_defaults(func=cmd_status)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
