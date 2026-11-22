import discord
from fastapi import HTTPException

HOOK_NAME = "Griedon Embeds"


def need_permission(channel):
    return HTTPException(403, "I need the Manage Webhooks permission in #" + channel.name)


async def hooks_in(channel):
    try:
        return await channel.webhooks()
    except discord.Forbidden:
        raise need_permission(channel)


def is_mine(hook, client):
    return hook.user is not None and hook.user.id == client.user.id and bool(hook.token)


async def get_hook(client, channel):
    for hook in await hooks_in(channel):
        if is_mine(hook, client):
            return hook
    try:
        return await channel.create_webhook(name=HOOK_NAME, reason="Created by the dashboard")
    except discord.Forbidden:
        raise need_permission(channel)


async def find_hook(client, channel, webhook_id):
    for hook in await hooks_in(channel):
        if str(hook.id) == str(webhook_id) and is_mine(hook, client):
            return hook
    return None


async def send(client, channel, payload, persona, mentions):
    hook = await get_hook(client, channel)
    extra = {"username": persona["name"]}
    if persona.get("avatar_url"):
        extra["avatar_url"] = persona["avatar_url"]
    sent = await hook.send(wait=True, allowed_mentions=mentions, **extra, **payload)
    return hook, sent


async def edit(client, channel, webhook_id, message_id, payload, mentions):
    hook = await find_hook(client, channel, webhook_id)
    if hook is None:
        raise HTTPException(404, "The webhook that posted this message was deleted. Post it again.")
    await hook.edit_message(int(message_id), allowed_mentions=mentions, **payload)


async def delete(client, channel, webhook_id, message_id):
    hook = await find_hook(client, channel, webhook_id)
    if hook is not None:
        await hook.delete_message(int(message_id))
        return
    target = await channel.fetch_message(int(message_id))
    await target.delete()
