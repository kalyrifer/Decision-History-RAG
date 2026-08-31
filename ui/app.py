"""Streamlit UI для Decision History RAG.

Запуск:
    streamlit run ui/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Decision History RAG",
    page_icon=":material/history:",
    layout="wide",
    initial_sidebar_state="expanded",
)

KIND_LABEL = {
    "issue": "Issue",
    "pr": "PR",
    "commit": "Commit",
    "comment": "Comment",
}


def kind_label(kind: str) -> str:
    return KIND_LABEL.get(kind, kind)


def warm_up() -> bool:
    if "model_ready" not in st.session_state:
        from retrieve.hybrid import get_model

        get_model()
        st.session_state["model_ready"] = True
    return st.session_state["model_ready"]


def do_search(query: str, no_expand: bool) -> dict:
    from retrieve import pipeline

    res = pipeline.search(query, no_expand=no_expand, rewrite_query=True)
    st.session_state["last_search"] = res
    return res


def run_llm(res: dict, query: str, verbose: bool) -> dict:
    from synthesize.answer import answer

    return answer(res, query, verbose=verbose)


def render_entities(res: dict) -> None:
    rows = res["rows"][:40]
    if not rows:
        st.caption("Ничего не найдено — попробуйте переформулировать запрос.")
        return

    df = pd.DataFrame(
        [
            {
                "Тип": kind_label(r["kind"]),
                "Номер": r["native_id"][:8] if r["kind"] == "commit" else f"#{r['number']}",
                "Заголовок": (r.get("title") or "")[:90],
                "Score": (
                    f"{r['score']:.4f}"
                    if r.get("score") is not None
                    else f"w={r.get('weight'):.3f}"
                ),
                "Хоп": r.get("hop", 0),
                "Комментарии": r.get("comments", 0),
                "URL": r.get("url") or "",
            }
            for r in rows
        ]
    )
    st.dataframe(
        df,
        column_config={
            "Тип": st.column_config.TextColumn(
                "Тип", help="Тип сущности GitHub: issue / PR / commit / comment"
            ),
            "Score": st.column_config.TextColumn(
                "Score", help="fusion-score; для graph-расширения — вес связи (w=…)"
            ),
            "Хоп": st.column_config.NumberColumn("Хоп", help="0 — прямое совпадение, >0 — расширение по связям"),
            "Комментарии": st.column_config.NumberColumn("Комментарии"),
            "URL": st.column_config.LinkColumn("Ссылка"),
        },
        hide_index=True,
        width="stretch",
    )


def render_timeline(res: dict, result: dict | None) -> None:
    timeline = []
    if result is not None and result.get("timeline"):
        timeline = result["timeline"]
    elif not timeline and res.get("rows"):
        by_date = {}
        for r in res["rows"]:
            d = (r.get("created_at") or "")[:10]
            if r["kind"] not in ("issue", "pr", "commit") or not d:
                continue
            by_date.setdefault(d, []).append(r)
        for d in sorted(by_date):
            for r in by_date[d]:
                timeline.append(
                    {
                        "date": d,
                        "kind": r["kind"],
                        "native_id": r["native_id"],
                        "title": r.get("title"),
                    }
                )

    if not timeline:
        st.caption("Timeline не построен.")
        return

    for t in timeline[:40]:
        title = (t.get("title") or "")[:80]
        badge = {
            "issue": ":blue-badge[Issue]",
            "pr": ":violet-badge[PR]",
            "commit": ":green-badge[Commit]",
        }.get(t.get("kind"), t.get("kind"))
        st.markdown(f"**{t['date']}** · {badge} `{t.get('native_id', '')}` — {title}")


def render_sources(result: dict | None) -> None:
    if result is None:
        st.caption("Синтез ответа выключен — источники не собирались.")
        return
    sources = result.get("sources", [])
    if not sources:
        st.caption("Источники не найдены.")
        return
    for s in sources:
        role_badge = (
            ":green-badge[cited]"
            if s.get("role") == "cited"
            else ":gray-badge[retrieved]"
        )
        title = f" — {s.get('title', '')[:70]}" if s.get("title") else ""
        st.markdown(
            f"- {role_badge} [`{s.get('kind', '')} #{s.get('number', '')}`]"
            f"({s['url']}){title}"
        )


def render_details(res: dict, result: dict | None) -> None:
    c1, c2, c3 = st.columns(3)
    if result is not None:
        confidence = result.get("confidence", "—")
        conf_badge = {
            "high": ":green-badge[высокая]",
            "medium": ":orange-badge[средняя]",
            "low": ":red-badge[низкая]",
        }.get(confidence, confidence)
        c1.metric("Уверенность", conf_badge)
        c2.metric("Источников", len(result.get("sources", [])))
        c3.metric("Событий timeline", len(result.get("timeline", [])))
        stats = result.get("llm_stats", {})
        if stats:
            st.caption(f"LLM: {stats.get('total_tokens', 0)} токенов · {stats.get('elapsed_s', '—')} с")
    else:
        c1.metric("Найдено сущностей", len(res.get("rows", [])))
        c2.metric("Fusion", res.get("fusion_size", 0))
        c3.metric("Graph expansion", res.get("expanded_size", 0))


# ---------------------------------------------------------------- header
st.title("Decision History RAG")
st.caption(
    "Реконструкция цепочки принятия решений из истории **pydantic/pydantic** "
    "— гибридный поиск по issues, PR и commits"
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Настройки", divider=True)
    graph_expand = st.toggle(
        "Расширение по связям",
        value=True,
        help="Добавлять связанные сущности (graph expansion) к результатам гибридного поиска",
    )
    use_llm = st.toggle(
        "Синтез ответа LLM",
        value=True,
        help="Собирать связный нарратив через OpenRouter (:free) по найденным evidence",
    )
    verbose = st.checkbox(
        "Показать evidence",
        value=False,
        help="Выводить отладочную информацию о собранных блоках доказательств",
    )

    st.space("medium")
    with st.container(border=True):
        st.markdown(":material/bolt: **Прогрев модели**")
        st.caption("Загрузить эмбеддинг-модель в память (первый запуск — долгий)")
        if st.button(
            "Прогреть", icon=":material/local_fire_department:", type="secondary", width="stretch"
        ):
            with st.status("Загрузка эмбеддинг-модели...", expanded=True) as s:
                warm_up()
                s.update(label="Модель загружена", state="complete", expanded=False)
            st.toast("Модель прогрета", icon=":material/check_circle:")

    st.space("large")
    st.caption("🔬 Decision History RAG · версия 1.0")
    st.caption("Поиск: pgvector + Postgres FTS · RRF fusion")

# ---------------------------------------------------------------- search
with st.container(border=True):
    query = st.text_input(
        "Вопрос",
        placeholder="Например: Почему ядро pydantic переписали с Cython на Rust?",
        label_visibility="collapsed",
    )
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        go = st.button(
            "Найти / Ответить",
            type="primary",
            icon=":material/search:",
            width="content",
        )
        if st.button(
            "Только поиск",
            icon=":material/travel_explore:",
            type="secondary",
            width="content",
        ):
            go = True
            use_llm = False

if not query.strip() or not go:
    with st.container(border=True):
        st.markdown(
            ":material/help: **Как пользоваться**\n\n"
            "Задайте вопрос о решениях в истории репозитория — например, "
            "«Почему переход на Rust?», «Кто внёс изменения в валидацию?», "
            "«Когда появился pydantic-core?»"
        )
        st.caption(
            "Нажмите «Найти / Ответить», чтобы запустить поиск и (по желанию) синтез ответа."
        )
    st.stop()

# ---------------------------------------------------------------- run search
if not warm_up():
    with st.status("Загрузка эмбеддинг-модели...", expanded=False) as s:
        warm_up()
        s.update(label="Модель загружена", state="complete")

with st.status("Поиск по истории...", expanded=True) as search_status:
    try:
        res = do_search(query.strip(), no_expand=not graph_expand)
    except Exception as e:
        search_status.update(label="Ошибка поиска", state="error")
        st.error(f"Ошибка поиска: {e}")
        st.stop()
    search_status.update(
        label=f"Найдено {len(res['rows'])} сущностей за {res['elapsed_s']} с",
        state="complete",
        expanded=False,
    )

# ---------------------------------------------------------------- results
result = None
if use_llm:
    with st.status("Синтез ответа (OpenRouter :free)...", expanded=True) as llm_status:
        try:
            result = run_llm(res, query.strip(), verbose=verbose)
        except Exception as e:
            llm_status.update(label="Ошибка синтеза", state="error")
            st.error(f"Ошибка синтеза: {e}")
            result = None
    if result is not None:
        llm_status.update(label="Ответ готов", state="complete", expanded=False)

st.space("small")

# answer block
if result is not None:
    with st.container(border=True):
        st.markdown("### :material/chat: Ответ")
        st.markdown(result["answer"])

st.space("small")

# tabs
tab_entities, tab_tl, tab_src, tab_det = st.tabs(
    [
        ":material/table_rows: Evidence",
        ":material/commit: Timeline",
        ":material/link: Источники",
        ":material/tune: Тех. детали",
    ]
)

with tab_entities:
    st.caption(
        f"Время: {res['elapsed_s']} с · fusion: {res['fusion_size']} · "
        f"expansion: {res['expanded_size']} · всего строк: {len(res['rows'])}"
    )
    render_entities(res)

with tab_tl:
    render_timeline(res, result)

with tab_src:
    render_sources(result)

with tab_det:
    render_details(res, result)
