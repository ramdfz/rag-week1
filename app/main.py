from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Generator

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from app.azure_clients import hybrid_retrieve, stream_chat_completion
from app.config import Settings, get_settings
from app.database import (
    add_feedback,
    add_message,
    conversation_exists,
    ensure_conversation,
    format_history_for_prompt,
    get_chunk,
    get_history,
    get_message_by_id,
    get_message_by_index,
    init_chat_tables,
    list_documents,
)
from app.rag import REFUSAL, build_messages, citations_from_chunks, answer_chat, expand_retrieval_query, is_refusal_text, retrieval_is_relevant
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationMessagesResponse,
    CitationDetailResponse,
    FeedbackRequest,
    FeedbackResponse,
    DocumentRecord,
    DocumentsResponse,
    HealthResponse,
    ReindexResponse,
)


settings = get_settings()
init_chat_tables(settings.database_path)
app = FastAPI(title="Meridian Health Partners RAG API", version="0.2.0")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != settings.api_key_auth_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def settings_dep() -> Settings:
    return settings


def document_records() -> list[DocumentRecord]:
    return [
        DocumentRecord(id=row["id"], filename=row["filename"], format=row["format"], chunk_count=row["chunk_count"])
        for row in list_documents(settings.database_path)
    ]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    _auth: None = Depends(require_api_key),
    app_settings: Settings = Depends(settings_dep),
):
    if request.stream:
        return StreamingResponse(stream_chat_response(app_settings, request), media_type="text/event-stream")
    return answer_chat(app_settings, request.message, request.conversation_id)


@app.get(
    "/api/v1/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
    dependencies=[Depends(require_api_key)],
)
def conversation_messages(conversation_id: str) -> ConversationMessagesResponse:
    if not conversation_exists(settings.database_path, conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = get_history(settings.database_path, conversation_id)
    return ConversationMessagesResponse(
        conversation_id=conversation_id,
        messages=[
            ConversationMessage(role=row["role"], content=row["content"], created_at=row["created_at"])
            for row in rows
        ],
    )


@app.post("/api/v1/feedback", response_model=FeedbackResponse, dependencies=[Depends(require_api_key)])
def feedback(request: FeedbackRequest) -> FeedbackResponse:
    row = None
    conversation_id = request.conversation_id
    if request.message_id:
        row = get_message_by_id(settings.database_path, request.message_id)
        if row is not None:
            conversation_id = row["conversation_id"]
    elif request.conversation_id is not None and request.message_index is not None:
        if not conversation_exists(settings.database_path, request.conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        row = get_message_by_index(settings.database_path, request.conversation_id, request.message_index)

    if row is None or conversation_id is None:
        raise HTTPException(status_code=404, detail="Message not found")

    feedback_id = add_feedback(
        settings.database_path,
        conversation_id,
        row["content"],
        request.rating,
        request.note,
    )
    return FeedbackResponse(id=feedback_id, conversation_id=conversation_id, rating=request.rating, saved=True)


def sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def stream_chat_response(app_settings: Settings, request: ChatRequest) -> Generator[str, None, None]:
    conversation_id = ensure_conversation(app_settings.database_path, request.conversation_id)
    retrieval_query = expand_retrieval_query(request.message)
    chunks = hybrid_retrieve(app_settings, retrieval_query, top=5, use_semantic=app_settings.semantic_ranking_enabled)
    if not retrieval_is_relevant(request.message, chunks, app_settings.retrieval_min_score):
        add_message(app_settings.database_path, conversation_id, "user", request.message)
        add_message(app_settings.database_path, conversation_id, "assistant", REFUSAL)
        yield sse("done", {"response": REFUSAL, "citations": [], "conversation_id": conversation_id})
        return

    citations = citations_from_chunks(chunks)
    history_text = format_history_for_prompt(get_history(app_settings.database_path, conversation_id))
    messages = build_messages(request.message, history_text, chunks)
    yield sse("metadata", {"conversation_id": conversation_id, "citations": [citation.model_dump() for citation in citations]})

    collected: list[str] = []
    for token in stream_chat_completion(app_settings, messages):
        collected.append(token)
        yield sse("token", token)

    response_text = "".join(collected)
    if is_refusal_text(response_text):
        citations = []
    add_message(app_settings.database_path, conversation_id, "user", request.message)
    add_message(app_settings.database_path, conversation_id, "assistant", response_text)
    yield sse(
        "done",
        {
            "response": response_text,
            "citations": [citation.model_dump() for citation in citations],
            "conversation_id": conversation_id,
        },
    )


@app.get("/api/v1/documents", response_model=DocumentsResponse, dependencies=[Depends(require_api_key)])
def documents() -> DocumentsResponse:
    return DocumentsResponse(documents=document_records())


@app.post("/api/v1/documents/reindex", response_model=ReindexResponse, dependencies=[Depends(require_api_key)])
def reindex() -> ReindexResponse:
    result = subprocess.run([sys.executable, "ingest.py"], cwd=".", capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr[-2000:] or result.stdout[-2000:])
    init_chat_tables(settings.database_path)
    docs = document_records()
    return ReindexResponse(
        documents=docs,
        total_documents=len(docs),
        total_chunks=sum(document.chunk_count for document in docs),
    )


@app.get("/api/v1/citations/{chunk_id}", response_model=CitationDetailResponse, dependencies=[Depends(require_api_key)])
def citation_detail(chunk_id: str) -> CitationDetailResponse:
    row = get_chunk(settings.database_path, chunk_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return CitationDetailResponse(
        chunk_id=row["id"],
        document=row["document"],
        section=row["section"],
        chunk_index=row["chunk_index"],
        text=row["text"],
    )
