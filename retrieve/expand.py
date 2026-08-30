"""Расширение якорей по графу связей: recursive CTE, глубина <=2, приоритет видов рёбер."""

KIND_PRIORITY = {"closes": 1.0, "references": 0.8, "pr_commit": 0.6}

CTE_SQL = """
WITH RECURSIVE walk(node_id, depth, prio, path) AS (
    SELECT id, 0, 1.0, ARRAY[id] FROM entities WHERE id = ANY(%(anchors)s)
  UNION
    SELECT CASE WHEN r.src_id = w.node_id THEN r.dst_id ELSE r.src_id END,
           w.depth + 1,
           CASE r.kind WHEN 'closes' THEN 1.0 WHEN 'references' THEN 0.8 ELSE 0.6 END,
           w.path || (CASE WHEN r.src_id = w.node_id THEN r.dst_id ELSE r.src_id END)
    FROM walk w
    JOIN relations r ON (r.src_id = w.node_id OR r.dst_id = w.node_id)
    WHERE w.depth < %(depth)s
      AND r.kind IN ('closes', 'references', 'pr_commit')
      AND NOT ((CASE WHEN r.src_id = w.node_id THEN r.dst_id ELSE r.src_id END) = ANY(w.path))
)
SELECT node_id, depth, prio FROM walk WHERE depth > 0 LIMIT 20000
"""


def expand(cur, anchor_ids: list[int], max_nodes: int = 40, max_depth: int = 2):
    if not anchor_ids:
        return []
    cur.execute(CTE_SQL, {"anchors": anchor_ids, "depth": max_depth})
    agg = {}
    anchors_set = set(anchor_ids)
    for node_id, depth, prio in cur.fetchall():
        if node_id in anchors_set:
            continue
        prev = agg.get(node_id)
        if prev is None or depth < prev[0]:
            agg[node_id] = (depth, prio)
        elif depth == prev[0] and prio > prev[1]:
            agg[node_id] = (depth, prio)
    scored = [
        (node_id, depth, float(prio) * (0.5 ** (depth - 1)))
        for node_id, (depth, prio) in agg.items()
    ]
    scored.sort(key=lambda x: -x[2])
    return scored[:max_nodes]
