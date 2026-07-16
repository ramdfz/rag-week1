from __future__ import annotations

import json
import time
from collections.abc import Generator
from dataclasses import dataclass

import requests

from app.config import Settings


AZURE_SEARCH_API_VERSION = "2024-07-01"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document: str
    section: str
    chunk_index: int
    text: str
    score: float
    reranker_score: float | None = None


def headers(api_key: str) -> dict[str, str]:
    return {"api-key": api_key, "Content-Type": "application/json"}


def post_with_retry(url: str, api_key: str, body: dict, timeout: int = 90) -> requests.Response:
    response: requests.Response | None = None
    for attempt in range(1, 6):
        response = requests.post(url, headers=headers(api_key), json=body, timeout=timeout)
        if response.status_code not in {429, 500, 502, 503, 504}:
            return response
        time.sleep(min(20, 2**attempt))
    assert response is not None
    return response


def embed(settings: Settings, texts: list[str]) -> list[list[float]]:
    url = f"{settings.azure_openai_endpoint}/embeddings"
    response = post_with_retry(
        url,
        settings.azure_openai_api_key,
        {"model": settings.embedding_deployment, "input": texts},
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Embedding request failed: HTTP {response.status_code} {response.text}")
    data = sorted(response.json()["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def hybrid_retrieve(settings: Settings, query: str, top: int = 5, use_semantic: bool | None = None) -> list[RetrievedChunk]:
    query_embedding = embed(settings, [query])[0]
    url = f"{settings.azure_search_endpoint}/indexes/{settings.azure_search_index_name}/docs/search?api-version={AZURE_SEARCH_API_VERSION}"
    semantic_enabled = settings.semantic_ranking_enabled if use_semantic is None else use_semantic
    body = {
        "search": query,
        "top": 25,
        "select": "id,source_document,section_title,chunk_index,text",
        "vectorQueries": [
            {
                "kind": "vector",
                "vector": query_embedding,
                "fields": "embedding",
                "k": 25,
            }
        ],
    }
    if semantic_enabled:
        body.update(
            {
                "queryType": "semantic",
                "semanticConfiguration": "meridian-semantic-config",
                "captions": "extractive",
            }
        )
    response = post_with_retry(url, settings.azure_search_api_key, body)
    if response.status_code >= 400:
        raise RuntimeError(f"Azure AI Search query failed: HTTP {response.status_code} {response.text}")

    candidates: list[RetrievedChunk] = []
    for item in response.json().get("value", []):
        document = item.get("source_document", "")
        candidates.append(
            RetrievedChunk(
                chunk_id=item["id"],
                document=document,
                section=item.get("section_title", ""),
                chunk_index=int(item.get("chunk_index", 0)),
                text=item.get("text", ""),
                score=float(item.get("@search.score", 0.0)),
                reranker_score=(
                    float(item["@search.rerankerScore"]) if item.get("@search.rerankerScore") is not None else None
                ),
            )
        )

    if not candidates:
        return []

    top_document = candidates[0].document
    selected: list[RetrievedChunk] = []
    document_counts: dict[str, int] = {}
    for chunk in candidates:
        document_limit = 2 if chunk.document == top_document else 1
        if document_counts.get(chunk.document, 0) >= document_limit:
            continue
        selected.append(chunk)
        document_counts[chunk.document] = document_counts.get(chunk.document, 0) + 1
        if len(selected) >= top:
            break
    return selected


def chat_completion(settings: Settings, messages: list[dict[str, str]], deployment: str | None = None) -> tuple[str, dict]:
    url = f"{settings.azure_openai_endpoint}/chat/completions"
    body = {
        "model": deployment or settings.generation_deployment_primary,
        "messages": messages,
    }
    response = post_with_retry(url, settings.azure_openai_api_key, body, timeout=180)
    if response.status_code >= 400:
        raise RuntimeError(f"Chat completion failed: HTTP {response.status_code} {response.text}")
    payload = response.json()
    return payload["choices"][0]["message"]["content"], payload.get("usage", {})


def stream_chat_completion(settings: Settings, messages: list[dict[str, str]]) -> Generator[str, None, dict]:
    url = f"{settings.azure_openai_endpoint}/chat/completions"
    body = {
        "model": settings.generation_deployment_primary,
        "messages": messages,
        "stream": True,
    }
    with requests.post(url, headers=headers(settings.azure_openai_api_key), json=body, timeout=180, stream=True) as response:
        if response.status_code >= 400:
            raise RuntimeError(f"Streaming chat completion failed: HTTP {response.status_code} {response.text}")
        final_payload: dict = {}
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data: "):
                continue
            data = raw_line.removeprefix("data: ").strip()
            if data == "[DONE]":
                break
            payload = json.loads(data)
            final_payload = payload
            choices = payload.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield content
        return final_payload
