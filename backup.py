import asyncio
import base64
import json
import os
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import db

KEEP = 14
CHECK_EVERY = 3600
FOLDER = "backups"

_last_check = 0.0


def setting(name):
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


def github(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "griedon-embeds-backup")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            text = res.read().decode()
            return res.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as err:
        return err.code, None


def snapshot():
    """Copy data.db with SQLite's own backup function and return the bytes."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(db.DB_PATH)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def run_backup(force=False):
    """Blocking. Returns a short status text, or None when backups are not set up."""
    token = setting("GITHUB_BACKUP_TOKEN")
    repo = setting("GITHUB_BACKUP_REPO")
    if not token or not repo:
        return None
    if repo.count("/") != 1:
        return "Backup skipped: GITHUB_BACKUP_REPO must look like owner/name"

    code, info = github("GET", "/repos/" + repo, token)
    if code != 200 or not info:
        return f"Backup failed: cannot open the backup repo (HTTP {code})"
    if not info.get("private"):
        return "Backup refused: the backup repo must be PRIVATE"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = f"{FOLDER}/data-{today}.db"
    code, existing = github("GET", f"/repos/{repo}/contents/{path}", token)
    if code == 200 and not force:
        return None
    if code not in (200, 404):
        return f"Backup failed: could not check today's file (HTTP {code})"

    content = base64.b64encode(snapshot()).decode()
    body = {"message": "Backup " + today, "content": content}
    if code == 200 and existing:
        body["sha"] = existing["sha"]
    code, _ = github("PUT", f"/repos/{repo}/contents/{path}", token, body)
    if code not in (200, 201):
        return f"Backup failed: upload refused (HTTP {code})"

    code, listing = github("GET", f"/repos/{repo}/contents/{FOLDER}", token)
    if code == 200 and isinstance(listing, list):
        files = sorted(
            (f for f in listing if f["name"].startswith("data-") and f["name"].endswith(".db")),
            key=lambda f: f["name"],
        )
        for old in files[:-KEEP]:
            github(
                "DELETE",
                f"/repos/{repo}/contents/{old['path']}",
                token,
                {"message": "Remove old backup", "sha": old["sha"]},
            )
    return "Backup saved: " + path


async def maybe_backup():
    """Called from the scheduler loop. Checks at most once an hour."""
    global _last_check
    now = time.time()
    if now - _last_check < CHECK_EVERY:
        return
    _last_check = now
    result = await asyncio.to_thread(run_backup)
    if result:
        print(result)
