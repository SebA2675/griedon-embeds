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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                guild_name TEXT NOT NULL,
                username TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS click_counts (
                message_id INTEGER NOT NULL,
                component_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (message_id, component_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS form_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                guild_id TEXT NOT NULL,
                form_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sequences_guild ON sequences(guild_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_settings (
                guild_id TEXT PRIMARY KEY,
                welcome_enabled INTEGER NOT NULL DEFAULT 0,
                welcome_channel_id TEXT NOT NULL DEFAULT '',
                welcome_message_id INTEGER NOT NULL DEFAULT 0,
                goodbye_enabled INTEGER NOT NULL DEFAULT 0,
                goodbye_channel_id TEXT NOT NULL DEFAULT '',
                goodbye_message_id INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keyword_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                phrase TEXT NOT NULL,
                match_type TEXT NOT NULL DEFAULT 'contains',
                message_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL DEFAULT '',
                cooldown INTEGER NOT NULL DEFAULT 10,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'rss',
                url TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                last_seen TEXT NOT NULL DEFAULT '',
                last_check INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stickies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL UNIQUE,
                message_id INTEGER NOT NULL,
                last_posted_id TEXT NOT NULL DEFAULT '',
                delay_seconds INTEGER NOT NULL DEFAULT 5,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_form_resp_msg ON form_responses(message_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kw_guild ON keyword_rules(guild_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS server_settings (
                guild_id TEXT PRIMARY KEY,
                default_color TEXT NOT NULL DEFAULT '#5865F2',
                default_footer TEXT NOT NULL DEFAULT '',
                logo_url TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_editors (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feeds_guild ON feeds(guild_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_guild ON audit_log(guild_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_versions_message ON versions(message_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(active, next_run)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schedules_message ON schedules(message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_templates_guild ON templates(guild_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_personas_guild ON webhook_personas(guild_id)")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "deleted_at" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN deleted_at INTEGER NOT NULL DEFAULT 0")
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
            rows = conn.execute("SELECT * FROM messages WHERE deleted_at = 0 ORDER BY updated_at DESC").fetchall()
        elif not guild_ids:
            return []
        else:
            marks = ",".join("?" for _ in guild_ids)
            rows = conn.execute(
                f"SELECT * FROM messages WHERE deleted_at = 0 AND guild_id IN ({marks}) ORDER BY updated_at DESC",
                list(guild_ids),
            ).fetchall()
    return [to_dict(r, full=False) for r in rows]


def list_messages_full(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM messages WHERE guild_id = ? ORDER BY id", (guild_id,)).fetchall()
    return [to_dict(r) for r in rows]


def count_messages(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE deleted_at = 0 AND guild_id = ?", (guild_id,)).fetchone()
    return row["n"]


def get_message(message_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ? AND deleted_at = 0", (message_id,)).fetchone()
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
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute("UPDATE messages SET deleted_at = ? WHERE id = ?", (now, message_id))
        conn.execute("DELETE FROM schedules WHERE message_id = ?", (message_id,))
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
def guild_summary_stats():
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT guild_id, COUNT(*) AS cnt, MAX(updated_at) AS last_edit FROM messages GROUP BY guild_id"
        ).fetchall()
    return {str(r["guild_id"]): {"count": int(r["cnt"]), "last_edit": int(r["last_edit"] or 0)} for r in rows if r["guild_id"]}


def list_access_requests():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM access_requests ORDER BY created_at DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def get_access_request(req_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM access_requests WHERE id = ?", (req_id,)).fetchone()
    return dict(row) if row else None


def create_access_request(guild_id, guild_name, username):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO access_requests (guild_id, guild_name, username, created_at) VALUES (?, ?, ?, ?)",
            (str(guild_id), guild_name[:100], username[:100], now),
        )
        conn.commit()
        rid = cur.lastrowid
    return get_access_request(rid)


def delete_access_request(req_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM access_requests WHERE id = ?", (req_id,))
        conn.commit()
def set_schedule_active(schedule_id, active):
    with closing(connect()) as conn:
        conn.execute("UPDATE schedules SET active = ? WHERE id = ?", (1 if active else 0, schedule_id))
        conn.commit()
    return get_schedule(schedule_id)
def increment_click(message_id, component_id):
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO click_counts (message_id, component_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(message_id, component_id) DO UPDATE SET count = count + 1
            """,
            (message_id, str(component_id)),
        )
        conn.commit()


def get_click_count(message_id, component_id):
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT count FROM click_counts WHERE message_id = ? AND component_id = ?",
            (message_id, str(component_id)),
        ).fetchone()
    return int(row["count"]) if row else 0


def list_click_counts(message_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT component_id, count FROM click_counts WHERE message_id = ?",
            (message_id,),
        ).fetchall()
    return {r["component_id"]: int(r["count"]) for r in rows}


def add_form_response(message_id, guild_id, form_id, user_id, username, answers):
    now = int(time.time())
    with closing(connect()) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM form_responses WHERE message_id = ? AND form_id = ?",
            (message_id, str(form_id)),
        ).fetchone()["n"]
        if cnt >= 5000:
            conn.execute(
                """
                DELETE FROM form_responses WHERE id IN (
                    SELECT id FROM form_responses WHERE message_id = ? AND form_id = ?
                    ORDER BY created_at ASC LIMIT 1
                )
                """,
                (message_id, str(form_id)),
            )
        cur = conn.execute(
            """
            INSERT INTO form_responses (message_id, guild_id, form_id, user_id, username, answers_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (message_id, str(guild_id), str(form_id), str(user_id), str(username)[:100], json.dumps(answers), now),
        )
        conn.commit()
        return cur.lastrowid


def list_form_responses(message_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM form_responses WHERE message_id = ? ORDER BY created_at DESC LIMIT 500",
            (message_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["answers"] = json.loads(d.pop("answers_json", "[]"))
        out.append(d)
    return out


def get_form_response(resp_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM form_responses WHERE id = ?", (resp_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["answers"] = json.loads(d.pop("answers_json", "[]"))
    return d


def set_form_response_status(resp_id, status):
    with closing(connect()) as conn:
        conn.execute("UPDATE form_responses SET status = ? WHERE id = ?", (status, resp_id))
        conn.commit()
    return get_form_response(resp_id)
def get_automation(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM automation_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    if not row:
        return {
            "guild_id": str(guild_id),
            "welcome_enabled": False,
            "welcome_channel_id": "",
            "welcome_message_id": 0,
            "goodbye_enabled": False,
            "goodbye_channel_id": "",
            "goodbye_message_id": 0,
        }
    d = dict(row)
    d["welcome_enabled"] = bool(d["welcome_enabled"])
    d["goodbye_enabled"] = bool(d["goodbye_enabled"])
    return d


def save_automation(guild_id, data):
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO automation_settings (
                guild_id, welcome_enabled, welcome_channel_id, welcome_message_id,
                goodbye_enabled, goodbye_channel_id, goodbye_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                welcome_enabled = excluded.welcome_enabled,
                welcome_channel_id = excluded.welcome_channel_id,
                welcome_message_id = excluded.welcome_message_id,
                goodbye_enabled = excluded.goodbye_enabled,
                goodbye_channel_id = excluded.goodbye_channel_id,
                goodbye_message_id = excluded.goodbye_message_id
            """,
            (
                str(guild_id),
                1 if data.get("welcome_enabled") else 0,
                str(data.get("welcome_channel_id") or ""),
                int(data.get("welcome_message_id") or 0),
                1 if data.get("goodbye_enabled") else 0,
                str(data.get("goodbye_channel_id") or ""),
                int(data.get("goodbye_message_id") or 0),
            ),
        )
        conn.commit()
    return get_automation(guild_id)


def list_keywords(guild_id=None):
    with closing(connect()) as conn:
        if guild_id is None:
            rows = conn.execute("SELECT * FROM keyword_rules WHERE enabled = 1").fetchall()
        else:
            rows = conn.execute("SELECT * FROM keyword_rules WHERE guild_id = ? ORDER BY id DESC", (str(guild_id),)).fetchall()
    return [dict(r) for r in rows]


def create_keyword(guild_id, phrase, match_type, message_id, channel_id="", cooldown=10):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO keyword_rules (guild_id, phrase, match_type, message_id, channel_id, cooldown, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (str(guild_id), phrase[:100], match_type, int(message_id), str(channel_id or ""), int(cooldown), now),
        )
        conn.commit()
        kid = cur.lastrowid
        row = conn.execute("SELECT * FROM keyword_rules WHERE id = ?", (kid,)).fetchone()
    return dict(row)


def get_keyword(rule_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM keyword_rules WHERE id = ?", (rule_id,)).fetchone()
    return dict(row) if row else None


def delete_keyword(rule_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM keyword_rules WHERE id = ?", (rule_id,))
        conn.commit()


def list_feeds(guild_id=None):
    with closing(connect()) as conn:
        if guild_id is None:
            rows = conn.execute("SELECT * FROM feeds").fetchall()
        else:
            rows = conn.execute("SELECT * FROM feeds WHERE guild_id = ? ORDER BY id DESC", (str(guild_id),)).fetchall()
    return [dict(r) for r in rows]


def create_feed(guild_id, kind, url, channel_id, message_id):
    now = int(time.time())
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO feeds (guild_id, kind, url, channel_id, message_id, last_seen, last_check, last_error, created_at) VALUES (?, ?, ?, ?, ?, '', 0, '', ?)",
            (str(guild_id), kind, url[:500], str(channel_id), int(message_id), now),
        )
        conn.commit()
        fid = cur.lastrowid
        row = conn.execute("SELECT * FROM feeds WHERE id = ?", (fid,)).fetchone()
    return dict(row)


def get_feed(feed_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    return dict(row) if row else None


def update_feed_state(feed_id, last_seen, last_check, last_error=""):
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE feeds SET last_seen = ?, last_check = ?, last_error = ? WHERE id = ?",
            (str(last_seen)[:300], int(last_check), str(last_error)[:200], feed_id),
        )
        conn.commit()


def delete_feed(feed_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        conn.commit()


def list_stickies(guild_id=None):
    with closing(connect()) as conn:
        if guild_id is None:
            rows = conn.execute("SELECT * FROM stickies").fetchall()
        else:
            rows = conn.execute("SELECT * FROM stickies WHERE guild_id = ? ORDER BY id DESC", (str(guild_id),)).fetchall()
    return [dict(r) for r in rows]


def set_sticky(guild_id, channel_id, message_id, delay_seconds=5):
    now = int(time.time())
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO stickies (guild_id, channel_id, message_id, last_posted_id, delay_seconds, created_at)
            VALUES (?, ?, ?, '', ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                message_id = excluded.message_id,
                delay_seconds = excluded.delay_seconds
            """,
            (str(guild_id), str(channel_id), int(message_id), int(delay_seconds), now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM stickies WHERE channel_id = ?", (str(channel_id),)).fetchone()
    return dict(row)


def update_sticky_posted(channel_id, posted_id):
    with closing(connect()) as conn:
        conn.execute("UPDATE stickies SET last_posted_id = ? WHERE channel_id = ?", (str(posted_id), str(channel_id)))
        conn.commit()


def get_sticky(sticky_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM stickies WHERE id = ?", (sticky_id,)).fetchone()
    return dict(row) if row else None


def delete_sticky(sticky_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM stickies WHERE id = ?", (sticky_id,))
        conn.commit()
def list_trash(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT id, name, guild_id, deleted_at, updated_at FROM messages WHERE guild_id = ? AND deleted_at > 0 ORDER BY deleted_at DESC",
            (str(guild_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def restore_message(message_id):
    with closing(connect()) as conn:
        conn.execute("UPDATE messages SET deleted_at = 0 WHERE id = ?", (message_id,))
        conn.commit()
    return get_message(message_id)


def purge_message(message_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.execute("DELETE FROM versions WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM schedules WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM button_uses WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM click_counts WHERE message_id = ?", (message_id,))
        conn.execute("DELETE FROM form_responses WHERE message_id = ?", (message_id,))
        conn.commit()


def log_audit(guild_id, user_id, action, detail=""):
    now = int(time.time())
    with closing(connect()) as conn:
        conn.execute(
            "INSERT INTO audit_log (guild_id, user_id, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(guild_id or ""), str(user_id), str(action)[:60], str(detail)[:300], now),
        )
        conn.commit()


def list_audit(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE guild_id = ? ORDER BY created_at DESC LIMIT 100",
            (str(guild_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_server_settings(guild_id):
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM server_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    if not row:
        return {"guild_id": str(guild_id), "default_color": "#5865F2", "default_footer": "", "logo_url": "", "timezone": "UTC", "updated_at": 0}
    return dict(row)


def save_server_settings(guild_id, data):
    now = int(time.time())
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO server_settings (guild_id, default_color, default_footer, logo_url, timezone, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                default_color = excluded.default_color,
                default_footer = excluded.default_footer,
                logo_url = excluded.logo_url,
                timezone = excluded.timezone,
                updated_at = excluded.updated_at
            """,
            (
                str(guild_id),
                str(data.get("default_color") or "#5865F2")[:16],
                str(data.get("default_footer") or "")[:300],
                str(data.get("logo_url") or "")[:500],
                str(data.get("timezone") or "UTC")[:64],
                now,
            ),
        )
        conn.commit()
    return get_server_settings(guild_id)


def is_editor(guild_id, user_id):
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM guild_editors WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id)),
        ).fetchone()
    return row is not None


def list_editors(guild_id):
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM guild_editors WHERE guild_id = ? ORDER BY added_at DESC", (str(guild_id),)).fetchall()
    return [dict(r) for r in rows]


def add_editor(guild_id, user_id):
    now = int(time.time())
    with closing(connect()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO guild_editors (guild_id, user_id, added_at) VALUES (?, ?, ?)",
            (str(guild_id), str(user_id), now),
        )
        conn.commit()


def remove_editor(guild_id, user_id):
    with closing(connect()) as conn:
        conn.execute("DELETE FROM guild_editors WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
        conn.commit()


def delete_guild_data(guild_id):
    gid = str(guild_id)
    with closing(connect()) as conn:
        for tbl in (
            "messages", "templates", "schedules", "webhook_personas", "sequences",
            "form_responses", "automation_settings", "keyword_rules", "feeds",
            "stickies", "audit_log", "server_settings", "guild_editors",
        ):
            conn.execute(f"DELETE FROM {tbl} WHERE guild_id = ?", (gid,))
        conn.commit()