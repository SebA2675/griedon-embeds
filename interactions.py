import discord

import builder
import db
import forms


def walk_blocks(blocks):
    for b in blocks:
        yield b
        if b.get("type") == "box":
            yield from walk_blocks(b.get("children", []))


def all_blocks(layout):
    yield from walk_blocks(layout.get("blocks", []))
    for reply in layout.get("replies", []):
        yield from walk_blocks(reply.get("blocks", []))


def find_button(layout, button_id):
    for b in all_blocks(layout):
        if b.get("type") == "button_row":
            for btn in b.get("buttons", []):
                if btn.get("id") == button_id:
                    return btn
        if b.get("type") == "section" and b.get("side") == "button":
            btn = b.get("button") or {}
            if btn.get("id") == button_id:
                return btn
    return None


def find_option(layout, block_id, option_id):
    for b in all_blocks(layout):
        if b.get("type") == "dropdown" and b.get("id") == block_id:
            for o in b.get("options", []):
                if o.get("id") == option_id:
                    return o
    return None


def find_form(layout, form_id):
    for f in layout.get("forms", []):
        if f.get("id") == form_id:
            return f
    return None


async def say(interaction, text):
    await interaction.response.send_message(text, ephemeral=True)


async def handle_role(interaction, action):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await say(interaction, "This only works inside a server.")
        return
    role = guild.get_role(int(action["role_id"]))
    if role is None:
        await say(interaction, "That role doesn't exist any more.")
        return
    me = guild.me
    if not me.guild_permissions.manage_roles or role >= me.top_role or role.managed:
        await say(interaction, "I can't manage that role. My role must be above it in Server Settings.")
        return
    member = interaction.user
    has = role in member.roles
    mode = action.get("mode", "toggle")
    if mode == "toggle":
        mode = "remove" if has else "add"
    if mode == "add":
        if has:
            await say(interaction, f"You already have the {role.name} role.")
            return
        await member.add_roles(role, reason="Dashboard role action")
        await say(interaction, f"Added the {role.name} role.")
    else:
        if not has:
            await say(interaction, f"You don't have the {role.name} role.")
            return
        await member.remove_roles(role, reason="Dashboard role action")
        await say(interaction, f"Removed the {role.name} role.")


async def run_action(interaction, message, action):
    kind = action.get("type")
    if kind == "reply":
        view = builder.build_reply_view(message["layout"], message["id"], action.get("reply_id"))
        await interaction.response.send_message(view=view, ephemeral=True)
    elif kind == "role":
        await handle_role(interaction, action)
    elif kind == "form":
        form = find_form(message["layout"], action.get("form_id"))
        if not form:
            await say(interaction, "This form was removed from the message.")
            return
        await interaction.response.send_modal(forms.FormModal(form))
    else:
        await say(interaction, "This has no action.")


async def handle(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    data = interaction.data or {}
    parts = data.get("custom_id", "").split(":")
    if len(parts) != 3 or parts[0] != "m" or not parts[1].isdigit():
        return
    try:
        message = db.get_message(int(parts[1]))
        if not message:
            await say(interaction, "This message was deleted from the dashboard.")
            return
        layout = message["layout"]
        if data.get("component_type") == 3:
            values = data.get("values") or []
            option = find_option(layout, parts[2], values[0]) if values else None
            if not option:
                await say(interaction, "This option was removed from the message.")
                return
            await run_action(interaction, message, option.get("action") or {})
        else:
            button = find_button(layout, parts[2])
            if not button:
                await say(interaction, "This button was removed from the message.")
                return
            await run_action(interaction, message, button.get("action") or {})
    except ValueError as err:
        await say(interaction, "Could not open this: " + str(err))
    except discord.HTTPException as err:
        print("Interaction error:", err)
