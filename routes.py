import json
import time

import discord
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import access
import builder
import db
import timeutil

MAX_PER_GUILD = 200
MAX_TEMPLATES = 100
MAX_IMPORT_ITEMS = 100
MAX_IMPORT_BYTES = 3000000
MAX_SCHEDULES_PER_MESSAGE = 20
MAX_SCHEDULES_PER_GUILD = 100


class MessageIn(BaseModel):
    name: str
    layout: dict


class SendIn(BaseModel):
    guild_id: str
    channel_id: str


class AllowIn(BaseModel):
    guild_id: str
    name: str = ""


class ImportIn(BaseModel):
    data: dict


class ScheduleIn(BaseModel):
    action: str
    channel_id: str = ""
    run_date: str
    run_time: str
    tz: str
    repeat: str = "once"
    replace_previous: bool = False


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
    return str(name).strip()[:100] or "Untitled message"


def strip_guild_refs(x):
    if isinstance(x, list):
        for item in x:
            strip_guild_refs(item)
    elif isinstance(x, dict):
        if "role_id" in x:
            x["role_id"] = ""
        if "fields" in x and "channel_id" in x:
            x["channel_id"] = ""
        for value in x.values():
            strip_guild_refs(value)


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

    # ----- Messages -----

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

    # ----- Servers -----

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

    # ----- Posting -----

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
        db.add_version(message_id, found["name"], found["layout"])
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
        db.add_version(message_id, found["name"], found["layout"])
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

    # ----- Version history -----

    @router.get("/messages/{message_id}/versions")
    async def versions(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        await load_message(message_id, uid)
        return db.list_versions(message_id)

    @router.get("/messages/{message_id}/versions/{version_id}")
    async def one_version(message_id: int, version_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        await load_message(message_id, uid)
        found = db.get_version(message_id, version_id)
        if not found:
            raise HTTPException(404, "Version not found")
        return found

    # ----- Schedules -----

    @router.get("/messages/{message_id}/schedules")
    async def list_schedules(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        await load_message(message_id, uid)
        return db.list_schedules(message_id)

    @router.post("/messages/{message_id}/schedules")
    async def add_schedule(message_id: int, body: ScheduleIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        gid = found["guild_id"]
        if not gid:
            raise HTTPException(400, "Choose a server for this message first")
        guild = await user_guild(uid, gid)
        if body.action not in ("post", "update"):
            raise HTTPException(400, "Unknown action")
        if body.repeat not in ("once", "daily", "weekly"):
            raise HTTPException(400, "Unknown repeat option")
        channel_id = ""
        if body.action == "post":
            channel = find_channel(get_client(), gid, body.channel_id)
            channel_id = str(channel.id)
        check_form_channels(found["layout"], guild)
        make_view(found["layout"], message_id)
        try:
            run_date, next_run = timeutil.first_run(body.run_date, body.run_time, body.tz, body.repeat)
        except ValueError as err:
            raise HTTPException(400, str(err))
        if db.count_schedules_message(message_id) >= MAX_SCHEDULES_PER_MESSAGE:
            raise HTTPException(400, "This message has too many schedules. Delete some first.")
        if db.count_schedules_guild(gid) >= MAX_SCHEDULES_PER_GUILD:
            raise HTTPException(400, "This server has reached the schedule limit")
        return db.create_schedule(
            message_id, gid, body.action, channel_id, run_date, body.run_time, body.tz,
            body.repeat, body.replace_previous and body.action == "post", next_run,
        )

    @router.delete("/schedules/{schedule_id}")
    async def remove_schedule(schedule_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = db.get_schedule(schedule_id)
        if not found:
            raise HTTPException(404, "Schedule not found")
        await user_guild(uid, found["guild_id"])
        db.delete_schedule(schedule_id)
        return {"deleted": True}

    # ----- Export, backup, import -----

    @router.get("/messages/{message_id}/export")
    async def export_message(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        return {
            "format": "embed-dashboard-message",
            "version": 1,
            "guild_id": found["guild_id"],
            "name": found["name"],
            "layout": found["layout"],
        }

    @router.get("/guilds/{guild_id}/backup")
    async def backup(guild_id: str, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        gid = str(guild.id)
        return {
            "format": "embed-dashboard-backup",
            "version": 1,
            "guild_id": gid,
            "exported_at": int(time.time()),
            "messages": [{"name": m["name"], "layout": m["layout"]} for m in db.list_messages_full(gid)],
            "templates": [{"name": t["name"], "layout": t["layout"]} for t in db.list_templates_full(gid)],
        }

    @router.post("/guilds/{guild_id}/import")
    async def import_data(guild_id: str, body: ImportIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        gid = str(guild.id)
        data = body.data
        if len(json.dumps(data)) > MAX_IMPORT_BYTES:
            raise HTTPException(400, "That file is too large")
        source = str(data.get("guild_id") or "")
        foreign = source != gid

        kind = data.get("format")
        if kind == "embed-dashboard-message":
            items = [{"name": data.get("name"), "layout": data.get("layout")}]
            temps = []
        elif kind == "embed-dashboard-backup":
            items = data.get("messages", [])
            temps = data.get("templates", [])
        else:
            raise HTTPException(400, "This file isn't an export from this dashboard")
        if not isinstance(items, list) or not isinstance(temps, list):
            raise HTTPException(400, "The file is damaged")
        if len(items) > MAX_IMPORT_ITEMS or len(temps) > MAX_IMPORT_ITEMS:
            raise HTTPException(400, "Too many items in one file")
        if db.count_messages(gid) + len(items) > MAX_PER_GUILD:
            raise HTTPException(400, "This would go over the saved-message limit for this server")
        if db.count_templates(gid) + len(temps) > MAX_TEMPLATES:
            raise HTTPException(400, "This would go over the template limit for this server")

        def prepare(entries):
            ready = []
            for entry in entries:
                layout = entry.get("layout") if isinstance(entry, dict) else None
                if not isinstance(layout, dict):
                    raise HTTPException(400, "The file is damaged")
                check_layout(layout)
                ready.append((clean_name(entry.get("name") or "Imported message"), layout))
            return ready

        ready_messages = prepare(items)
        ready_templates = prepare(temps)
        for name, layout in ready_messages:
            layout["guild_id"] = gid
            if foreign:
                strip_guild_refs(layout)
            db.create_message(name, layout, gid)
        for name, layout in ready_templates:
            layout["guild_id"] = gid
            if foreign:
                strip_guild_refs(layout)
            db.create_template(gid, name, layout)
        return {"messages": len(ready_messages), "templates": len(ready_templates), "stripped": foreign}

    # ----- Templates -----

    @router.get("/guilds/{guild_id}/templates")
    async def list_templates(guild_id: str, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        return db.list_templates(str(guild.id))

    @router.post("/guilds/{guild_id}/templates")
    async def make_template(guild_id: str, body: MessageIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        gid = str(guild.id)
        check_layout(body.layout)
        if db.count_templates(gid) >= MAX_TEMPLATES:
            raise HTTPException(400, "This server has reached the template limit")
        layout = dict(body.layout)
        layout["guild_id"] = gid
        return db.create_template(gid, clean_name(body.name), layout)

    @router.get("/templates/{template_id}")
    async def read_template(template_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = db.get_template(template_id)
        if not found:
            raise HTTPException(404, "Template not found")
        await user_guild(uid, found["guild_id"])
        return found

    @router.delete("/templates/{template_id}")
    async def remove_template(template_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = db.get_template(template_id)
        if not found:
            raise HTTPException(404, "Template not found")
        await user_guild(uid, found["guild_id"])
        db.delete_template(template_id)
        return {"deleted": True}

    # ----- Owner-only server list -----

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
