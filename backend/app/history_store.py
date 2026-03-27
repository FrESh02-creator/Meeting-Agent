from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

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
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _get_existing_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_action_items_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_history_id INTEGER,
            title TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            task TEXT NOT NULL,
            owner TEXT NOT NULL,
            deadline TEXT NOT NULL,
            risk TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(meeting_history_id) REFERENCES meeting_history(id) ON DELETE CASCADE
        )
        """
    )

    existing_columns = _get_existing_columns(connection, "action_items")
    if "meeting_history_id" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN meeting_history_id INTEGER")
    if "title" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    if "date" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN date TEXT NOT NULL DEFAULT ''")
    if "risk" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN risk TEXT NOT NULL DEFAULT ''")
    if "status" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
    if "created_at" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    if "updated_at" not in existing_columns:
        connection.execute("ALTER TABLE action_items ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")


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

        existing_columns = _get_existing_columns(connection, "meeting_history")
        if "entry_type" not in existing_columns:
            connection.execute(
                "ALTER TABLE meeting_history ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'meeting_analysis'"
            )
        if "report_type" not in existing_columns:
            connection.execute("ALTER TABLE meeting_history ADD COLUMN report_type TEXT NOT NULL DEFAULT ''")

        _ensure_action_items_table(connection)


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


def _normalize_action_row(
    *,
    meeting_history_id: int | None,
    title: str,
    date: str,
    action: dict[str, Any],
    timestamp: str,
) -> tuple[Any, ...] | None:
    task = str(action.get("task", "") or "").strip()
    if not task:
        return None

    owner = str(action.get("owner", "") or "").strip() or "待定"
    deadline = str(action.get("deadline", "") or "").strip() or "待定"
    risk = str(action.get("risk", "") or "").strip() or "无"
    status = str(action.get("status", "") or "pending").strip() or "pending"
    if status not in {"pending", "completed"}:
        status = "pending"

    return (
        meeting_history_id,
        title,
        date,
        task,
        owner,
        deadline,
        risk,
        status,
        timestamp,
        timestamp,
    )


def _insert_action_items(
    connection: sqlite3.Connection,
    *,
    meeting_history_id: int | None,
    title: str,
    date: str,
    actions: Sequence[dict[str, Any]],
) -> list[int]:
    timestamp = datetime.now().isoformat(timespec="seconds")
    inserted_ids: list[int] = []
    for action in actions:
        row = _normalize_action_row(
            meeting_history_id=meeting_history_id,
            title=title,
            date=date,
            action=action,
            timestamp=timestamp,
        )
        if row is None:
            continue
        cursor = connection.execute(
            """
            INSERT INTO action_items (
                meeting_history_id,
                title,
                date,
                task,
                owner,
                deadline,
                risk,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        inserted_ids.append(int(cursor.lastrowid))
    return inserted_ids


def save_action_items(
    *,
    meeting_history_id: int | None,
    title: str,
    date: str,
    actions: Sequence[dict[str, Any]],
) -> list[int]:
    with _connect() as connection:
        return _insert_action_items(
            connection,
            meeting_history_id=meeting_history_id,
            title=title,
            date=date,
            actions=actions,
        )


def replace_action_items_for_history(
    *,
    meeting_history_id: int,
    title: str,
    date: str,
    actions: Sequence[dict[str, Any]],
) -> list[int]:
    with _connect() as connection:
        connection.execute("DELETE FROM action_items WHERE meeting_history_id = ?", (meeting_history_id,))
        return _insert_action_items(
            connection,
            meeting_history_id=meeting_history_id,
            title=title,
            date=date,
            actions=actions,
        )


def list_action_items(limit: int = 500) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                meeting_history_id,
                title,
                date,
                task,
                owner,
                deadline,
                risk,
                status,
                created_at,
                updated_at
            FROM action_items
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def update_action_item_status(action_item_id: int, status: str) -> bool:
    normalized_status = status.strip().lower()
    if normalized_status not in {"pending", "completed"}:
        raise ValueError("Unsupported action item status")

    updated_at = datetime.now().isoformat(timespec="seconds")
    with _connect() as connection:
        cursor = connection.execute(
            "UPDATE action_items SET status = ?, updated_at = ? WHERE id = ?",
            (normalized_status, updated_at, action_item_id),
        )
        return cursor.rowcount > 0


def delete_action_item(action_item_id: int) -> bool:
    with _connect() as connection:
        cursor = connection.execute("DELETE FROM action_items WHERE id = ?", (action_item_id,))
        return cursor.rowcount > 0


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
