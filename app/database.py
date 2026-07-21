from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


def connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(database_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_chat_tables(database_path: str) -> None:
    with connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_content TEXT NOT NULL,
                rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                note TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
            """
        )


def ensure_conversation(database_path: str, conversation_id: str | None) -> str:
    now = int(time.time())
    conversation_id = conversation_id or str(uuid.uuid4())
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO conversations (id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (conversation_id, now, now),
        )
    return conversation_id


def add_message(database_path: str, conversation_id: str, role: str, content: str) -> None:
    now = int(time.time())
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), conversation_id, role, content, now),
        )
        connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))


def get_history(database_path: str, conversation_id: str) -> list[sqlite3.Row]:
    with connect(database_path) as connection:
        return connection.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()


def conversation_exists(database_path: str, conversation_id: str) -> bool:
    with connect(database_path) as connection:
        row = connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return row is not None


def get_message_by_id(database_path: str, message_id: str) -> sqlite3.Row | None:
    with connect(database_path) as connection:
        return connection.execute(
            "SELECT id, conversation_id, role, content, created_at FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()


def get_message_by_index(database_path: str, conversation_id: str, message_index: int) -> sqlite3.Row | None:
    history = get_history(database_path, conversation_id)
    if message_index < 0 or message_index >= len(history):
        return None
    return history[message_index]


def add_feedback(database_path: str, conversation_id: str, message_content: str, rating: str, note: str | None) -> str:
    feedback_id = str(uuid.uuid4())
    now = int(time.time())
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO feedback (id, conversation_id, message_content, rating, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, conversation_id, message_content, rating, note, now),
        )
    return feedback_id


def format_history_for_prompt(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "No prior conversation."

    older = rows[:-6]
    recent = rows[-6:]
    lines: list[str] = []
    if older:
        lines.append("Older conversation summaries:")
        for row in older:
            content = " ".join(str(row["content"]).split())
            if len(content) > 180:
                content = content[:177] + "..."
            lines.append(f"- {row['role']}: {content}")
    if recent:
        lines.append("Last 3 turns verbatim:")
        for row in recent:
            lines.append(f"{row['role']}: {row['content']}")
    return "\n".join(lines)


def list_conversations(database_path: str) -> list[sqlite3.Row]:
    with connect(database_path) as connection:
        return connection.execute(
            """
            SELECT
                c.id,
                c.created_at,
                c.updated_at,
                COALESCE(
                    (SELECT m.content FROM messages m
                     WHERE m.conversation_id = c.id AND m.role = 'user'
                     ORDER BY m.created_at, m.rowid LIMIT 1),
                    ''
                ) AS preview,
                (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
            FROM conversations c
            WHERE EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)
            ORDER BY c.updated_at DESC, c.rowid DESC
            """
        ).fetchall()


def list_documents(database_path: str) -> list[sqlite3.Row]:
    with connect(database_path) as connection:
        return connection.execute(
            """
            SELECT d.id, d.filename, d.format, COUNT(c.id) AS chunk_count
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.id
            GROUP BY d.id, d.filename, d.format
            ORDER BY d.filename
            """
        ).fetchall()


def get_chunk(database_path: str, chunk_id: str) -> sqlite3.Row | None:
    with connect(database_path) as connection:
        return connection.execute(
            """
            SELECT c.id, d.filename AS document, d.format, c.section_title AS section, c.chunk_index, c.text
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
