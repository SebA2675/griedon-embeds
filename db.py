import json
import os
import hashlib
import sqlite3
import time
from contextlib import closing

DB_PATH = "data.db"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def text_hash(text):
    try:
        text = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except ValueError:
        pass
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                layout_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                name TEXT,
                layout_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                guild_id TEXT NOT NULL,
                action TEXT NOT NULL,
                channel_id TEXT,
                run_date TEXT NOT NULL,
                run_time TEXT NOT NULL,
                tz TEXT NOT NULL,
                repeat_kind TEXT NOT NULL,
                replace_previous INTEGER NOT NULL DEFAULT 0,
                next_run INTEGER,
                last_run INTEGER,
                last_status TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS button_uses (
                message_id INTEGER NOT NULL,
                component_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                last_used INTEGER NOT NULL,
                uses INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (message_id, component_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                avatar_url TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS token_state (
                user_id TEXT PRIMARY KEY,
                valid_after INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                message_ids_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_guild ON messages(guild_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sequences_guild ON sequences(guild_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_versions_message ON versions(message_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(active, next_run)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_message ON schedules(message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_guild ON templates(guild_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_personas_guild ON webhook_personas(guild_id)")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "webhook_id" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN webhook_id TEXT")
        for col in ("layout_hash", "version_hash"):
            if col not in cols:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")
        for row in conn.execute("SELECT id, layout_json FROM messages WHERE layout_hash IS NULL").fetchall():
            conn.execute("UPDATE messages SET layout_hash = ? WHERE id = ?", (text_hash(row["layout_json"]), row["id"]))
        latest = conn.execute(
            "SELECT message_id, layout_json FROM versions WHERE id IN (SELECT MAX(id) FROM versions GROUP BY message_id)"
        ).fetchall()
        for row in latest:
            conn.execute(
                "UPDATE messages SET version_hash = ? WHERE id = ? AND version_hash IS NULL",
                (text_hash(row["layout_json"]), row["message_id"]),
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


def row_changed(row):
    if not row["posted_message_id"] or not row["layout_hash"] or not row["version_hash"]:
        return False
    return row["layout_hash"] != row["version_hash"]


def is_changed(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return row_changed(row) if row else False


def to_dict(row, full=True):
    data = {
        "id": row["id"],
        "name": row["name"],
        "guild_id": row["guild_id"],
        "channel_id": row["channel_id"],
        "posted_message_id": row["posted_message_id"],
        "webhook_id": row["webhook_id"],
        "updated_at": row["updated_at"],
        "changed": row_changed(row),
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


def list_messages_full(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM messages WHERE guild_id = ? ORDER BY id", (guild_id,)).fetchall()
    return [to_dict(r) for r in rows]


def count_messages(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def get_message(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return to_dict(row) if row else None


CACHE_SECONDS = 60
_cache = {}


def forget(message_id):
    _cache.pop(int(message_id), None)


def get_message_cached(message_id):
    """For button clicks only: a message kept in memory for about a minute."""
    message_id = int(message_id)
    hit = _cache.get(message_id)
    now = time.time()
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    message = get_message(message_id)
    if message is None:
        _cache.pop(message_id, None)
        return None
    if len(_cache) > 200:
        _cache.clear()
    _cache[message_id] = (now, message)
    return message


def create_message(name, layout, guild_id):
    now = int(time.time())
    text = json.dumps(layout)
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (name, guild_id, layout_json, layout_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, guild_id, text, text_hash(text), now, now),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_message(new_id)


def update_message(message_id, name, layout, guild_id):
    forget(message_id)
    text = json.dumps(layout)
    with closing(connect()) as conn:
        cur = conn.execute(
            """
            UPDATE messages SET
                name = ?,
                layout_json = ?,
                layout_hash = ?,
                updated_at = ?,
                posted_message_id = CASE WHEN guild_id = ? THEN posted_message_id ELSE NULL END,
                channel_id = CASE WHEN guild_id = ? THEN channel_id ELSE NULL END,
                webhook_id = CASE WHEN guild_id = ? THEN webhook_id ELSE NULL END,
                guild_id = ?
            WHERE id = ?
            """,
            (name, text, text_hash(text), int(time.time()), guild_id, guild_id, guild_id, guild_id, message_id),
        )
        conn.commit()
        changed = cur.rowcount
    return get_message(message_id) if changed else None


def delete_message(message_id):
    forget(message_id)
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.execute("DELETE FROM versions WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM schedules WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM button_uses WHERE message_id = ?", (message_id,))
        conn.commit()
        return cur.rowcount > 0


def set_posted(message_id, guild_id, channel_id, posted_message_id):
    forget(message_id)
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE messages SET guild_id = ?, channel_id = ?, posted_message_id = ?, webhook_id = NULL WHERE id = ?",
            (guild_id, channel_id, posted_message_id, message_id),
        )
        conn.commit()


def set_posted_via_webhook(message_id, guild_id, channel_id, posted_message_id, webhook_id):
    forget(message_id)
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE messages SET guild_id = ?, channel_id = ?, posted_message_id = ?, webhook_id = ? WHERE id = ?",
            (guild_id, channel_id, posted_message_id, webhook_id, message_id),
        )
        conn.commit()


def clear_posted(message_id):
    forget(message_id)
    with closing(connect()) as conn:
        conn.execute("UPDATE messages SET posted_message_id = NULL, webhook_id = NULL WHERE id = ?", (message_id,))
        conn.commit()


# ---------- Templates ----------

def template_dict(row, full=True):
    data = {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "name": row["name"],
        "created_at": row["created_at"],
    }
    if full:
        data["layout"] = json.loads(row["layout_json"])
    return data


def list_templates(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM templates WHERE guild_id = ? ORDER BY name COLLATE NOCASE", (guild_id,)
        ).fetchall()
    return [template_dict(r, full=False) for r in rows]


def list_templates_full(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM templates WHERE guild_id = ? ORDER BY id", (guild_id,)).fetchall()
    return [template_dict(r) for r in rows]


def count_templates(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM templates WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def get_template(template_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    return template_dict(row) if row else None


def create_template(guild_id, name, layout):
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO templates (guild_id, name, layout_json, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, name, json.dumps(layout), int(time.time())),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_template(new_id)


def delete_template(template_id):
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------- Version history ----------

def add_version(message_id, name, layout, keep=20):
    text = json.dumps(layout)
    with closing(connect()) as conn:
        last = conn.execute(
            "SELECT layout_json FROM versions WHERE message_id = ? ORDER BY id DESC LIMIT 1", (message_id,)
        ).fetchone()
        if last and last["layout_json"] == text:
            conn.execute("UPDATE messages SET version_hash = ? WHERE id = ?", (text_hash(text), message_id))
            conn.commit()
            return
        conn.execute(
            "INSERT INTO versions (message_id, name, layout_json, created_at) VALUES (?, ?, ?, ?)",
            (message_id, name, text, int(time.time())),
        )
        conn.execute("UPDATE messages SET version_hash = ? WHERE id = ?", (text_hash(text), message_id))
        conn.execute(
            """
            DELETE FROM versions WHERE message_id = ? AND id NOT IN (
                SELECT id FROM versions WHERE message_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (message_id, message_id, keep),
        )
        conn.commit()


def list_versions(message_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM versions WHERE message_id = ? ORDER BY id DESC", (message_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(message_id, version_id):
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM versions WHERE id = ? AND message_id = ?", (version_id, message_id)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "layout": json.loads(row["layout_json"]),
    }


# ---------- Schedules ----------

def schedule_dict(row):
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "guild_id": row["guild_id"],
        "action": row["action"],
        "channel_id": row["channel_id"] or "",
        "run_date": row["run_date"],
        "run_time": row["run_time"],
        "tz": row["tz"],
        "repeat": row["repeat_kind"],
        "replace_previous": bool(row["replace_previous"]),
        "next_run": row["next_run"],
        "last_run": row["last_run"],
        "last_status": row["last_status"],
        "active": bool(row["active"]),
    }


def create_schedule(message_id, guild_id, action, channel_id, run_date, run_time, tz, repeat,
                    replace_previous, next_run):
    with closing(connect()) as conn:
        cur = conn.execute(
            """
            INSERT INTO schedules (message_id, guild_id, action, channel_id, run_date, run_time, tz,
                                   repeat_kind, replace_previous, next_run, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (message_id, guild_id, action, channel_id, run_date, run_time, tz, repeat,
             1 if replace_previous else 0, next_run, int(time.time())),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_schedule(new_id)


def get_schedule(schedule_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
    return schedule_dict(row) if row else None


def list_schedules(message_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE message_id = ? ORDER BY active DESC, next_run", (message_id,)
        ).fetchall()
    return [schedule_dict(r) for r in rows]


def count_schedules_message(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM schedules WHERE message_id = ?", (message_id,)).fetchone()
    return row["n"]


def count_schedules_guild(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM schedules WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def delete_schedule(schedule_id):
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        conn.commit()
        return cur.rowcount > 0


def due_schedules(now):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE active = 1 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run",
            (now,),
        ).fetchall()
    return [schedule_dict(r) for r in rows]


def finish_schedule(schedule_id, last_run, status, next_run, run_date, active):
    with closing(connect()) as conn:
        conn.execute(
            """
            UPDATE schedules SET last_run = ?, last_status = ?, next_run = ?, run_date = ?, active = ?
            WHERE id = ?
            """,
            (last_run, status[:300], next_run, run_date, 1 if active else 0, schedule_id),
        )
        conn.commit()


# ---------- Approved servers ----------

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


# ---------- Button rules (cooldown and one-use) ----------

def get_use(message_id, component_id, user_id):
    with closing(connect()) as conn:
        return conn.execute(
            "SELECT last_used, uses FROM button_uses WHERE message_id = ? AND component_id = ? AND user_id = ?",
            (message_id, component_id, str(user_id)),
        ).fetchone()


def record_use(message_id, component_id, user_id):
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO button_uses (message_id, component_id, user_id, last_used, uses)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(message_id, component_id, user_id)
            DO UPDATE SET last_used = excluded.last_used, uses = uses + 1
            """,
            (message_id, component_id, str(user_id), int(time.time())),
        )
        conn.commit()


# ---------- Webhook presets ----------

def persona_dict(row):
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "name": row["name"],
        "avatar_url": row["avatar_url"] or "",
    }


def list_personas(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM webhook_personas WHERE guild_id = ? ORDER BY name COLLATE NOCASE", (guild_id,)
        ).fetchall()
    return [persona_dict(r) for r in rows]


def count_personas(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM webhook_personas WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def get_persona(persona_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM webhook_personas WHERE id = ?", (persona_id,)).fetchone()
    return persona_dict(row) if row else None


def create_persona(guild_id, name, avatar_url):
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO webhook_personas (guild_id, name, avatar_url, created_at) VALUES (?, ?, ?, ?)",
            (guild_id, name, avatar_url, int(time.time())),
        )
        conn.commit()
        new_id = cur.lastrowid
    return get_persona(new_id)


def delete_persona(persona_id):
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM webhook_personas WHERE id = ?", (persona_id,))
        conn.commit()
        return cur.rowcount > 0


# ---------- Housekeeping ----------

def cleanup():
    """Remove rows that belong to messages that no longer exist. Returns how many rows were removed."""
    removed = 0
    with closing(connect()) as conn:
        for table in ("versions", "schedules", "button_uses"):
            cur = conn.execute(
                f"DELETE FROM {table} WHERE message_id NOT IN (SELECT id FROM messages)"
            )
            removed += cur.rowcount
        conn.commit()
    return removed


def stats():
    with closing(connect()) as conn:
        counts = {}
        for table in ("messages", "versions", "schedules", "templates", "button_uses", "webhook_personas"):
            counts[table] = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    try:
        size = os.path.getsize(DB_PATH)
    except OSError:
        size = 0
    return {"size_bytes": size, "counts": counts}


# ---------- Logout (token revocation) ----------

_valid_after = {}


def get_valid_after(user_id):
    """Tokens issued before this time (unix seconds) are no longer accepted."""
    user_id = str(user_id)
    if user_id not in _valid_after:
        with closing(connect()) as conn:
            row = conn.execute("SELECT valid_after FROM token_state WHERE user_id = ?", (user_id,)).fetchone()
        _valid_after[user_id] = row["valid_after"] if row else 0
    return _valid_after[user_id]


def revoke_tokens(user_id):
    user_id = str(user_id)
    now = int(time.time())
    with closing(connect()) as conn:
        conn.execute(
            "INSERT INTO token_state (user_id, valid_after) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET valid_after = excluded.valid_after",
            (user_id, now),
        )
        conn.commit()
    _valid_after[user_id] = now

def _seq_row(row):
    d = dict(row)
    d["message_ids"] = json.loads(d.pop("message_ids_json", "[]"))
    return d


def list_sequences(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sequences WHERE guild_id = ? ORDER BY updated_at DESC",
            (str(guild_id),),
        ).fetchall()
    return [_seq_row(r) for r in rows]


def count_sequences(guild_id):
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sequences WHERE guild_id = ?",
            (str(guild_id),),
        ).fetchone()
    return int(row["n"])


def get_sequence(seq_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM sequences WHERE id = ?", (seq_id,)).fetchone()
    return _seq_row(row) if row else None


def create_sequence(guild_id, name, message_ids):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            """
            INSERT INTO sequences (guild_id, name, message_ids_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(guild_id), name, json.dumps([int(x) for x in message_ids]), now, now),
        )
        conn.commit()
        sid = cur.lastrowid
    return get_sequence(sid)


def delete_sequence(seq_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM sequences WHERE id = ?", (seq_id,))
        conn.commit()