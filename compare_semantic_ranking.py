from __future__ import annotations

from dataclasses import dataclass

from app.azure_clients import RetrievedChunk, hybrid_retrieve
from app.config import get_settings
from app.rag import expand_retrieval_query, retrieval_is_relevant


@dataclass(frozen=True)
class Comparison:
    query: str
    plain: RetrievedChunk | None
    semantic: RetrievedChunk | None
    plain_relevant: bool
    semantic_relevant: bool


QUESTIONS = [
    "How many days of PTO do employees get?",
    "What is the deductible on the Bronze plan?",
    "What should I do if I lose my laptop?",
    "Do workers get sick leave, or is illness covered by the time-off policy?",
    "My company computer was stolen. Who do I notify and how quickly?",
    "For the lowest-tier medical insurance option, how much do I pay before coverage starts?",
    "If I see workplace bullying or inappropriate comments, where should I report it?",
    "how do I report a coworker being rude to me",
    "what happens if I damage a company laptop",
    "What is Meridian Health Partners stock price today?",
    "Who won the 2026 World Cup final?",
    "How do I reset my Netflix password?",
    "What is the cafeteria menu on Mars tomorrow?",
]


def label(chunk: RetrievedChunk | None) -> str:
    if chunk is None:
        return "none"
    score = f"{chunk.score:.4f}"
    reranker = f", reranker={chunk.reranker_score:.4f}" if chunk.reranker_score is not None else ""
    return f"{chunk.document} / {chunk.section} / score={score}{reranker}"


def changed(plain: RetrievedChunk | None, semantic: RetrievedChunk | None) -> str:
    if plain is None and semantic is None:
        return "no"
    if plain is None or semantic is None:
        return "yes"
    return "yes" if plain.chunk_id != semantic.chunk_id else "no"


def main() -> None:
    settings = get_settings()
    rows: list[Comparison] = []
    for question in QUESTIONS:
        retrieval_query = expand_retrieval_query(question)
        plain_chunks = hybrid_retrieve(settings, retrieval_query, top=5, use_semantic=False)
        semantic_chunks = hybrid_retrieve(settings, retrieval_query, top=5, use_semantic=True)
        rows.append(
            Comparison(
                query=question,
                plain=plain_chunks[0] if plain_chunks else None,
                semantic=semantic_chunks[0] if semantic_chunks else None,
                plain_relevant=retrieval_is_relevant(question, plain_chunks, settings.retrieval_min_score),
                semantic_relevant=retrieval_is_relevant(question, semantic_chunks, settings.retrieval_min_score),
            )
        )

    print("| Query | Top without semantic | Top with semantic | Order changed? | Gate without/with |")
    print("| --- | --- | --- | --- | --- |")
    for row in rows:
        safe_query = row.query.replace("|", "\\|")
        print(
            f"| {safe_query} | {label(row.plain)} | {label(row.semantic)} | "
            f"{changed(row.plain, row.semantic)} | {row.plain_relevant}/{row.semantic_relevant} |"
        )


if __name__ == "__main__":
    main()
