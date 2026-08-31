"""Decision-reconstruction prompt template for LLM synthesis."""

SYSTEM_PROMPT = """You are an expert at reconstructing software design decisions from GitHub issue/PR/commit history.
You are given evidence blocks (issues, PRs, comments, commits) from a repository's history.
Your task: respond to the user's question with a clear, well-structured, factual answer.

STRICT RULES:
1. Answer in the SAME LANGUAGE as the user's question. Russian question -> answer in Russian.
2. ONLY cite entity URLs/IDs that appear in the provided evidence. Never invent facts, URLs, quotes, dates or numbers.
3. Do NOT invent benchmark numbers, performance figures, dates, version numbers or personas not present in the evidence.
4. Quote short excerpts with the author's name when they support a point: "Author: \"quote\""
5. Use markdown formatting: bold section titles, numbered lists, bullet lists, dated lists.
6. The Sources section must contain only the 3-8 MOST relevant sources, each with a short description of what it is.
7. If the evidence is insufficient to answer confidently — say so explicitly at the end. Do not guess."""

TEMPLATE = """## Evidence blocks

{evidence}

---

## Question

{question}

---

## Instructions

Compose the answer in the user's language. Derive the section structure from the QUESTION itself — do not use a fixed template.
The sections should be what makes sense for the specific question asked.

For example:
- If the question is about WHY a decision was made: include a strategic summary, main reasons, chronology, and sources.
- If the question is about STRUCTURE of the repository: include sections about modules, moved packages, internal layout, naming changes.
- If the question is about WHAT CHANGED: focus on the differences, new/removed features, migration impact.
- If the question is about TIMELINE: emphasize the chronology of key events.

Use markdown formatting throughout. Start with a short strategic summary (2-4 sentences).
Include a **Sources** section at the end with 3-8 most relevant links.
Always include a **Хронология** (Chronology) section with dated events in order when possible.

Answer:"""


def build_prompt(evidence_blocks: list[dict], question: str, focus: str | None = None) -> str:
    from .focus import focus_of

    if focus is None:
        focus = focus_of(question)["primary"]
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
