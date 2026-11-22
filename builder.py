import re

import discord

MAX_BUTTONS = 5
MAX_IMAGES = 10
MAX_OPTIONS = 25
NAME_RE = re.compile(r"^\w{1,32}$")

STYLES = {
    "grey": discord.ButtonStyle.secondary,
    "blue": discord.ButtonStyle.primary,
    "green": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
}


def colour_from_hex(value):
    try:
        return discord.Colour(int(str(value).lstrip("#"), 16))
    except ValueError:
        return discord.Colour(0x5865F2)


def make_emoji(data):
    if not data:
        return None
    emoji_id = str(data.get("id", ""))
    name = str(data.get("name", ""))
    if not emoji_id.isdigit() or not NAME_RE.match(name):
        raise ValueError("An emoji is invalid")
    return discord.PartialEmoji(name=name, id=int(emoji_id), animated=bool(data.get("animated")))


def check_action(action, ctx):
    kind = action.get("type")
    if kind == "reply":
        if action.get("reply_id") not in ctx["reply_ids"]:
            raise ValueError("Something points to a reply that doesn't exist")
    elif kind == "form":
        if action.get("form_id") not in ctx["form_ids"]:
            raise ValueError("Something points to a form that doesn't exist")
    elif kind == "role":
        if not str(action.get("role_id", "")).strip().isdigit():
            raise ValueError("A role action has no role chosen")
        if action.get("mode", "toggle") not in ("toggle", "add", "remove"):
            raise ValueError("A role action has a bad mode")
    else:
        raise ValueError("Unknown action")


def check_form(form):
    name = form.get("name") or "A form"
    fields = form.get("fields", [])
    if not 1 <= len(fields) <= 5:
        raise ValueError(f"{name} needs 1 to 5 questions")
    for f in fields:
        if not str(f.get("label", "")).strip():
            raise ValueError(f"{name} has a question with no label")
    if not str(form.get("channel_id", "")).strip().isdigit():
        raise ValueError(f"{name} has no channel chosen for the answers")


def build_button(b, ctx):
    label = str(b.get("label", "")).strip()
    emoji = make_emoji(b.get("emoji"))
    if not label and not emoji:
        raise ValueError("A button has no label or emoji")
    action = b.get("action") or {"type": "link", "url": b.get("url", "")}

    if action.get("type") == "link":
        url = str(action.get("url", "")).strip()
        if not url.startswith("https://"):
            raise ValueError("Button links must start with https://")
        return discord.ui.Button(
            style=discord.ButtonStyle.link, label=label[:80] or None, emoji=emoji, url=url
        )

    check_action(action, ctx)
    style = STYLES.get(b.get("style"), discord.ButtonStyle.secondary)
    custom_id = f"m:{ctx['message_id']}:{b['id']}"
    return discord.ui.Button(style=style, label=label[:80] or None, emoji=emoji, custom_id=custom_id)


def build_select(block, ctx):
    options = block.get("options", [])
    if not options:
        raise ValueError("A dropdown has no options")
    if len(options) > MAX_OPTIONS:
        raise ValueError("Max 25 options in a dropdown")
    items = []
    for o in options:
        label = str(o.get("label", "")).strip()
        if not label:
            raise ValueError("A dropdown option has no label")
        check_action(o.get("action") or {}, ctx)
        description = str(o.get("description", "")).strip()
        items.append(
            discord.SelectOption(
                label=label[:100],
                value=str(o["id"]),
                description=description[:100] or None,
                emoji=make_emoji(o.get("emoji")),
            )
        )
    placeholder = str(block.get("placeholder") or "Choose an option...")[:150]
    select = discord.ui.Select(
        placeholder=placeholder,
        options=items,
        custom_id=f"m:{ctx['message_id']}:{block['id']}",
        min_values=1,
        max_values=1,
    )
    return select


def build_dropdown(block, ctx):
    row = discord.ui.ActionRow()
    row.add_item(build_select(block, ctx))
    return row


def build_section(block, ctx):
    content = str(block.get("content", "")).strip()
    if not content:
        raise ValueError("A Text + side part has no text")
    text = discord.ui.TextDisplay(content)
    if block.get("side") == "thumbnail":
        url = str(block.get("image", "")).strip()
        if not url.startswith("https://"):
            raise ValueError("A thumbnail link must start with https://")
        accessory = discord.ui.Thumbnail(url)
    else:
        accessory = build_button(block.get("button") or {}, ctx)
    return discord.ui.Section(text, accessory=accessory)


def build_item(block, ctx):
    kind = block.get("type")

    if kind == "text":
        content = str(block.get("content", "")).strip()
        if not content:
            raise ValueError("A text part is empty")
        return discord.ui.TextDisplay(content)

    if kind == "section":
        return build_section(block, ctx)

    if kind == "images":
        urls = [str(u).strip() for u in block.get("urls", []) if str(u).strip()]
        if not urls:
            raise ValueError("An image part has no link")
        if len(urls) > MAX_IMAGES:
            raise ValueError("Max 10 images per part")
        for url in urls:
            if not url.startswith("https://"):
                raise ValueError("Image links must start with https://")
        return discord.ui.MediaGallery(*[discord.MediaGalleryItem(u) for u in urls])

    if kind == "divider":
        if block.get("spacing") == "large":
            spacing = discord.SeparatorSpacing.large
        else:
            spacing = discord.SeparatorSpacing.small
        return discord.ui.Separator(visible=bool(block.get("line", True)), spacing=spacing)

    if kind == "button_row":
        buttons = block.get("buttons", [])
        if not buttons:
            raise ValueError("A button row is empty")
        if len(buttons) > MAX_BUTTONS:
            raise ValueError("Max 5 buttons per row")
        row = discord.ui.ActionRow()
        for b in buttons:
            row.add_item(build_button(b, ctx))
        return row

    if kind == "dropdown":
        return build_dropdown(block, ctx)

    if kind == "box":
        box = discord.ui.Container(accent_colour=colour_from_hex(block.get("accent")))
        for child in block.get("children", []):
            if child.get("type") == "box":
                raise ValueError("A box can't contain another box")
            box.add_item(build_item(child, ctx))
        return box

    raise ValueError("Unknown part type")


def make_ctx(layout, message_id):
    return {
        "message_id": message_id,
        "reply_ids": {r.get("id") for r in layout.get("replies", [])},
        "form_ids": {f.get("id") for f in layout.get("forms", [])},
    }


def view_from_blocks(blocks, ctx):
    if not blocks:
        raise ValueError("Message is empty")
    view = discord.ui.LayoutView(timeout=None)
    for block in blocks:
        view.add_item(build_item(block, ctx))
    return view


def build_view(layout, message_id):
    ctx = make_ctx(layout, message_id)
    for form in layout.get("forms", []):
        check_form(form)
    for reply in layout.get("replies", []):
        view_from_blocks(reply.get("blocks", []), ctx)
    return view_from_blocks(layout.get("blocks", []), ctx)


def build_reply_view(layout, message_id, reply_id):
    ctx = make_ctx(layout, message_id)
    for reply in layout.get("replies", []):
        if reply.get("id") == reply_id:
            return view_from_blocks(reply.get("blocks", []), ctx)
    raise ValueError("Reply not found")


EMBED_TOTAL = 6000
MAX_EMBEDS = 10
MAX_FIELDS = 25


def https_or_none(value, what):
    value = str(value or "").strip()
    if not value:
        return None
    if not value.startswith("https://"):
        raise ValueError(what + " must start with https://")
    return value


def build_embed(e):
    title = str(e.get("title") or "").strip()
    desc = str(e.get("description") or "").strip()
    embed = discord.Embed(
        title=title or None,
        description=desc or None,
        url=https_or_none(e.get("url"), "An embed link"),
        colour=colour_from_hex(e.get("color")),
    )
    size = len(title) + len(desc)
    author = str(e.get("author_name") or "").strip()
    if author:
        embed.set_author(
            name=author[:256],
            url=https_or_none(e.get("author_url"), "An author link"),
            icon_url=https_or_none(e.get("author_icon"), "An author icon link"),
        )
        size += len(author)
    fields = e.get("fields", [])
    if len(fields) > MAX_FIELDS:
        raise ValueError("Max 25 fields in an embed")
    for f in fields:
        name = str(f.get("name") or "").strip()
        value = str(f.get("value") or "").strip()
        if not name or not value:
            raise ValueError("An embed field needs a name and a value")
        embed.add_field(name=name, value=value, inline=bool(f.get("inline")))
        size += len(name) + len(value)
    image = https_or_none(e.get("image"), "An embed image link")
    if image:
        embed.set_image(url=image)
    thumb = https_or_none(e.get("thumbnail"), "An embed thumbnail link")
    if thumb:
        embed.set_thumbnail(url=thumb)
    footer = str(e.get("footer_text") or "").strip()
    if footer:
        embed.set_footer(text=footer, icon_url=https_or_none(e.get("footer_icon"), "A footer icon link"))
        size += len(footer)
    if e.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()
    if not (title or desc or author or footer or fields or image or thumb):
        raise ValueError("An embed is empty")
    return embed, size


def build_classic(layout, ctx):
    content = str(layout.get("content") or "").strip()
    if len(content) > 2000:
        raise ValueError("Message text is over 2000 characters")
    raw = layout.get("embeds", [])
    if len(raw) > MAX_EMBEDS:
        raise ValueError("Max 10 embeds")
    embeds = []
    total = 0
    for e in raw:
        embed, size = build_embed(e)
        embeds.append(embed)
        total += size
    if total > EMBED_TOTAL:
        raise ValueError("Embeds are over 6000 characters in total")
    blocks = layout.get("blocks", [])
    if len(blocks) > 5:
        raise ValueError("Max 5 rows of buttons and dropdowns")
    view = discord.ui.View(timeout=None)
    for i, block in enumerate(blocks):
        kind = block.get("type")
        if kind == "button_row":
            buttons = block.get("buttons", [])
            if not buttons:
                raise ValueError("A button row is empty")
            if len(buttons) > MAX_BUTTONS:
                raise ValueError("Max 5 buttons per row")
            for b in buttons:
                item = build_button(b, ctx)
                item.row = i
                view.add_item(item)
        elif kind == "dropdown":
            item = build_select(block, ctx)
            item.row = i
            view.add_item(item)
        else:
            raise ValueError("Classic messages only support buttons and dropdowns")
    if not content and not embeds:
        raise ValueError("Add some text or an embed")
    return {"content": content or None, "embeds": embeds, "view": view}


def build_payload(layout, message_id):
    ctx = make_ctx(layout, message_id)
    for form in layout.get("forms", []):
        check_form(form)
    for reply in layout.get("replies", []):
        view_from_blocks(reply.get("blocks", []), ctx)
    if layout.get("kind") == "classic":
        return build_classic(layout, ctx)
    return {"view": view_from_blocks(layout.get("blocks", []), ctx)}
