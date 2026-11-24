"""Variable substitution engine for Griedon Embeds.

Syntax: {{variable_name}} in message text fields.
Supports both per-message custom variables (layout.variables) and
permanent built-in Discord variables (userid, username, usermention,
servername, membercount, channelmention, date, time, etc.).
Substitution is safe: never applied to URL fields.
"""

from datetime import datetime, timezone
import json
import re

VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]{1,32})\}\}")

_TEXT_FIELDS = {
    "content", "title", "description", "author_name",
    "footer_text", "label", "placeholder", "thanks",
    "name", "value",
}

_URL_FIELDS = {
    "url", "author_url", "author_icon", "image", "thumbnail",
    "footer_icon", "urls",
}


def builtin_values(guild=None, channel=None, user=None) -> dict[str, str]:
    """Build dictionary of permanent Discord variables from live context."""
    now = datetime.now(timezone.utc)
    vals: dict[str, str] = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M UTC"),
        "timestamp": f"<t:{int(now.timestamp())}:f>",
    }
    if user is not None:
        global_nick = getattr(user, "global_name", None) or user.name
        nick = getattr(user, "display_name", None) or global_nick
        vals.update({
            "userid": str(user.id),
            "username": user.name,
            "usertag": str(user),
            "usermention": f"<@{user.id}>",
            "userglobalnickname": global_nick,
            "usernickname": nick,
        })
    if guild is not None:
        vals.update({
            "servername": guild.name,
            "serverid": str(guild.id),
            "membercount": str(guild.member_count or 0),
        })
    if channel is not None:
        vals.update({
            "channelname": channel.name,
            "channelid": str(channel.id),
            "channelmention": f"<#{channel.id}>",
        })
    return vals


def extract_variables(layout: dict) -> list[str]:
    """Return all {{name}} references found in text fields of the layout."""
    found: list[str] = []
    seen: set[str] = set()

    def _scan(obj):
        if isinstance(obj, str):
            for m in VAR_RE.finditer(obj):
                n = m.group(1)
                if n not in seen:
                    seen.add(n)
                    found.append(n)
        elif isinstance(obj, list):
            for item in obj:
                _scan(item)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k not in _URL_FIELDS:
                    _scan(v)

    _scan(layout)
    return found


def _sub(text: str, values: dict[str, str]) -> str:
    return VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def substitute(layout: dict, values: dict[str, str]) -> dict:
    clone = json.loads(json.dumps(layout))
    _apply(clone, values)
    return clone


def resolve_layout(layout: dict, guild=None, channel=None, user=None, overrides: dict[str, str] | None = None) -> dict:
    """Resolve custom defaults + built-in Discord variables + optional overrides."""
    merged: dict[str, str] = {}
    for v in layout.get("variables", []):
        if isinstance(v, dict) and v.get("name"):
            default_val = v.get("default")
            if isinstance(default_val, str) and default_val != "":
                merged[v["name"]] = default_val
    merged.update(builtin_values(guild=guild, channel=channel, user=user))
    if overrides:
        merged.update(overrides)
    if not merged:
        return layout
    return substitute(layout, merged)


def _apply(obj, values: dict[str, str]):
    if isinstance(obj, list):
        for item in obj:
            _apply(item, values)
    elif isinstance(obj, dict):
        for k in list(obj.keys()):
            if k in _URL_FIELDS:
                continue
            v = obj[k]
            if isinstance(v, str) and k in _TEXT_FIELDS:
                obj[k] = _sub(v, values)
            else:
                _apply(v, values)