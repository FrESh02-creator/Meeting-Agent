from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "meeting_history.db"


def _resolve_db_path() -> Path:
    configured = os.getenv("MEETING_HISTORY_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DB_PATH


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db_path = _resolve_db_path()
    _ensure_parent_dir(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _get_existing_columns(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(meeting_history)").fetchall()
    return {str(row[1]) for row in rows}


def init_history_db() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meeting_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                entry_type TEXT NOT NULL DEFAULT 'meeting_analysis',
                report_type TEXT NOT NULL DEFAULT '',
                structured_data TEXT NOT NULL,
                markdown_content TEXT NOT NULL,
                docx_content BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        existing_columns = _get_existing_columns(connection)
        if "entry_type" not in existing_columns:
            connection.execute(
                "ALTER TABLE meeting_history ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'meeting_analysis'"
            )
        if "report_type" not in existing_columns:
            connection.execute("ALTER TABLE meeting_history ADD COLUMN report_type TEXT NOT NULL DEFAULT ''")


def save_meeting_history(
    *,
    title: str,
    date: str,
    entry_type: str,
    report_type: str | None,
    structured_data: str,
    markdown_content: str,
    docx_content: bytes,
) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO meeting_history (
                title,
                date,
                entry_type,
                report_type,
                structured_data,
                markdown_content,
                docx_content,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                date,
                entry_type,
                report_type or "",
                structured_data,
                markdown_content,
                sqlite3.Binary(docx_content),
                created_at,
            ),
        )
        return int(cursor.lastrowid)


def list_meeting_history(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, title, date, entry_type, report_type, created_at
            FROM meeting_history
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["entry_type"] = item.get("entry_type") or "meeting_analysis"
        item["report_type"] = item.get("report_type") or None
        items.append(item)
    return items


def get_meeting_history_record(history_id: int) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, title, date, entry_type, report_type, structured_data, markdown_content, docx_content, created_at
            FROM meeting_history
            WHERE id = ?
            """,
            (history_id,),
        ).fetchone()

    if not row:
        return None

    record = dict(row)
    record["entry_type"] = record.get("entry_type") or "meeting_analysis"
    record["report_type"] = record.get("report_type") or None
    return record


def delete_meeting_history(history_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM meeting_history WHERE id = ?", (history_id,))
        return cursor.rowcount > 0


def clear_meeting_history() -> int:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM meeting_history")
        return int(cursor.rowcount or 0)


def update_audio_transcript_history(
    history_id: int,
    *,
    title: str,
    structured_data: str,
    markdown_content: str,
    docx_content: bytes,
) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE meeting_history
            SET title = ?, structured_data = ?, markdown_content = ?, docx_content = ?
            WHERE id = ? AND entry_type = ?
            """,
            (
                title,
                structured_data,
                markdown_content,
                sqlite3.Binary(docx_content),
                history_id,
                "audio_transcript",
            ),
        )
        return cursor.rowcount > 0
