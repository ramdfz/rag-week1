from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1)
    stream: bool = False


class Citation(BaseModel):
    document: str
    section: str
    chunk_id: str


class ChatResponse(BaseModel):
    response: str
    citations: list[Citation]
    conversation_id: str


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: int


class ConversationMessagesResponse(BaseModel):
    conversation_id: str
    messages: list[ConversationMessage]


class FeedbackRequest(BaseModel):
    message_id: str | None = None
    conversation_id: str | None = None
    message_index: int | None = Field(default=None, ge=0)
    rating: Literal["up", "down"]
    note: str | None = None


class FeedbackResponse(BaseModel):
    id: str
    conversation_id: str
    rating: Literal["up", "down"]
    saved: bool


class DocumentRecord(BaseModel):
    id: int
    filename: str
    format: str
    chunk_count: int


class DocumentsResponse(BaseModel):
    documents: list[DocumentRecord]


class ReindexResponse(BaseModel):
    documents: list[DocumentRecord]
    total_documents: int
    total_chunks: int


class CitationDetailResponse(BaseModel):
    chunk_id: str
    document: str
    section: str
    chunk_index: int
    text: str


class HealthResponse(BaseModel):
    status: str
