
import json
import re
import time

import discord
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import access
import builder
import db
import timeutil
import webhooks

MAX_PER_GUILD = 200
MAX_TEMPLATES = 100
MAX_IMPORT_ITEMS = 100
MAX_IMPORT_BYTES = 3000000
MAX_SCHEDULES_PER_MESSAGE = 20
MAX_SCHEDULES_PER_GUILD = 100

SAFE_MENTIONS = discord.AllowedMentions(
    everyone=False,
    roles=True,
    users=True,
    replied_user=False
)
MAX_PERSONAS = 25


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


class PersonaIn(BaseModel):
    name: str
    avatar_url: str = ""


class HookSendIn(BaseModel):
    guild_id: str
    channel_id: str
    persona_id: int


ID_RE = re.compile(r"^[A-Za-z0-9\_-]{1,40}$")
BLOCK_TYPES = {"text", "section", "images", "divider", "box", "button_row", "dropdown"}
BUTTON_STYLES = {"grey", "blue", "green", "red"}


def bad(msg):
    raise HTTPException(400, msg)


def v_id(x, what):
    if not isinstance(x, str) or not ID_RE.match(x):
        bad("Invalid " + what + " id")


def v_str(x, what, limit):
    if not isinstance(x, str):
        bad(what + " must be text")
    if len(x) > limit:
        bad(what + " is too long")


def v_url(x, what):
    v_str(x, what, 2000)
    if x and not x.startswith("https://"):
        bad(what + " must start with https://")


def v_digits(x, what):
    if x == "":
        return
    if not isinstance(x, str) or not x.isdigit() or len(x) > 25:
        bad("Invalid " + what)


def v_emoji(e):
    if e is None:
        return
    if not isinstance(e, dict):
        bad("Invalid emoji")
    eid = str(e.get("id", ""))
    name = str(e.get("name", ""))
    if not eid.isdigit() or len(eid) > 25 or not re.match(r"^\w{1,32}$", name):
        bad("Invalid emoji")


def v_action(a):
    if not isinstance(a, dict):
        bad("Invalid action")
    kind = a.get("type")
    if kind == "link":
        v_url(a.get("url", ""), "Link")
    elif kind in ("reply", "form"):
        ref = a.get("reply_id" if kind == "reply" else "form_id", "")
        if ref != "":
            v_id(ref, kind)
    elif kind == "role":
        v_digits(a.get("role_id", ""), "role")
        if a.get("mode", "toggle") not in ("toggle", "add", "remove"):
            bad("Invalid role mode")
    else:
        bad("Unknown action type")


def v_button(b):
    if not isinstance(b, dict):
        bad("Invalid button")
    v_id(b.get("id"), "button")
    v_str(b.get("label", ""), "Button label", 80)
    if b.get("style", "grey") not in BUTTON_STYLES:
        bad("Invalid button colour")
    v_emoji(b.get("emoji"))
    v_action(b.get("action") or {"type": "link", "url": b.get("url", "")})


def v_block(b, in_box=False):
    if not isinstance(b, dict):
        bad("Invalid part")
    v_id(b.get("id"), "part")
    kind = b.get("type")
    if kind not in BLOCK_TYPES:
        bad("Unknown part type")

    if kind == "text":
        v_str(b.get("content", ""), "Text", 8000)

    elif kind == "section":
        v_str(b.get("content", ""), "Text", 8000)
        side = b.get("side", "button")
        if side not in ("button", "thumbnail"):
            bad("Invalid side option")
        if side == "thumbnail":
            v_url(b.get("image", ""), "Thumbnail link")
        else:
            v_button(b.get("button"))

    elif kind == "images":
        urls = b.get("urls", [])
        if not isinstance(urls, list) or len(urls) > 20:
            bad("Too many images in one part")
        for u in urls:
            v_url(u, "Image link")

    elif kind == "divider":
        if b.get("spacing", "small") not in ("small", "large"):
            bad("Invalid divider spacing")

    elif kind == "box":
        if in_box:
            bad("A box can't contain another box")
        if not re.match(r"^#[0-9a-fA-F]{6}$", str(b.get("accent", "#5865F2"))):
            bad("Invalid box colour")
        kids = b.get("children", [])
        if not isinstance(kids, list) or len(kids) > 40:
            bad("Too many parts in a box")
        for c in kids:
            v_block(c, True)

    elif kind == "button_row":
        btns = b.get("buttons", [])
        if not isinstance(btns, list) or len(btns) > 10:
            bad("Too many buttons in a row")
        for x in btns:
            v_button(x)

    elif kind == "dropdown":
        v_str(b.get("placeholder", ""), "Placeholder", 150)
        opts = b.get("options", [])
        if not isinstance(opts, list) or len(opts) > 50:
            bad("Too many dropdown options")
        for o in opts:
            if not isinstance(o, dict):
                bad("Invalid option")
            v_id(o.get("id"), "option")
            v_str(o.get("label", ""), "Option label", 100)
            v_str(o.get("description", ""), "Option description", 100)
            v_emoji(o.get("emoji"))
            v_action(o.get("action"))


def v_embed(e):
    if not isinstance(e, dict):
        bad("Invalid embed")
    v_id(e.get("id"), "embed")
    for key, limit in (
        ("title", 256),
        ("description", 4096),
        ("author_name", 256),
        ("footer_text", 2048)
    ):
        v_str(e.get(key, ""), "Embed " + key.replace("_", " "), limit)

    for key in ("url", "author_url", "author_icon", "image", "thumbnail", "footer_icon"):
        v_url(e.get(key, ""), "Embed link")

    if not re.match(r"^#[0-9a-fA-F]{6}$", str(e.get("color", "#5865F2"))):
        bad("Invalid embed colour")

    fields = e.get("fields", [])
    if not isinstance(fields, list) or len(fields) > 25:
        bad("Too many embed fields")

    for f in fields:
        if not isinstance(f, dict):
            bad("Invalid embed field")
        v_id(f.get("id"), "field")
        v_str(f.get("name", ""), "Field name", 256)
        v_str(f.get("value", ""), "Field value", 1024)


def v_blocks(blocks):
    if not isinstance(blocks, list) or len(blocks) > 40:
        bad("Too many parts")
    for b in blocks:
        v_block(b)


def check_layout(layout):
    if not isinstance(layout, dict):
        bad("layout must be an object")
    if len(json.dumps(layout)) > 200000:
        bad("Message is too large")

    v_digits(str(layout.get("guild_id") or ""), "server id")

    kind = layout.get("kind", "layout")
    if kind not in ("layout", "classic"):
        bad("Unknown message kind")

    if kind == "classic":
        v_str(layout.get("content", ""), "Message text", 2000)
        embeds = layout.get("embeds", [])
        if not isinstance(embeds, list) or len(embeds) > 10:
            bad("A message can have at most 10 embeds")
        for e in embeds:
            v_embed(e)

    v_blocks(layout.get("blocks"))

    replies = layout.get("replies", [])
    if not isinstance(replies, list) or len(replies) > 25:
        bad("replies must be a list of at most 25")

    for r in replies:
        if not isinstance(r, dict):
            bad("Invalid reply")
        v_id(r.get("id"), "reply")
        v_str(r.get("name", ""), "Reply name", 100)
        v_blocks(r.get("blocks", []))

    forms = layout.get("forms", [])
    if not isinstance(forms, list) or len(forms) > 25:
        bad("forms must be a list of at most 25")

    for f in forms:
        if not isinstance(f, dict):
            bad("Invalid form")
        v_id(f.get("id"), "form")
        v_str(f.get("name", ""), "Form name", 100)
        v_str(f.get("title", ""), "Form title", 100)
        v_str(f.get("thanks", ""), "Thank-you text", 2000)
        v_digits(f.get("channel_id", ""), "channel")

        fields = f.get("fields", [])
        if not isinstance(fields, list) or len(fields) > 10:
            bad("Too many form questions")

        for q in fields:
            if not isinstance(q, dict):
                bad("Invalid question")
            v_id(q.get("id"), "question")
            v_str(q.get("label", ""), "Question label", 100)
            v_str(q.get("placeholder", ""), "Hint text", 200)
            if q.get("style", "short") not in ("short", "paragraph"):
                bad("Invalid question size")


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
        return builder.build_payload(layout, message_id)
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

            p = role.permissions
            dangerous = bool(
                p.administrator
                or p.manage_guild
                or p.manage_roles
                or p.ban_members
                or p.kick_members
            )

            result.append({
                "id": str(role.id),
                "name": role.name,
                "assignable": can,
                "dangerous": dangerous
            })

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
                result.append({
                    "guild_id": str(g.id),
                    "guild_name": g.name,
                    "emojis": items
                })

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
            sent = await channel.send(**view, allowed_mentions=SAFE_MENTIONS)
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
            if found["webhook_id"]:
                await webhooks.edit(
                    get_client(), channel, found["webhook_id"], found["posted_message_id"], view, SAFE_MENTIONS
                )
            else:
                target = await channel.fetch_message(int(found["posted_message_id"]))
                await target.edit(**view, allowed_mentions=SAFE_MENTIONS)
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
            if found["webhook_id"]:
                await webhooks.delete(get_client(), channel, found["webhook_id"], found["posted_message_id"])
            else:
                target = await channel.fetch_message(int(found["posted_message_id"]))
                await target.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as err:
            raise discord_error(err)
        db.clear_posted(message_id)
        return db.get_message(message_id)

    # ----- Webhook presets and sending -----

    @router.get("/guilds/{guild_id}/personas")
    async def list_personas(guild_id: str, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        return db.list_personas(str(guild.id))

    @router.post("/guilds/{guild_id}/personas")
    async def add_persona(guild_id: str, body: PersonaIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        guild = await user_guild(uid, guild_id)
        gid = str(guild.id)
        name = body.name.strip()
        if not 1 <= len(name) <= 80:
            bad("The name must be 1 to 80 characters")
        avatar = body.avatar_url.strip()
        v_url(avatar, "Avatar link")
        if db.count_personas(gid) >= MAX_PERSONAS:
            raise HTTPException(400, "This server has reached the limit of webhook presets")
        return db.create_persona(gid, name, avatar)

    @router.delete("/personas/{persona_id}")
    async def remove_persona(persona_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = db.get_persona(persona_id)
        if not found:
            raise HTTPException(404, "Preset not found")
        await user_guild(uid, found["guild_id"])
        db.delete_persona(persona_id)
        return {"deleted": True}

    @router.post("/messages/{message_id}/post-webhook")
    async def post_webhook(message_id: int, body: HookSendIn, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)
        if not found["guild_id"]:
            raise HTTPException(400, "Choose a server for this message first")
        if str(body.guild_id) != found["guild_id"]:
            raise HTTPException(400, "This message belongs to another server")
        guild = await user_guild(uid, found["guild_id"])
        persona = db.get_persona(body.persona_id)
        if not persona or persona["guild_id"] != found["guild_id"]:
            raise HTTPException(404, "Preset not found")
        check_form_channels(found["layout"], guild)
        channel = find_channel(get_client(), found["guild_id"], body.channel_id)
        payload = make_view(found["layout"], message_id)
        try:
            hook, sent = await webhooks.send(get_client(), channel, payload, persona, SAFE_MENTIONS)
        except discord.HTTPException as err:
            raise discord_error(err)
        db.set_posted_via_webhook(message_id, found["guild_id"], str(channel.id), str(sent.id), str(hook.id))
        db.add_version(message_id, found["name"], found["layout"])
        return db.get_message(message_id)

    @router.post("/messages/{message_id}/test")
    async def test_send(message_id: int, authorization: str = Header(default=None)):
        uid = require_user(authorization)
        found = await load_message(message_id, uid)

        if not found["guild_id"]:
            raise HTTPException(400, "Choose a server for this message first")

        guild = await user_guild(uid, found["guild_id"])
        check_form_channels(found["layout"], guild)

        try:
            payload = builder.build_payload(found["layout"], message_id)
        except ValueError as err:
            raise HTTPException(400, str(err))

        try:
            user = await get_client().fetch_user(int(uid))
            await user.send(**payload)
        except discord.Forbidden:
            raise HTTPException(
                400,
                "I couldn't DM you. In Discord, allow direct messages from server members, then try again."
            )
        except discord.HTTPException as err:
            raise discord_error(err)

        return {"ok": True}

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
            run_date, next_run = timeutil.first_run(
                body.run_date, body.run_time, body.tz, body.repeat
            )
        except ValueError as err:
            raise HTTPException(400, str(err))

        if db.count_schedules_message(message_id) >= MAX_SCHEDULES_PER_MESSAGE:
            raise HTTPException(400, "This message has too many schedules. Delete some first.")

        if db.count_schedules_guild(gid) >= MAX_SCHEDULES_PER_GUILD:
            raise HTTPException(400, "This server has reached the schedule limit")

        return db.create_schedule(
            message_id,
            gid,
            body.action,
            channel_id,
            run_date,
            body.run_time,
            body.tz,
            body.repeat,
            body.replace_previous and body.action == "post",
            next_run
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
            "layout": found["layout"]
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
            "messages": [
                {"name": m["name"], "layout": m["layout"]}
                for m in db.list_messages_full(gid)
            ],
            "templates": [
                {"name": t["name"], "layout": t["layout"]}
                for t in db.list_templates_full(gid)
            ]
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
                ready.append((
                    clean_name(entry.get("name") or "Imported message"),
                    layout
                ))
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

        return {
            "messages": len(ready_messages),
            "templates": len(ready_templates),
            "stripped": foreign
        }

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
            allowed.append({
                "id": gid,
                "name": joined.get(gid) or row["name"],
                "joined": gid in joined
            })

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
