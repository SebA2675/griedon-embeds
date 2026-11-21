import time

import discord
from fastapi import HTTPException

import db

CACHE_SECONDS = 60
_cache = {}


async def is_admin(user_id, guild):
    key = (guild.id, str(user_id))
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    result = False
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        result = bool(member.guild_permissions.administrator)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        result = False
    _cache[key] = (result, now + CACHE_SECONDS)
    return result


async def accessible_guilds(client, user_id, owner_id):
    allowed = set(db.list_allowed_ids())
    found = []
    for g in client.guilds:
        if user_id == owner_id:
            found.append(g)
        elif str(g.id) in allowed and await is_admin(user_id, g):
            found.append(g)
    return found


async def require_guild(client, user_id, owner_id, guild_id):
    try:
        guild = client.get_guild(int(guild_id))
    except (TypeError, ValueError):
        raise HTTPException(400, "Bad server id")
    if guild is None:
        raise HTTPException(404, "Server not found")
    if user_id != owner_id:
        approved = str(guild.id) in set(db.list_allowed_ids())
        if not approved or not await is_admin(user_id, guild):
            raise HTTPException(403, "You need to be an Administrator of that server")
    return guild
