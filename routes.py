import json

import discord
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import builder
import db


class MessageIn(BaseModel):
    name: str
    layout: dict


class SendIn(BaseModel):
    guild_id: str
    channel_id: str


def check_layout(layout):
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        raise HTTPException(400, "layout must contain a list called blocks")
    replies = layout.get("replies", [])
    if not isinstance(replies, list) or len(replies) > 25:
        raise HTTPException(400, "replies must be a list of at most 25")
    forms = layout.get("forms", [])
    if not isinstance(forms, list) or len(forms) > 25:
        raise HTTPException(400, "forms must be a list of at most 25")
    if len(json.dumps(layout)) > 200000:
        raise HTTPException(400, "Message is too large")


def clean_name(name):
    return name.strip()[:100] or "Untitled message"


def make_view(layout, message_id):
    try:
        return builder.build_view(layout, message_id)
    except ValueError as err:
        raise HTTPException(400, str(err))


def get_guild(client, guild_id):
    try:
        guild = client.get_guild(int(guild_id))
    except ValueError:
        raise HTTPException(400, "Bad server id")
    if not guild:
        raise HTTPException(404, "Server not found")
    return guild


def find_channel(client, guild_id, channel_id):
    try:
        channel = client.get_channel(int(channel_id))
    except (TypeError, ValueError):
        raise HTTPException(400, "Bad channel id")
    if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != str(guild_id):
        raise HTTPException(404, "Channel not found")
    perms = channel.permissions_for(channel.guild.me)
    if not (perms.view_channel and perms.send_messages):
        raise HTTPException(403, "The bot can't send messages in that channel")
    return channel


def discord_error(err):
    return HTTPException(400, "Discord refused the message: " + str(err)[:300])


def make_router(require_owner, get_client):
    router = APIRouter()

    @router.get("/messages")
    def list_all(authorization: str = Header(default=None)):
        require_owner(authorization)
        return db.list_messages()

    @router.post("/messages")
    def create(body: MessageIn, authorization: str = Header(default=None)):
        require_owner(authorization)
        check_layout(body.layout)
        return db.create_message(clean_name(body.name), body.layout)

    @router.get("/messages/{message_id}")
    def read_one(message_id: int, authorization: str = Header(default=None)):
        require_owner(authorization)
        found = db.get_message(message_id)
        if not found:
            raise HTTPException(404, "Message not found")
        return found

    @router.put("/messages/{message_id}")
    def save(message_id: int, body: MessageIn, authorization: str = Header(default=None)):
        require_owner(authorization)
        check_layout(body.layout)
        updated = db.update_message(message_id, clean_name(body.name), body.layout)
        if not updated:
            raise HTTPException(404, "Message not found")
        return updated

    @router.delete("/messages/{message_id}")
    def remove(message_id: int, authorization: str = Header(default=None)):
        require_owner(authorization)
        if not db.delete_message(message_id):
            raise HTTPException(404, "Message not found")
        return {"deleted": True}

    @router.get("/guilds")
    def guilds(authorization: str = Header(default=None)):
        require_owner(authorization)
        return [{"id": str(g.id), "name": g.name} for g in get_client().guilds]

    @router.get("/guilds/{guild_id}/channels")
    def channels(guild_id: str, authorization: str = Header(default=None)):
        require_owner(authorization)
        guild = get_guild(get_client(), guild_id)
        result = []
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.send_messages:
                category = ch.category.name if ch.category else None
                result.append({"id": str(ch.id), "name": ch.name, "category": category})
        return result

    @router.get("/guilds/{guild_id}/roles")
    def roles(guild_id: str, authorization: str = Header(default=None)):
        require_owner(authorization)
        guild = get_guild(get_client(), guild_id)
        me = guild.me
        result = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed:
                continue
            can = bool(me.guild_permissions.manage_roles and role < me.top_role)
            result.append({"id": str(role.id), "name": role.name, "assignable": can})
        return result

    @router.get("/emojis")
    def all_emojis(authorization: str = Header(default=None)):
        require_owner(authorization)
        result = []
        for g in get_client().guilds:
            items = [
                {"id": str(e.id), "name": e.name, "animated": e.animated}
                for e in g.emojis
                if e.available
            ]
            if items:
                result.append({"guild_id": str(g.id), "guild_name": g.name, "emojis": items})
        return result

    @router.post("/messages/{message_id}/post")
    async def post_message(message_id: int, body: SendIn, authorization: str = Header(default=None)):
        require_owner(authorization)
        found = db.get_message(message_id)
        if not found:
            raise HTTPException(404, "Message not found")
        channel = find_channel(get_client(), body.guild_id, body.channel_id)
        view = make_view(found["layout"], message_id)
        try:
            sent = await channel.send(view=view)
        except discord.HTTPException as err:
            raise discord_error(err)
        db.set_posted(message_id, body.guild_id, body.channel_id, str(sent.id))
        return db.get_message(message_id)

    @router.post("/messages/{message_id}/update")
    async def update_posted(message_id: int, authorization: str = Header(default=None)):
        require_owner(authorization)
        found = db.get_message(message_id)
        if not found:
            raise HTTPException(404, "Message not found")
        if not found["posted_message_id"]:
            raise HTTPException(400, "This message hasn't been posted yet")
        channel = find_channel(get_client(), found["guild_id"], found["channel_id"])
        view = make_view(found["layout"], message_id)
        try:
            target = await channel.fetch_message(int(found["posted_message_id"]))
            await target.edit(view=view)
        except discord.NotFound:
            raise HTTPException(404, "The posted message was deleted. Post it again.")
        except discord.HTTPException as err:
            raise discord_error(err)
        return db.get_message(message_id)

    return router
