"""Streamlit UI для Decision History RAG.

Запуск:
    streamlit run ui/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import streamlit as st

st.set_page_config(page_title="Decision History RAG", page_icon="🔍", layout="wide")


def warm_up() -> None:
    if "model_ready" not in st.session_state:
        from retrieve.hybrid import get_model

        get_model()
        st.session_state["model_ready"] = True


def do_search(query: str, no_expand: bool) -> dict:
    from retrieve import pipeline

    res = pipeline.search(query, no_expand=no_expand, rewrite_query=True)
    st.session_state["last_search"] = res
    return res


def main() -> None:
    st.title("🔍 Decision History RAG")
    st.caption("Восстановление цепочки принятия решений из истории pydantic/pydantic")

    with st.sidebar:
        st.header("Настройки")
        no_expand = st.checkbox("Без graph expansion", value=False,
                                help="Отключить расширение по связям (только гибридный поиск)")
        use_llm = st.checkbox("Синтез ответа LLM", value=True,
                              help="Собирать нарратив через OpenRouter :free")
        verbose = st.checkbox("Показать evidence", value=False,
                              help="Показать собранные блоки доказательств")
        if st.button("Прогрев модели"):
            with st.spinner("Загрузка эмбеддинг-модели..."):
                warm_up()
            st.success("Модель прогрета")

    query = st.text_input("Вопрос", placeholder="Например: Почему ядро pydantic переписали с Cython на Rust?")
    col_b, col_c = st.columns([1, 5])
    with col_b:
        go = st.button("Найти / Ответить", type="primary", use_container_width=True)
    with col_c:
        if st.button("Только поиск (без LLM)"):
            go = True
            use_llm = False

    if not query or not go:
        st.info("Задайте вопрос о решениях в истории репозитория.")
        return

    warm_up()
    with st.spinner("Поиск по истории..."):
        res = do_search(query, no_expand)

    st.subheader("Найденные сущности")
    st.caption(f"Время: {res['elapsed_s']}с · fusion: {res['fusion_size']} · "
               f"expansion: {res['expanded_size']} · всего строк: {len(res['rows'])}")
    rows = res["rows"][:30]
    import pandas as pd
    df = pd.DataFrame([
        {
            "тип": r["kind"],
            "источник": "/".join(r.get("channels", [])),
            "хоп": r.get("hop", 0),
            "score": r.get("score") if r.get("score") is not None else r.get("weight"),
            "номер": r["native_id"][:8] if r["kind"] == "commit" else f"#{r['number']}",
            "заголовок": (r.get("title") or "")[:80],
            "комментарии": r.get("comments", 0),
            "url": r.get("url") or "",
        }
        for r in rows
    ])
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("url")})

    if not use_llm:
        return

    with st.spinner("Синтез ответа (OpenRouter :free)..."):
        from synthesize.answer import answer

        try:
            result = answer(res, query, verbose=verbose)
        except Exception as e:
            st.error(f"Ошибка синтеза: {e}")
            return

    st.subheader("Ответ")
    st.markdown(result["answer"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Уверенность", result.get("confidence", "—"))
    c2.metric("Источников", len(result.get("sources", [])))
    c3.metric("Событий timeline", len(result.get("timeline", [])))

    st.subheader("Источники")
    for s in result.get("sources", []):
        role = f" [{s.get('role', '')}]" if s.get("role") else ""
        st.markdown(f"- [{s.get('kind', '')} {s.get('number', '')}]({s['url']}){role}")

    if result.get("timeline"):
        st.subheader("Timeline")
        for t in result["timeline"][:25]:
            title = (t.get("title") or "")[:70]
            st.markdown(f"- **{t['date']}** · {t['kind']} {t['native_id']} — {title}")

    stats = result.get("llm_stats", {})
    if stats:
        st.caption(f"LLM: {stats.get('total_tokens', 0)} токенов")


if __name__ == "__main__":
    main()
