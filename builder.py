import discord

MAX_BUTTONS = 5
MAX_IMAGES = 10

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


def build_button(b, ctx):
    label = str(b.get("label", "")).strip()
    if not label:
        raise ValueError("A button has no label")
    action = b.get("action") or {"type": "link", "url": b.get("url", "")}
    kind = action.get("type")

    if kind == "link":
        url = str(action.get("url", "")).strip()
        if not url.startswith("https://"):
            raise ValueError("Button links must start with https://")
        return discord.ui.Button(style=discord.ButtonStyle.link, label=label[:80], url=url)

    if kind == "reply":
        if action.get("reply_id") not in ctx["reply_ids"]:
            raise ValueError("A button points to a reply that doesn't exist")
    elif kind == "role":
        if not str(action.get("role_id", "")).strip().isdigit():
            raise ValueError("A role button has no role chosen")
        if action.get("mode", "toggle") not in ("toggle", "add", "remove"):
            raise ValueError("A role button has a bad mode")
    else:
        raise ValueError("Unknown button action")

    style = STYLES.get(b.get("style"), discord.ButtonStyle.secondary)
    custom_id = f"m:{ctx['message_id']}:{b['id']}"
    return discord.ui.Button(style=style, label=label[:80], custom_id=custom_id)


def build_item(block, ctx):
    kind = block.get("type")

    if kind == "text":
        content = str(block.get("content", "")).strip()
        if not content:
            raise ValueError("A text part is empty")
        return discord.ui.TextDisplay(content)

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

    if kind == "box":
        box = discord.ui.Container(accent_colour=colour_from_hex(block.get("accent")))
        for child in block.get("children", []):
            if child.get("type") == "box":
                raise ValueError("A box can't contain another box")
            box.add_item(build_item(child, ctx))
        return box

    raise ValueError("Unknown part type")


def make_ctx(layout, message_id):
    ids = {r.get("id") for r in layout.get("replies", [])}
    return {"message_id": message_id, "reply_ids": ids}


def view_from_blocks(blocks, ctx):
    if not blocks:
        raise ValueError("Message is empty")
    view = discord.ui.LayoutView(timeout=None)
    for block in blocks:
        view.add_item(build_item(block, ctx))
    return view


def build_view(layout, message_id):
    ctx = make_ctx(layout, message_id)
    for reply in layout.get("replies", []):
        view_from_blocks(reply.get("blocks", []), ctx)
    return view_from_blocks(layout.get("blocks", []), ctx)


def build_reply_view(layout, message_id, reply_id):
    ctx = make_ctx(layout, message_id)
    for reply in layout.get("replies", []):
        if reply.get("id") == reply_id:
            return view_from_blocks(reply.get("blocks", []), ctx)
    raise ValueError("Reply not found")
