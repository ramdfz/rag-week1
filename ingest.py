from __future__ import annotations

import argparse
import hashlib
import logging
import os
import random
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import requests
from docx import Document
from dotenv import load_dotenv

try:
    import tiktoken
except ImportError:  # pragma: no cover - fallback is for minimal local installs
    tiktoken = None


LOGGER = logging.getLogger("meridian_ingest")
SUPPORTED_CORPUS = {
    "policies": ".docx",
    "benefits": ".pdf",
    "procedures": ".md",
}
EMBEDDING_DIMENSIONS = 3072
AZURE_SEARCH_API_VERSION = "2024-07-01"
CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+|\d+\.)\s+(.+)")
ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,/&()'-]{8,}$")
SECTION_HEADING_RE = re.compile(r"^(?:section\s+)?(?:\d+\.|\d+\.\d+(?:\.\d+)*\.?)\s+.+", re.I)


@dataclass
class Section:
    title: str
    text: str
    page: int | None = None


@dataclass
class Chunk:
    id: str
    filename: str
    format: str
    section_title: str
    chunk_index: int
    text: str


def token_counter():
    if tiktoken is None:
        return lambda text: max(1, int(len(re.findall(r"\S+", text)) * 1.3))
    encoding = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(encoding.encode(text))


count_tokens = token_counter()


def normalize_space(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean_title(title: str) -> str:
    title = re.sub(r"^#{1,6}\s*", "", title).strip()
    return title or "Untitled Section"


def is_numbered_clause(line: str) -> bool:
    return bool(CLAUSE_RE.match(line.strip()))


def looks_like_pdf_heading(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 4 or len(stripped) > 120:
        return False
    if stripped.endswith(".") and len(stripped.split()) > 8:
        return False
    return bool(ALL_CAPS_HEADING_RE.match(stripped) or SECTION_HEADING_RE.match(stripped))


def is_noisy_pdf_title(line: str) -> bool:
    stripped = re.sub(r"\s+", " ", line).strip()
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", stripped)
    has_clause_structure = bool(SECTION_HEADING_RE.match(stripped) or CLAUSE_RE.match(stripped))
    if not stripped:
        return True
    if re.fullmatch(r"[\d\s\-–—/]+", stripped):
        return True
    if has_clause_structure:
        return False
    if len(words) < 4:
        return True
    if stripped.isupper() and len(words) <= 4:
        return True
    return False


def add_section(sections: list[Section], title: str, lines: list[str], page: int | None = None) -> None:
    text = normalize_space("\n".join(lines))
    if text:
        sections.append(Section(title=clean_title(title), text=text, page=page))


def parse_docx(path: Path) -> list[Section]:
    document = Document(path)
    sections: list[Section] = []
    current_title = path.stem
    current_lines: list[str] = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue

        style_name = (paragraph.style.name if paragraph.style else "").lower()
        is_heading = style_name.startswith("heading") or style_name in {"title", "subtitle"}
        if is_heading or is_numbered_clause(text):
            add_section(sections, current_title, current_lines)
            current_title = text
            current_lines = [text]
        else:
            current_lines.append(text)

    add_section(sections, current_title, current_lines)
    return sections


def parse_pdf(path: Path) -> list[Section]:
    sections: list[Section] = []
    current_title = "Page 1"
    current_lines: list[str] = []
    current_page: int | None = None

    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if current_page != page_number:
                add_section(sections, current_title, current_lines, current_page)
                current_title = f"Page {page_number}"
                current_lines = [f"[Page {page_number}]"]
                current_page = page_number
            for line in lines:
                if (is_numbered_clause(line) or looks_like_pdf_heading(line)) and not is_noisy_pdf_title(line):
                    add_section(sections, current_title, current_lines, current_page)
                    current_title = line
                    current_lines = [line]
                else:
                    current_lines.append(line)

    add_section(sections, current_title, current_lines, current_page)
    return sections


def parse_markdown(path: Path) -> list[Section]:
    sections: list[Section] = []
    current_title = path.stem
    current_lines: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if re.match(r"^#{1,6}\s+", line) or is_numbered_clause(line):
            add_section(sections, current_title, current_lines)
            current_title = line
            current_lines = [line]
        else:
            current_lines.append(line)

    add_section(sections, current_title, current_lines)
    return sections


def parse_document(path: Path) -> list[Section]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".md":
        return parse_markdown(path)
    raise ValueError(f"Unsupported file format: {path}")


def split_section_into_units(section: Section) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", section.text) if block.strip()]
    units: list[str] = []
    buffer: list[str] = []

    for block in blocks:
        lines = block.splitlines()
        if lines and is_numbered_clause(lines[0]) and buffer:
            units.append("\n".join(buffer).strip())
            buffer = [block]
        else:
            buffer.append(block)

    if buffer:
        units.append("\n".join(buffer).strip())
    return units or [section.text]


def chunk_sections(
    filename: str,
    doc_format: str,
    sections: list[Section],
    target_min: int = 400,
    target_max: int = 600,
    overlap_ratio: float = 0.15,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    previous_tail = ""

    def make_chunk(section_title: str, parts: list[str]) -> None:
        nonlocal previous_tail
        body = normalize_space("\n\n".join(part for part in parts if part.strip()))
        if not body:
            return
        text = normalize_space(f"{previous_tail}\n\n{body}" if previous_tail else body)
        chunk_index = len(chunks)
        chunk_id = hashlib.sha256(f"{filename}:{chunk_index}:{text[:80]}".encode("utf-8")).hexdigest()
        chunks.append(
            Chunk(
                id=chunk_id,
                filename=filename,
                format=doc_format,
                section_title=section_title,
                chunk_index=chunk_index,
                text=text,
            )
        )
        words = body.split()
        tail_words = max(1, int(len(words) * overlap_ratio))
        previous_tail = " ".join(words[-tail_words:]) if len(words) > 20 else body

    for section in sections:
        units = split_section_into_units(section)
        current_parts: list[str] = []
        current_tokens = 0

        for unit in units:
            unit_tokens = count_tokens(unit)
            if current_parts and current_tokens + unit_tokens > target_max:
                make_chunk(section.title, current_parts)
                current_parts = []
                current_tokens = 0

            if unit_tokens > target_max:
                paragraphs = [p.strip() for p in unit.splitlines() if p.strip()]
                sub_parts: list[str] = []
                sub_tokens = 0
                for paragraph in paragraphs:
                    paragraph_tokens = count_tokens(paragraph)
                    if sub_parts and sub_tokens + paragraph_tokens > target_max:
                        make_chunk(section.title, sub_parts)
                        sub_parts = []
                        sub_tokens = 0
                    sub_parts.append(paragraph)
                    sub_tokens += paragraph_tokens
                if sub_parts:
                    if current_parts and current_tokens + sub_tokens > target_max:
                        make_chunk(section.title, current_parts)
                        current_parts = []
                        current_tokens = 0
                    current_parts.extend(sub_parts)
                    current_tokens += sub_tokens
            else:
                current_parts.append(unit)
                current_tokens += unit_tokens

            if current_tokens >= target_min:
                make_chunk(section.title, current_parts)
                current_parts = []
                current_tokens = 0

        if current_parts:
            make_chunk(section.title, current_parts)

    return chunks


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.rstrip("/")


def azure_headers(api_key: str) -> dict[str, str]:
    return {
        "api-key": api_key,
        "Content-Type": "application/json",
    }


def request_with_retry(method: str, url: str, *, headers: dict[str, str], json_body: dict, timeout: int = 60) -> requests.Response:
    for attempt in range(1, 7):
        response = requests.request(method, url, headers=headers, json=json_body, timeout=timeout)
        if response.status_code not in {429, 500, 502, 503, 504}:
            return response
        sleep_for = min(30, (2**attempt) + random.random())
        LOGGER.warning("Transient HTTP %s from %s; retrying in %.1fs", response.status_code, url, sleep_for)
        time.sleep(sleep_for)
    return response


def create_search_index(search_endpoint: str, search_key: str, index_name: str) -> None:
    url = f"{search_endpoint}/indexes/{index_name}?api-version={AZURE_SEARCH_API_VERSION}"
    delete_response = requests.delete(url, headers=azure_headers(search_key), timeout=60)
    if delete_response.status_code not in {204, 404}:
        raise RuntimeError(f"Failed to clear existing Azure AI Search index: HTTP {delete_response.status_code} {delete_response.text}")

    schema = {
        "name": index_name,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "text", "type": "Edm.String", "searchable": True, "retrievable": True, "analyzer": "en.microsoft"},
            {"name": "embedding", "type": "Collection(Edm.Single)", "searchable": True, "retrievable": False, "dimensions": EMBEDDING_DIMENSIONS, "vectorSearchProfile": "hnsw-profile"},
            {"name": "source_document", "type": "Edm.String", "filterable": True, "facetable": True, "searchable": True, "retrievable": True},
            {"name": "section_title", "type": "Edm.String", "filterable": True, "searchable": True, "retrievable": True},
            {"name": "chunk_index", "type": "Edm.Int32", "filterable": True, "sortable": True, "retrievable": True},
            {"name": "format", "type": "Edm.String", "filterable": True, "facetable": True, "retrievable": True},
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw-config", "kind": "hnsw"}],
            "profiles": [{"name": "hnsw-profile", "algorithm": "hnsw-config"}],
        },
    }
    response = request_with_retry("PUT", url, headers=azure_headers(search_key), json_body=schema)
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to create/update Azure AI Search index: HTTP {response.status_code} {response.text}")


def embed_texts(openai_endpoint: str, openai_key: str, deployment: str, texts: list[str], batch_size: int = 16) -> list[list[float]]:
    embeddings: list[list[float]] = []
    url = f"{openai_endpoint}/embeddings"
    headers = azure_headers(openai_key)

    for offset in range(0, len(texts), batch_size):
        batch = texts[offset : offset + batch_size]
        body = {"model": deployment, "input": batch}
        response = request_with_retry("POST", url, headers=headers, json_body=body, timeout=120)
        if response.status_code >= 400:
            LOGGER.error("Embedding batch %s-%s failed: HTTP %s %s", offset, offset + len(batch) - 1, response.status_code, response.text)
            raise RuntimeError(f"Embedding failed for batch beginning at chunk {offset}")
        payload = response.json()
        returned = sorted(payload["data"], key=lambda item: item["index"])
        embeddings.extend(item["embedding"] for item in returned)

    if len(embeddings) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
    return embeddings


def upload_chunks(search_endpoint: str, search_key: str, index_name: str, chunks: list[Chunk], embeddings: list[list[float]], batch_size: int = 100) -> None:
    url = f"{search_endpoint}/indexes/{index_name}/docs/index?api-version={AZURE_SEARCH_API_VERSION}"
    for offset in range(0, len(chunks), batch_size):
        actions = []
        for chunk, embedding in zip(chunks[offset : offset + batch_size], embeddings[offset : offset + batch_size], strict=True):
            actions.append(
                {
                    "@search.action": "mergeOrUpload",
                    "id": chunk.id,
                    "text": chunk.text,
                    "embedding": embedding,
                    "source_document": chunk.filename,
                    "section_title": chunk.section_title,
                    "chunk_index": chunk.chunk_index,
                    "format": chunk.format,
                }
            )
        response = request_with_retry("POST", url, headers=azure_headers(search_key), json_body={"value": actions}, timeout=120)
        if response.status_code >= 400:
            raise RuntimeError(f"Azure AI Search upload failed: HTTP {response.status_code} {response.text}")


def write_sqlite(db_path: Path, chunks_by_doc: dict[Path, list[Chunk]]) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DROP TABLE IF EXISTS chunks")
        connection.execute("DROP TABLE IF EXISTS documents")
        connection.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                format TEXT NOT NULL,
                chunk_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY,
                document_id INTEGER NOT NULL,
                section_title TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id)
            )
            """
        )

        for path, chunks in chunks_by_doc.items():
            cursor = connection.execute(
                "INSERT INTO documents (filename, format, chunk_count) VALUES (?, ?, ?)",
                (path.name, path.suffix.lower().lstrip("."), len(chunks)),
            )
            document_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO chunks (id, document_id, section_title, chunk_index, text) VALUES (?, ?, ?, ?, ?)",
                [(chunk.id, document_id, chunk.section_title, chunk.chunk_index, chunk.text) for chunk in chunks],
            )
        connection.commit()
    finally:
        connection.close()


def discover_documents(corpus_root: Path) -> list[Path]:
    documents: list[Path] = []
    for folder, suffix in SUPPORTED_CORPUS.items():
        folder_path = corpus_root / folder
        if not folder_path.exists():
            raise RuntimeError(f"Missing corpus folder: {folder_path}")
        documents.extend(sorted(folder_path.glob(f"*{suffix}")))
    return documents


def flag_outliers(chunks_by_doc: dict[Path, list[Chunk]]) -> list[str]:
    counts = [len(chunks) for chunks in chunks_by_doc.values()]
    if not counts:
        return ["No chunks were produced."]
    average = sum(counts) / len(counts)
    flags: list[str] = []
    for path, chunks in chunks_by_doc.items():
        count = len(chunks)
        if count == 0:
            flags.append(f"{path.name}: produced 0 chunks")
        elif count <= 1:
            flags.append(f"{path.name}: suspiciously few chunks ({count})")
        elif count > max(12, average * 2.5):
            flags.append(f"{path.name}: suspiciously many chunks ({count}; average {average:.1f})")
    return flags


def hybrid_search(
    search_endpoint: str,
    search_key: str,
    index_name: str,
    query: str,
    query_embedding: list[float],
    top: int = 3,
) -> list[dict]:
    url = f"{search_endpoint}/indexes/{index_name}/docs/search?api-version={AZURE_SEARCH_API_VERSION}"
    body = {
        "search": query,
        "top": top,
        "select": "source_document,section_title,chunk_index,format,text",
        "vectorQueries": [
            {
                "kind": "vector",
                "vector": query_embedding,
                "fields": "embedding",
                "k": top,
            }
        ],
    }
    response = request_with_retry("POST", url, headers=azure_headers(search_key), json_body=body)
    if response.status_code >= 400:
        raise RuntimeError(f"Hybrid search query failed: HTTP {response.status_code} {response.text}")
    return response.json().get("value", [])


def print_verification(chunks_by_doc: dict[Path, list[Chunk]], parsing_failures: list[str], outliers: list[str], retrievals: dict[str, list[dict]]) -> None:
    total_chunks = sum(len(chunks) for chunks in chunks_by_doc.values())
    print("\nVerification")
    print("============")
    print(f"Total documents processed: {len(chunks_by_doc)}")
    for path, chunks in sorted(chunks_by_doc.items(), key=lambda item: item[0].name):
        print(f"- {path.name}: {len(chunks)} chunks")
    print(f"Total chunks: {total_chunks}")
    print("\nHybrid search configuration: enabled via a searchable text field plus an HNSW vector field, and verified with sample queries that send both `search` text and `vectorQueries`.")

    print("\nParsing failures:")
    if parsing_failures:
        for failure in parsing_failures:
            print(f"- {failure}")
    else:
        print("- None")

    print("\nChunk count outliers:")
    if outliers:
        for outlier in outliers:
            print(f"- {outlier}")
    else:
        print("- None flagged")

    print("\nSample retrieval results:")
    for query, results in retrievals.items():
        print(f"\nQuery: {query}")
        for index, result in enumerate(results, start=1):
            snippet = normalize_space(result.get("text", "")).replace("\n", " ")
            if len(snippet) > 420:
                snippet = snippet[:417] + "..."
            print(
                f"{index}. {result.get('source_document')} | {result.get('section_title')} | "
                f"chunk {result.get('chunk_index')} | score {result.get('@search.score')}: {snippet}"
            )


def run(args: argparse.Namespace) -> None:
    load_dotenv(args.env_file)

    corpus_root = Path(args.corpus_root)
    db_path = Path(args.database)
    openai_endpoint = required_env("AZURE_OPENAI_ENDPOINT")
    openai_key = required_env("AZURE_OPENAI_API_KEY")
    embedding_deployment = required_env("EMBEDDING_DEPLOYMENT")
    search_endpoint = required_env("AZURE_SEARCH_ENDPOINT")
    search_key = required_env("AZURE_SEARCH_API_KEY")
    index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "meridian-knowledge-base")

    documents = discover_documents(corpus_root)
    if len(documents) != 19:
        LOGGER.warning("Expected 19 documents but found %s under %s", len(documents), corpus_root)

    chunks_by_doc: dict[Path, list[Chunk]] = {}
    parsing_failures: list[str] = []
    for path in documents:
        try:
            LOGGER.info("Parsing %s", path.name)
            sections = parse_document(path)
            chunks_by_doc[path] = chunk_sections(path.name, path.suffix.lower().lstrip("."), sections)
            LOGGER.info("Chunked %s into %s chunks", path.name, len(chunks_by_doc[path]))
        except Exception as exc:  # noqa: BLE001 - keep processing and report every failed doc
            LOGGER.exception("Failed to parse %s", path)
            parsing_failures.append(f"{path.name}: {exc}")

    all_chunks = [chunk for chunks in chunks_by_doc.values() for chunk in chunks]
    if not all_chunks:
        raise RuntimeError("No chunks were produced; aborting before embedding/indexing.")

    LOGGER.info("Writing SQLite metadata to %s", db_path)
    write_sqlite(db_path, chunks_by_doc)
    LOGGER.info("Creating clean Azure AI Search index %s", index_name)
    create_search_index(search_endpoint, search_key, index_name)
    LOGGER.info("Embedding %s chunks", len(all_chunks))
    embeddings = embed_texts(openai_endpoint, openai_key, embedding_deployment, [chunk.text for chunk in all_chunks], args.embedding_batch_size)
    LOGGER.info("Uploading %s chunks to Azure AI Search", len(all_chunks))
    upload_chunks(search_endpoint, search_key, index_name, all_chunks, embeddings)

    sample_queries = [
        "How many days of PTO do employees get?",
        "What is the deductible on the Bronze plan?",
        "What should I do if I lose my laptop?",
    ]
    query_embeddings = embed_texts(openai_endpoint, openai_key, embedding_deployment, sample_queries, args.embedding_batch_size)
    retrievals = {
        query: hybrid_search(search_endpoint, search_key, index_name, query, embedding)
        for query, embedding in zip(sample_queries, query_embeddings, strict=True)
    }
    print_verification(chunks_by_doc, parsing_failures, flag_outliers(chunks_by_doc), retrievals)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Meridian Health Partners corpus into SQLite and Azure AI Search.")
    parser.add_argument("--corpus-root", default="corpus", help="Root folder containing policies, benefits, and procedures.")
    parser.add_argument("--database", default="meridian.db", help="SQLite database path for local admin metadata.")
    parser.add_argument("--env-file", default=".env", help="Environment file to load. Values are never printed.")
    parser.add_argument("--embedding-batch-size", type=int, default=16, help="Embedding request batch size.")
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(build_parser().parse_args())
