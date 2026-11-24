import time

import discord

import builder
import db
import forms
import variables

_page_state: dict[int, int] = {}


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


def wait_text(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def rule_problem(interaction, message, key, item):
    allowed = [str(r) for r in (item.get("allowed_roles") or [])]
    if allowed:
        member = interaction.user
        if not isinstance(member, discord.Member) or interaction.guild is None:
            return "This only works inside a server."
        if not {str(r.id) for r in member.roles} & set(allowed):
            names = []
            for rid in allowed:
                role = interaction.guild.get_role(int(rid)) if rid.isdigit() else None
                if role:
                    names.append(role.name)
            return "You need one of these roles to use this: " + (", ".join(names) or "(role removed)")
    expires_at = int(item.get("expires_at") or 0)
    if expires_at > 0 and time.time() > expires_at:
        return "This button has expired."
    max_uses = int(item.get("max_uses") or 0)
    if max_uses > 0 and db.get_click_count(message["id"], key) >= max_uses:
        return "This button has reached its maximum number of uses."
    cooldown = int(item.get("cooldown") or 0)
    once = bool(item.get("once"))
    if cooldown > 0 or once:
        used = db.get_use(message["id"], key, interaction.user.id)
        if used:
            if once:
                return "You can only use this once."
            left = used["last_used"] + cooldown - int(time.time())
            if left > 0:
                return "Please wait " + wait_text(left) + " before using this again."
    return None


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
    if role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_roles:
        await say(interaction, "For safety, buttons can't give roles that have Administrator, Manage Server or Manage Roles.")
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


async def run_action(interaction, message, action, on_done=None):
    kind = action.get("type")
    if kind == "reply":
        resolved = variables.resolve_layout(message["layout"], guild=interaction.guild, channel=interaction.channel, user=interaction.user)
        view = builder.build_reply_view(resolved, message["id"], action.get("reply_id"))
        await interaction.response.send_message(view=view, ephemeral=True)
    elif kind == "role":
        await handle_role(interaction, action)
    elif kind == "form":
        form = find_form(message["layout"], action.get("form_id"))
        if not form:
            await say(interaction, "This form was removed from the message.")
            return
        await interaction.response.send_modal(forms.FormModal(form, on_done, message_id=message["id"]))
    elif kind == "page":
        pages = message["layout"].get("pages") or []
        total = 1 + len(pages)
        if total <= 1:
            await say(interaction, "This message only has 1 page.")
            return
        msg_id = interaction.message.id if interaction.message else 0
        cur_idx = _page_state.get(msg_id, 0)
        req = int(action.get("page_index", 0))
        if req == -2:
            target = (cur_idx + 1) % total
        elif req == -1:
            target = (cur_idx - 1) % total
        else:
            target = max(0, min(req, total - 1))
        _page_state[msg_id] = target
        resolved = variables.resolve_layout(
            message["layout"],
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
        )
        payload = builder.build_payload(resolved, message["id"], page_index=target)
        await interaction.response.edit_message(**payload)
    else:
        await say(interaction, "This has no action.")


async def use_item(interaction, message, key, item):
    problem = rule_problem(interaction, message, key, item)
    if problem:
        await say(interaction, problem)
        return
    action = item.get("action") or {}
    counted = int(item.get("cooldown") or 0) > 0 or bool(item.get("once"))
    if action.get("type") == "form":
        done = None
        if counted:
            user_id = interaction.user.id
            done = lambda: db.record_use(message["id"], key, user_id)
        await run_action(interaction, message, action, done)
        return
    db.increment_click(message["id"], key)
    if counted:
        db.record_use(message["id"], key, interaction.user.id)
    actions = item.get("actions")
    if isinstance(actions, list) and actions:
        for act in actions:
            if not interaction.response.is_done():
                await run_action(interaction, message, act)
            elif act.get("type") == "reply":
                resolved = variables.resolve_layout(message["layout"], guild=interaction.guild, channel=interaction.channel, user=interaction.user)
                view = builder.build_reply_view(resolved, message["id"], act.get("reply_id"))
                await interaction.followup.send(view=view, ephemeral=True)
    else:
        await run_action(interaction, message, action)


async def handle(interaction):
    if interaction.type != discord.InteractionType.component:
        return
    data = interaction.data or {}
    parts = data.get("custom_id", "").split(":")
    if len(parts) != 3 or parts[0] != "m" or not parts[1].isdigit():
        return
    try:
        message = db.get_message_cached(int(parts[1]))
        if not message:
            await say(interaction, "This message was deleted from the dashboard.")
            return
        gid = message.get("guild_id")
        if gid and str(interaction.guild_id) != str(gid):
            await say(interaction, "This message belongs to another server.")
            return
        layout = message["layout"]
        if data.get("component_type") == 3:
            values = data.get("values") or []
            if not values:
                return
            for val in values:
                option = find_option(layout, parts[2], val)
                if option and not interaction.response.is_done():
                    await use_item(interaction, message, parts[2] + "." + str(option.get("id")), option)
                elif option and (option.get("action") or {}).get("type") == "role":
                    await handle_role(interaction, option.get("action"))
        else:
            button = find_button(layout, parts[2])
            if not button:
                await say(interaction, "This button was removed from the message.")
                return
            await use_item(interaction, message, parts[2], button)
    except ValueError as err:
        await say(interaction, "Could not open this: " + str(err))
    except discord.HTTPException as err:
        print("Interaction error:", err)
