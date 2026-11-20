import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import db


class MessageIn(BaseModel):
    name: str
    layout: dict


def check_layout(layout):
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        raise HTTPException(400, "layout must contain a list called blocks")
    if len(json.dumps(layout)) > 200000:
        raise HTTPException(400, "Message is too large")


def clean_name(name):
    return name.strip()[:100] or "Untitled message"


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
        try:
            guild = get_client().get_guild(int(guild_id))
        except ValueError:
            raise HTTPException(400, "Bad server id")
        if not guild:
            raise HTTPException(404, "Server not found")
        result = []
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.send_messages:
                category = ch.category.name if ch.category else None
                result.append({"id": str(ch.id), "name": ch.name, "category": category})
        return result

    return router
