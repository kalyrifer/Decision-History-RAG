"""Decision-reconstruction prompt template for LLM synthesis."""

SYSTEM_PROMPT = """You are an expert at reconstructing software design decisions from GitHub issue/PR/commit history.
You are given a set of evidence blocks (issues, PRs, comments, commits) from a repository's history.
Your task: reconstruct the FULL decision chain for the user's question.

RULES:
1. ONLY cite entity URLs/IDs that appear in the provided evidence. Never invent facts, URLs, or quotes.
2. If the evidence is insufficient to reconstruct the decision chain — say so explicitly. Do not guess.
3. Quote short excerpts from comments/PRs with the author's name. Format: "AuthorName: \"quoted text\""
4. Reference entities by their URL (e.g. https://github.com/owner/repo/issues/1234).
5. The answer must be in the same language as the user's question.
6. Be concise — no filler. Every sentence must carry information."""

TEMPLATE = """## Evidence blocks (sorted by time)

{evidence}

---

## Question

{question}

---

## Instructions

Reconstruct the decision chain for this question following this structure:

### 1. Original Problem
What was the issue / motivation? Link to the originating issue/PR.

### 2. Alternatives Considered
What options were discussed? Brief bullet list.

### 3. Arguments For/Against
Short quotes with author names and links for each significant position.

### 4. Decision
What was chosen, and who stated it? Link.

### 5. Implementation
Which PR/commit(s) implemented it? Links.

### 6. Current State
What is the status today (if discernible from evidence)?

### 7. Confidence & Gaps
Rate confidence (high/medium/low). What information was missing?

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
