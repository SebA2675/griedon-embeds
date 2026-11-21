import json

import discord
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import access
import builder
import db

MAX_PER_GUILD = 200


class MessageIn(BaseModel):
    name: str
    layout: dict


class SendIn(BaseModel):
    guild_id: str
    channel_id: str


class AllowIn(BaseModel):
    guild_id: str
    name: str = ""


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


def check_form_channels(layout, guild):
    for form in layout.get("forms", []):
        cid = str(form.get("channel_id", ""))
        if not cid.isdigit() or guild.get_channel(int(cid)) is None:
            name = form.get("name", "")
            raise HTTPException(400, f'Form "{name}" sends answers to a channel outside this server')


def discord_error(err):
    return HTTPException(400, "Discord refused the message: " + str(err)[:300])


def make_router(require_user, get_client, owner_id):
    router = APIRouter()

    async def user_guild(uid, guild_id):
        return await access.require_guild(get_client(), uid, owner_id, guild_id)

    async def load_message(message_id, uid):
        found = db.get_message(message_id)
        if not found:
            raise HTTPException(404, "Message not found")
        if found["guild_id"]:
            await user_guild(uid, found["guild_id"])
        elif uid != owner_id:
            raise HTTPException(403, "You can't open this message")
        return found

    def require_owner_user(authorization):
        uid = require_user(authorization)
        if uid != owner_id:
            raise HTTPException(403, "Only the bot owner can do this")
        return uid

    @router.get("/messages")
    async def list_all(authorization: str = Header(default=None)):
        uid = require_user(authorization)
        if uid == owner_id:
            return db.list_messages(None)
        mine = await access.accessible_guilds(get_client(), uid, owner_id)
        return db.list_messages([str(g.id) for g in mine])

    @router.post("/messages")
    async def create(body: MessageIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        check_layout(body.layout)
        gid = str(body.layout.get("guild_id") or "")
        await user_guild(uid, gid)
        if db.count_messages(gid) >= MAX_PER_GUILD:
            raise HTTPException(400, "This server has reached the limit of saved messages")
        return db.create_message(clean_name(body.name), body.layout, gid)

    @router.get("/messages/{message_id}")
    async def read_one(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        return await load_message(message_id, uid)

    @router.put("/messages/{message_id}")
    async def save(message_id: int, body: MessageIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        check_layout(body.layout)
        gid = str(body.layout.get("guild_id") or found["guild_id"] or "")
        await user_guild(uid, gid)
        updated = db.update_message(message_id, clean_name(body.name), body.layout, gid)
        if not updated:
            raise HTTPException(404, "Message not found")
        return updated

    @router.delete("/messages/{message_id}")
    async def remove(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        await load_message(message_id, uid)
        db.delete_message(message_id)
        return {"deleted": True}

    @router.get("/guilds")
    async def guilds(authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await access.accessible_guilds(get_client(), uid, owner_id)
        return [{"id": str(g.id), "name": g.name} for g in found]

    @router.get("/guilds/{guild_id}/channels")
    async def channels(guild_id: str, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        result = []
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.send_messages:
                category = ch.category.name if ch.category else None
                result.append({"id": str(ch.id), "name": ch.name, "category": category})
        return result

    @router.get("/guilds/{guild_id}/roles")
    async def roles(guild_id: str, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        me = guild.me
        result = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed:
                continue
            can = bool(me.guild_permissions.manage_roles and role < me.top_role)
            result.append({"id": str(role.id), "name": role.name, "assignable": can})
        return result

    @router.get("/emojis")
    async def all_emojis(authorization: str = Header(default=None)):
        uid = require_user(authorization)
        result = []
        for g in await access.accessible_guilds(get_client(), uid, owner_id):
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
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        if not found["guild_id"]:
            raise HTTPException(400, "Choose a server for this message first")
        if str(body.guild_id) != found["guild_id"]:
            raise HTTPException(400, "This message belongs to another server")
        guild = await user_guild(uid, found["guild_id"])
        check_form_channels(found["layout"], guild)
        channel = find_channel(get_client(), found["guild_id"], body.channel_id)
        view = make_view(found["layout"], message_id)
        try:
            sent = await channel.send(view=view)
        except discord.HTTPException as err:
            raise discord_error(err)
        db.set_posted(message_id, found["guild_id"], str(channel.id), str(sent.id))
        return db.get_message(message_id)

    @router.post("/messages/{message_id}/update")
    async def update_posted(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        if not found["posted_message_id"]:
            raise HTTPException(400, "This message hasn't been posted yet")
        guild = await user_guild(uid, found["guild_id"])
        check_form_channels(found["layout"], guild)
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

    @router.delete("/messages/{message_id}/posted")
    async def delete_posted(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        if not found["posted_message_id"]:
            raise HTTPException(400, "This message hasn't been posted yet")
        channel = find_channel(get_client(), found["guild_id"], found["channel_id"])
        try:
            target = await channel.fetch_message(int(found["posted_message_id"]))
            await target.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as err:
            raise discord_error(err)
        db.clear_posted(message_id)
        return db.get_message(message_id)

    @router.get("/admin/servers")
    async def admin_servers(authorization: str = Header(default=None)):
        require_owner_user(authorization)
        joined = {str(g.id): g.name for g in get_client().guilds}
        allowed = []
        for row in db.list_allowed():
            gid = row["guild_id"]
            allowed.append({"id": gid, "name": joined.get(gid) or row["name"], "joined": gid in joined})
        return {"allowed": allowed, "pending": db.list_pending()}

    @router.post("/admin/allow")
    async def admin_allow(body: AllowIn, authorization: str = Header(default=None)):
        require_owner_user(authorization)
        gid = body.guild_id.strip()
        if not gid.isdigit():
            raise HTTPException(400, "A server ID is only numbers")
        db.allow_guild(gid, body.name.strip()[:100])
        return {"ok": True}

    @router.delete("/admin/allow/{guild_id}")
    async def admin_remove(guild_id: str, authorization: str = Header(default=None)):
        require_owner_user(authorization)
        if not guild_id.isdigit():
            raise HTTPException(400, "Bad server id")
        db.remove_allowed(guild_id)
        guild = get_client().get_guild(int(guild_id))
        if guild is not None:
            db.add_pending(guild_id, guild.name)
            await guild.leave()
        return {"ok": True}

    @router.delete("/admin/pending/{guild_id}")
    async def admin_dismiss(guild_id: str, authorization: str = Header(default=None)):
        require_owner_user(authorization)
        db.remove_pending(guild_id)
        return {"ok": True}

    return router
