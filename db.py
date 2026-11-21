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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_guilds (
                guild_id TEXT PRIMARY KEY,
                name TEXT,
                added_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_guilds (
                guild_id TEXT PRIMARY KEY,
                name TEXT,
                seen_at INTEGER NOT NULL
            )
            """
        )
        rows = conn.execute("SELECT id, layout_json FROM messages WHERE guild_id IS NULL").fetchall()
        for row in rows:
            try:
                gid = json.loads(row["layout_json"]).get("guild_id")
            except ValueError:
                gid = None
            if gid:
                conn.execute("UPDATE messages SET guild_id = ? WHERE id = ?", (str(gid), row["id"]))
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


def list_messages(guild_ids=None):
    with closing(connect()) as conn:
        if guild_ids is None:
            rows = conn.execute("SELECT * FROM messages ORDER BY updated_at DESC").fetchall()
        elif not guild_ids:
            return []
        else:
            marks = ",".join("?" for _ in guild_ids)
            rows = conn.execute(
                f"SELECT * FROM messages WHERE guild_id IN ({marks}) ORDER BY updated_at DESC",
                list(guild_ids),
            ).fetchall()
    return [to_dict(r, full=False) for r in rows]


def count_messages(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def get_message(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return to_dict(row) if row else None


def create_message(name, layout, guild_id):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (name, guild_id, layout_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, guild_id, json.dumps(layout), now, now),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_message(new_id)


def update_message(message_id, name, layout, guild_id):
    with closing(connect()) as conn:
        cur = conn.execute(
            """
            UPDATE messages SET
                name = ?,
                layout_json = ?,
                updated_at = ?,
                posted_message_id = CASE WHEN guild_id = ? THEN posted_message_id ELSE NULL END,
                channel_id = CASE WHEN guild_id = ? THEN channel_id ELSE NULL END,
                guild_id = ?
            WHERE id = ?
            """,
            (name, json.dumps(layout), int(time.time()), guild_id, guild_id, guild_id, message_id),
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


def list_allowed():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM allowed_guilds ORDER BY added_at").fetchall()
    return [dict(r) for r in rows]


def list_allowed_ids():
    return [r["guild_id"] for r in list_allowed()]


def allowed_count():
    with closing(connect()) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM allowed_guilds").fetchone()["n"]


def allow_guild(guild_id, name):
    with closing(connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO allowed_guilds (guild_id, name, added_at) VALUES (?, ?, ?)",
            (guild_id, name, int(time.time())),
        )
        conn.execute("DELETE FROM pending_guilds WHERE guild_id = ?", (guild_id,))
        conn.commit()


def remove_allowed(guild_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM allowed_guilds WHERE guild_id = ?", (guild_id,))
        conn.commit()


def add_pending(guild_id, name):
    with closing(connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pending_guilds (guild_id, name, seen_at) VALUES (?, ?, ?)",
            (guild_id, name, int(time.time())),
        )
        conn.commit()


def list_pending():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM pending_guilds ORDER BY seen_at DESC").fetchall()
    return [dict(r) for r in rows]


def remove_pending(guild_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM pending_guilds WHERE guild_id = ?", (guild_id,))
        conn.commit()
