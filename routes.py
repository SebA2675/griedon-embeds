import json
import re
import time
from typing import Annotated, Literal, Optional, Union

import discord
from fastapi import APIRouter, Header, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

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


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageIn(StrictModel):
    name: str
    layout: dict


class SendIn(StrictModel):
    guild_id: str
    channel_id: str


class AllowIn(StrictModel):
    guild_id: str
    name: str = ""


class ImportIn(StrictModel):
    data: dict


class ScheduleIn(StrictModel):
    action: str
    channel_id: str = ""
    run_date: str
    run_time: str
    tz: str
    repeat: str = "once"
    replace_previous: bool = False


class PersonaIn(StrictModel):
    name: str
    avatar_url: str = ""


class HookSendIn(StrictModel):
    guild_id: str
    channel_id: str
    persona_id: int


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
EMOJI_NAME_RE = re.compile(r"^\w{1,32}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def check_id(v: str, what: str = "id") -> str:
    if not isinstance(v, str) or not ID_RE.fullmatch(v):
        raise ValueError(f"Invalid {what} id")
    return v


def check_https(v: str, what: str = "Link") -> str:
    if not isinstance(v, str):
        raise ValueError(f"{what} must be text")
    if len(v) > 2000:
        raise ValueError(f"{what} is too long")
    if v and not v.startswith("https://"):
        raise ValueError(f"{what} must start with https://")
    return v


def check_digits(v: Optional[str], what: str = "id") -> str:
    if v is None or v == "":
        return ""
    if not isinstance(v, str) or not v.isdigit() or len(v) > 25:
        raise ValueError(f"Invalid {what}")
    return v


class EmojiModel(StrictModel):
    id: str
    name: str
    animated: StrictBool = False

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        if not isinstance(v, str) or not v.isdigit() or len(v) > 25:
            raise ValueError("Invalid emoji")
        return v

    @field_validator("name")
    @classmethod
    def valid_name(cls, v):
        if not isinstance(v, str) or not EMOJI_NAME_RE.fullmatch(v):
            raise ValueError("Invalid emoji")
        return v


class LinkAction(StrictModel):
    type: Literal["link"]
    url: str = ""

    @field_validator("url")
    @classmethod
    def valid_url(cls, v):
        return check_https(v, "Link")


class ReplyAction(StrictModel):
    type: Literal["reply"]
    reply_id: str = ""

    @field_validator("reply_id")
    @classmethod
    def valid_ref(cls, v):
        return "" if v == "" else check_id(v, "reply")


class FormAction(StrictModel):
    type: Literal["form"]
    form_id: str = ""

    @field_validator("form_id")
    @classmethod
    def valid_ref(cls, v):
        return "" if v == "" else check_id(v, "form")


class RoleAction(StrictModel):
    type: Literal["role"]
    role_id: str = ""
    mode: Literal["toggle", "add", "remove"] = "toggle"

    @field_validator("role_id")
    @classmethod
    def valid_role(cls, v):
        return check_digits(v, "role")


ActionModel = Annotated[Union[LinkAction, ReplyAction, FormAction, RoleAction], Field(discriminator="type")]
OptionActionModel = Annotated[Union[ReplyAction, FormAction, RoleAction], Field(discriminator="type")]


class RuleMixin(StrictModel):
    allowed_roles: list[str] = Field(default_factory=list, max_length=10)
    cooldown: StrictInt = Field(default=0, ge=0, le=86400)
    once: StrictBool = False

    @field_validator("allowed_roles")
    @classmethod
    def valid_roles(cls, v):
        for r in v:
            if not isinstance(r, str) or not r.isdigit() or len(r) > 25:
                raise ValueError("Invalid role")
        return v


class ButtonModel(RuleMixin):
    id: str
    label: str = Field(default="", max_length=80)
    style: Literal["grey", "blue", "green", "red"] = "grey"
    emoji: Optional[EmojiModel] = None
    action: ActionModel

    @model_validator(mode="before")
    @classmethod
    def legacy_link(cls, data):
        if isinstance(data, dict) and "action" not in data and "url" in data:
            copy = dict(data)
            copy["action"] = {"type": "link", "url": copy.pop("url") or ""}
            return copy
        return data

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "button")


class DropdownOptionModel(RuleMixin):
    id: str
    label: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=100)
    emoji: Optional[EmojiModel] = None
    action: OptionActionModel

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "option")


class TextBlock(StrictModel):
    id: str
    type: Literal["text"]
    content: str = Field(default="", max_length=4000)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")


class SectionBlock(StrictModel):
    id: str
    type: Literal["section"]
    content: str = Field(default="", max_length=4000)
    side: Literal["button", "thumbnail"] = "button"
    button: Optional[ButtonModel] = None
    image: str = ""

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")

    @field_validator("image")
    @classmethod
    def valid_image(cls, v):
        return check_https(v, "Thumbnail link")

    @model_validator(mode="after")
    def check_side(self):
        if self.side == "button" and self.button is None:
            raise ValueError("Section button is missing")
        return self


class ImagesBlock(StrictModel):
    id: str
    type: Literal["images"]
    urls: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, v):
        return [check_https(u, "Image link") for u in v]


class DividerBlock(StrictModel):
    id: str
    type: Literal["divider"]
    spacing: Literal["small", "large"] = "small"
    line: StrictBool = True

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")


class ButtonRowBlock(StrictModel):
    id: str
    type: Literal["button_row"]
    buttons: list[ButtonModel] = Field(default_factory=list, max_length=5)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")


class DropdownBlock(StrictModel):
    id: str
    type: Literal["dropdown"]
    placeholder: str = Field(default="", max_length=150)
    options: list[DropdownOptionModel] = Field(default_factory=list, max_length=25)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")


ChildBlock = Annotated[
    Union[TextBlock, SectionBlock, ImagesBlock, DividerBlock, ButtonRowBlock, DropdownBlock],
    Field(discriminator="type"),
]


class BoxBlock(StrictModel):
    id: str
    type: Literal["box"]
    accent: str = "#5865F2"
    children: list[ChildBlock] = Field(default_factory=list, max_length=40)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "part")

    @field_validator("accent")
    @classmethod
    def valid_accent(cls, v):
        if not isinstance(v, str) or not HEX_RE.fullmatch(v):
            raise ValueError("Invalid box colour")
        return v


TopBlock = Annotated[
    Union[TextBlock, SectionBlock, ImagesBlock, DividerBlock, ButtonRowBlock, DropdownBlock, BoxBlock],
    Field(discriminator="type"),
]


class EmbedFieldModel(StrictModel):
    id: str
    name: str = Field(default="", max_length=256)
    value: str = Field(default="", max_length=1024)
    inline: StrictBool = False

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "field")


class EmbedModel(StrictModel):
    id: str
    title: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=4096)
    color: str = "#5865F2"
    url: str = ""
    author_name: str = Field(default="", max_length=256)
    author_url: str = ""
    author_icon: str = ""
    image: str = ""
    thumbnail: str = ""
    footer_text: str = Field(default="", max_length=2048)
    footer_icon: str = ""
    timestamp: StrictBool = False
    fields: list[EmbedFieldModel] = Field(default_factory=list, max_length=25)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "embed")

    @field_validator("color")
    @classmethod
    def valid_color(cls, v):
        if not isinstance(v, str) or not HEX_RE.fullmatch(v):
            raise ValueError("Invalid embed colour")
        return v

    @field_validator("url", "author_url", "author_icon", "image", "thumbnail", "footer_icon")
    @classmethod
    def valid_links(cls, v):
        return check_https(v, "Embed link")


class ReplyModel(StrictModel):
    id: str
    name: str = Field(default="", max_length=100)
    blocks: list[TopBlock] = Field(default_factory=list, max_length=40)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "reply")


class FormFieldModel(StrictModel):
    id: str
    label: str = Field(default="", max_length=45)
    style: Literal["short", "paragraph"] = "short"
    placeholder: str = Field(default="", max_length=100)
    required: StrictBool = True

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "question")


class FormModel(StrictModel):
    id: str
    name: str = Field(default="", max_length=100)
    title: str = Field(default="", max_length=45)
    channel_id: str = ""
    thanks: str = Field(default="", max_length=1900)
    fields: list[FormFieldModel] = Field(default_factory=list, max_length=5)

    @field_validator("id")
    @classmethod
    def valid_id(cls, v):
        return check_id(v, "form")

    @field_validator("channel_id")
    @classmethod
    def valid_chan(cls, v):
        return check_digits(v, "channel")


def count_v2_components(blocks) -> int:
    total = 0
    for b in blocks:
        total += 1
        if b.type == "box":
            total += count_v2_components(b.children)
        elif b.type == "button_row":
            total += len(b.buttons)
        elif b.type == "dropdown":
            total += 1
        elif b.type == "section":
            total += 2
    return total


def count_v2_chars(blocks) -> int:
    total = 0
    for b in blocks:
        if b.type in ("text", "section"):
            total += len(b.content)
        elif b.type == "box":
            total += count_v2_chars(b.children)
    return total


def check_v2_limits(blocks, label: str):
    if count_v2_components(blocks) > 40:
        raise ValueError(f"{label} cannot have more than 40 components")
    if count_v2_chars(blocks) > 4000:
        raise ValueError(f"{label} cannot have more than 4000 text characters")


class LayoutModel(StrictModel):
    kind: Literal["layout", "classic"] = "layout"
    guild_id: Optional[str] = ""
    blocks: list[TopBlock] = Field(default_factory=list, max_length=40)
    content: str = Field(default="", max_length=2000)
    embeds: list[EmbedModel] = Field(default_factory=list, max_length=10)
    replies: list[ReplyModel] = Field(default_factory=list, max_length=25)
    forms: list[FormModel] = Field(default_factory=list, max_length=25)

    @field_validator("guild_id")
    @classmethod
    def valid_guild(cls, v):
        return check_digits(v, "server id")

    @model_validator(mode="after")
    def check_limits(self):
        if self.kind == "classic":
            if len(self.blocks) > 5:
                raise ValueError("Max 5 rows of buttons and dropdowns")
            for b in self.blocks:
                if b.type not in ("button_row", "dropdown"):
                    raise ValueError("Classic messages only support buttons and dropdowns")
            total = sum(
                len(e.title)
                + len(e.description)
                + len(e.author_name)
                + len(e.footer_text)
                + sum(len(f.name) + len(f.value) for f in e.fields)
                for e in self.embeds
            )
            if total > 6000:
                raise ValueError("Embeds cannot exceed 6000 characters in total")
        else:
            check_v2_limits(self.blocks, "Main message")
        for r in self.replies:
            check_v2_limits(r.blocks, f'Reply "{r.name or r.id}"')
        return self


def bad(msg):
    raise HTTPException(400, msg)


def v_url(x, what):
    try:
        check_https(x, what)
    except ValueError as err:
        bad(str(err))


def check_layout(layout):
    if not isinstance(layout, dict):
        bad("layout must be an object")
    if len(json.dumps(layout)) > 200000:
        bad("Message is too large")
    try:
        LayoutModel.model_validate(layout)
    except ValidationError as err:
        first = err.errors()[0]
        msg = first.get("msg", "Invalid message layout")
        if msg.startswith("Value error, "):
            msg = msg[13:]
        loc = ".".join(str(x) for x in first.get("loc", []))
        bad(f"{msg} ({loc})" if loc else msg)


def clean_name(name):
    return str(name).strip()[:100] or "Untitled message"


def strip_guild_refs(x):
    if isinstance(x, list):
        for item in x:
            strip_guild_refs(item)
    elif isinstance(x, dict):
        if "role_id" in x:
            x["role_id"] = ""
        if "allowed_roles" in x:
            x["allowed_roles"] = []
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
            await user.send(**payload, allowed_mentions=SAFE_MENTIONS)
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

        return {"allowed": allowed, "pending": db.list_pending(), "storage": db.stats()}

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
        if not guild_id.isdigit():
            raise HTTPException(400, "Bad server id")
        db.remove_pending(guild_id)
        return {"ok": True}

    return router
