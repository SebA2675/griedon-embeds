import json
import sqlite3
import time
from contextlib import closing

DB_PATH = "data.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                guild_id TEXT,
                channel_id TEXT,
                posted_message_id TEXT,
                layout_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def to_dict(row, full=True):
    data = {
        "id": row["id"],
        "name": row["name"],
        "guild_id": row["guild_id"],
        "channel_id": row["channel_id"],
        "posted_message_id": row["posted_message_id"],
        "updated_at": row["updated_at"],
    }
    if full:
        data["layout"] = json.loads(row["layout_json"])
    return data


def list_messages():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM messages ORDER BY updated_at DESC").fetchall()
    return [to_dict(r, full=False) for r in rows]


def get_message(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return to_dict(row) if row else None


def create_message(name, layout):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (name, layout_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, json.dumps(layout), now, now),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_message(new_id)


def update_message(message_id, name, layout):
    with closing(connect()) as conn:
        cur = conn.execute(
            "UPDATE messages SET name = ?, layout_json = ?, updated_at = ? WHERE id = ?",
            (name, json.dumps(layout), int(time.time()), message_id),
        )
        conn.commit()
        changed = cur.rowcount
    return get_message(message_id) if changed else None


def delete_message(message_id):
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()
        return cur.rowcount > 0


def set_posted(message_id, guild_id, channel_id, posted_message_id):
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE messages SET guild_id = ?, channel_id = ?, posted_message_id = ? WHERE id = ?",
            (guild_id, channel_id, posted_message_id, message_id),
        )
        conn.commit()


def clear_posted(message_id):
    with closing(connect()) as conn:
        conn.execute("UPDATE messages SET posted_message_id = NULL WHERE id = ?", (message_id,))
        conn.commit()
