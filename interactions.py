import discord

import builder
import db


def walk_blocks(blocks):
    for b in blocks:
        yield b
        if b.get("type") == "box":
            yield from walk_blocks(b.get("children", []))


def find_button(layout, button_id):
    groups = [layout.get("blocks", [])]
    for reply in layout.get("replies", []):
        groups.append(reply.get("blocks", []))
    for blocks in groups:
        for b in walk_blocks(blocks):
            if b.get("type") == "button_row":
                for btn in b.get("buttons", []):
                    if btn.get("id") == button_id:
                        return btn
    return None


async def say(interaction, text):
    await interaction.response.send_message(text, ephemeral=True)


async def handle_role(interaction, action):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await say(interaction, "This button only works inside a server.")
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
        await member.add_roles(role, reason="Dashboard role button")
        await say(interaction, f"Added the {role.name} role.")
    else:
        if not has:
            await say(interaction, f"You don't have the {role.name} role.")
            return
        await member.remove_roles(role, reason="Dashboard role button")
        await say(interaction, f"Removed the {role.name} role.")


async def handle(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = (interaction.data or {}).get("custom_id", "")
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "m" or not parts[1].isdigit():
        return
    try:
        message = db.get_message(int(parts[1]))
        if not message:
            await say(interaction, "This message was deleted from the dashboard.")
            return
        layout = message["layout"]
        button = find_button(layout, parts[2])
        if not button:
            await say(interaction, "This button was removed from the message.")
            return
        action = button.get("action") or {}
        if action.get("type") == "reply":
            view = builder.build_reply_view(layout, message["id"], action.get("reply_id"))
            await interaction.response.send_message(view=view, ephemeral=True)
        elif action.get("type") == "role":
            await handle_role(interaction, action)
        else:
            await say(interaction, "This button has no action.")
    except ValueError as err:
        await say(interaction, "Could not open this: " + str(err))
    except discord.HTTPException as err:
        print("Interaction error:", err)
