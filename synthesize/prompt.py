"""Decision-reconstruction prompt template for LLM synthesis."""

SYSTEM_PROMPT = """You are an expert at reconstructing software design decisions from GitHub issue/PR/commit history.
You are given evidence blocks (issues, PRs, comments, commits) from a repository's history.
Your task: explain WHY a decision was made and HOW it was implemented — as a clear, well-structured narrative.

STRICT RULES:
1. Answer in the SAME LANGUAGE as the user's question. Russian question -> answer in Russian.
2. ONLY cite entity URLs/IDs that appear in the provided evidence. Never invent facts, URLs, quotes, dates or numbers.
3. Do NOT invent benchmark numbers, performance figures, dates, version numbers or personas not present in the evidence.
4. Quote short excerpts with the author's name when they support a point: "Author: \"quote\""
5. Use markdown formatting: bold section titles, numbered lists for reasons, bullet sub-lists for details, a dated bullet list for the chronology.
6. The Sources section must contain only the 3-8 MOST relevant sources, each with a short description of what it is.
7. If the evidence is insufficient to answer confidently — say so explicitly at the end. Do not guess."""

TEMPLATE = """## Evidence blocks

{evidence}

---

## Question

{question}

---

## Instructions

Compose the answer in the user's language following this structure. Use markdown formatting (bold, numbered lists, bullet lists).

(No header — just write 2-4 sentences as a short strategic summary of what the decision was and the single most important reason)

**Главные причины решения**
Numbered list (1. 2. 3. ...) of the main reasons behind the decision. For each:
- **Bold title of the reason**
- 1-2 sentences of explanation grounded in the evidence
- bullet sub-list for concrete sub-points, features, or consequences

**Хронология**
Bullet list of key events in chronological order, one per line: `date — event (link if available in evidence)`

**Источники**
A focused list of the 3-8 most relevant sources, one per line:
- short description of what the source is — [URL](URL)

Answer:"""


def build_prompt(evidence_blocks: list[dict], question: str) -> str:
    lines = []
    for b in evidence_blocks:
        kind = b.get("kind", "?").upper()
        nid = b.get("native_id", "?")
        url = b.get("url", "")
        title = b.get("title", "")
        author = b.get("author", "")
        created = b.get("created_at", "")[:10] if b.get("created_at") else ""
        text = b.get("text", "")
        hop = b.get("hop", 0)
        hop_tag = f" [hop={hop}]" if hop else ""
        lines.append(
            f"[{kind} {nid}{hop_tag}] {title}\n"
            f"URL: {url}\n"
            f"Author: {author} | Date: {created}\n"
            f"Text:\n{text}\n"
            f"---"
        )
    evidence = "\n\n".join(lines)
    return TEMPLATE.format(evidence=evidence, question=question)
