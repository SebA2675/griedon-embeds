"""Variable substitution engine for Griedon Embeds.

Syntax: {{variable_name}} in message text fields.
Variables are defined per-message in layout.variables: [{id, name, default}].
Names must match [A-Za-z0-9_]{1,32}.
Substitution is safe: never applied to URL fields.
"""

import re

VAR_RE = re.compile(r"\{\{([A-Za-z0-9_]{1,32})\}\}")

# Fields in which substitution is allowed (text only, never URLs)
_TEXT_FIELDS = {
    "content", "title", "description", "author_name",
    "footer_text", "label", "placeholder", "thanks",
    "name", "value",
}

# Fields that are URL-like — must NEVER be substituted
_URL_FIELDS = {
    "url", "author_url", "author_icon", "image", "thumbnail",
    "footer_icon", "urls",
}


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
    """Replace {{name}} with the value from values dict (or leave as-is)."""
    return VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def substitute(layout: dict, values: dict[str, str]) -> dict:
    """Return a deep copy of layout with {{name}} substituted in text fields."""
    import json
    # Deep clone via JSON
    clone = json.loads(json.dumps(layout))
    _apply(clone, values)
    return clone


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