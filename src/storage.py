import sqlite3
from pathlib import Path
from typing import Optional


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                item_id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def is_seen(db_path: Path, item_id: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,))
        return cursor.fetchone() is not None


def mark_seen(db_path: Path, item_id: str, timestamp: str) -> Optional[str]:
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO seen_items (item_id, first_seen_at) VALUES (?, ?)",
                (item_id, timestamp),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return item_id
