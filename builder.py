import discord

MAX_BUTTONS = 5
MAX_IMAGES = 10


def colour_from_hex(value):
    try:
        return discord.Colour(int(str(value).lstrip("#"), 16))
    except ValueError:
        return discord.Colour(0x5865F2)


def build_item(block):
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
            label = str(b.get("label", "")).strip()
            url = str(b.get("url", "")).strip()
            if not label:
                raise ValueError("A button has no label")
            if not url.startswith("https://"):
                raise ValueError("Button links must start with https://")
            row.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label[:80], url=url))
        return row

    if kind == "box":
        box = discord.ui.Container(accent_colour=colour_from_hex(block.get("accent")))
        for child in block.get("children", []):
            if child.get("type") == "box":
                raise ValueError("A box can't contain another box")
            box.add_item(build_item(child))
        return box

    raise ValueError("Unknown part type")


def build_view(layout):
    blocks = layout.get("blocks", [])
    if not blocks:
        raise ValueError("Message is empty")
    view = discord.ui.LayoutView(timeout=None)
    for block in blocks:
        view.add_item(build_item(block))
    return view
