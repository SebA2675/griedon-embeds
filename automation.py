"""Phase 7 automation: Welcome/Goodbye, Keyword auto-replies, RSS/YouTube/Reddit feeds, Sticky messages."""

import asyncio
import ipaddress
import socket
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from urllib.parse import urlparse

import aiohttp
import discord

import builder
import db
import variables

SAFE_AUTOMATION_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)

# In-memory caches
_kw_cache: dict[str, list[dict]] | None = None
_kw_cd: dict[tuple[int, str], float] = {}
_welcome_hits: dict[str, deque] = defaultdict(deque)
_sticky_cache: dict[str, dict] | None = None
_sticky_tasks: dict[str, asyncio.Task] = {}


def reload_keywords():
    global _kw_cache
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in db.list_keywords(None):
        grouped[str(r["guild_id"])].append(r)
    _kw_cache = dict(grouped)


def reload_stickies():
    global _sticky_cache
    _sticky_cache = {str(s["channel_id"]): s for s in db.list_stickies(None)}


def _get_keywords(guild_id: str) -> list[dict]:
    if _kw_cache is None:
        reload_keywords()
    return (_kw_cache or {}).get(str(guild_id), [])


def _get_sticky(channel_id: str) -> dict | None:
    if _sticky_cache is None:
        reload_stickies()
    return (_sticky_cache or {}).get(str(channel_id))


async def handle_member_event(member: discord.Member, kind: str):
    if member.bot or member.guild is None:
        return
    gid = str(member.guild.id)
    now = time.time()
    q = _welcome_hits[gid]
    while q and q[0] < now - 60:
        q.popleft()
    if len(q) >= 15:
        return

    cfg = db.get_automation(gid)
    enabled = cfg["welcome_enabled"] if kind == "welcome" else cfg["goodbye_enabled"]
    cid = cfg["welcome_channel_id"] if kind == "welcome" else cfg["goodbye_channel_id"]
    mid = cfg["welcome_message_id"] if kind == "welcome" else cfg["goodbye_message_id"]
    if not enabled or not str(cid).isdigit() or not mid:
        return

    ch = member.guild.get_channel(int(cid))
    msg = db.get_message_cached(int(mid))
    if not isinstance(ch, discord.TextChannel) or not msg or str(msg.get("guild_id")) != gid:
        return

    q.append(now)
    try:
        resolved = variables.resolve_layout(msg["layout"], guild=member.guild, channel=ch, user=member)
        payload = builder.build_payload(resolved, msg["id"])
        await ch.send(**payload, allowed_mentions=SAFE_AUTOMATION_MENTIONS)
    except Exception as err:
        print(f"Automation {kind} error:", err)


async def handle_message(client: discord.Client, message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    gid = str(message.guild.id)
    cid = str(message.channel.id)
    text = (message.content or "").strip().lower()

    # 1. Keyword auto-replies (in-memory, no DB read)
    if text:
        now = time.time()
        for rule in _get_keywords(gid):
            if rule["channel_id"] and str(rule["channel_id"]) != cid:
                continue
            phrase = str(rule["phrase"]).lower()
            mtype = rule["match_type"]
            matched = (
                (mtype == "exact" and text == phrase)
                or (mtype == "starts" and text.startswith(phrase))
                or (mtype == "contains" and phrase in text)
            )
            if not matched:
                continue
            cd_key = (rule["id"], cid)
            if now - _kw_cd.get(cd_key, 0) < max(1, int(rule["cooldown"] or 10)):
                continue
            _kw_cd[cd_key] = now
            saved = db.get_message_cached(int(rule["message_id"]))
            if saved and str(saved.get("guild_id")) == gid:
                try:
                    resolved = variables.resolve_layout(saved["layout"], guild=message.guild, channel=message.channel, user=message.author)
                    payload = builder.build_payload(resolved, saved["id"])
                    await message.channel.send(**payload, allowed_mentions=SAFE_AUTOMATION_MENTIONS)
                except Exception as err:
                    print("Keyword reply error:", err)
            break

    # 2. Sticky message debounce
    st = _get_sticky(cid)
    if st:
        old_task = _sticky_tasks.get(cid)
        if old_task and not old_task.done():
            old_task.cancel()
        _sticky_tasks[cid] = asyncio.create_task(_repost_sticky(client, message.guild, message.channel, st))


async def _repost_sticky(client, guild, channel, st):
    try:
        await asyncio.sleep(max(3, min(int(st.get("delay_seconds") or 5), 60)))
        saved = db.get_message_cached(int(st["message_id"]))
        if not saved or str(saved.get("guild_id")) != str(guild.id):
            return
        if st.get("last_posted_id"):
            try:
                old_msg = await channel.fetch_message(int(st["last_posted_id"]))
                await old_msg.delete()
            except (discord.HTTPException, ValueError):
                pass
        resolved = variables.resolve_layout(saved["layout"], guild=guild, channel=channel)
        payload = builder.build_payload(resolved, saved["id"])
        sent = await channel.send(**payload, allowed_mentions=SAFE_AUTOMATION_MENTIONS)
        db.update_sticky_posted(str(channel.id), str(sent.id))
        if _sticky_cache is not None and str(channel.id) in _sticky_cache:
            _sticky_cache[str(channel.id)]["last_posted_id"] = str(sent.id)
    except asyncio.CancelledError:
        pass
    except Exception as err:
        print("Sticky repost error:", err)


def is_safe_public_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname:
            return False
        infos = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def _parse_feed_latest(xml_text: str):
    root = ET.fromstring(xml_text)
    # RSS 2.0
    item = root.find("./channel/item")
    if item is not None:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        author = (item.findtext("author") or item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        return guid, title, link, author
    # Atom (YouTube / Reddit)
    ns = "{http://www.w3.org/2005/Atom}"
    entry = root.find(f"{ns}entry") or root.find("entry")
    if entry is not None:
        title = (entry.findtext(f"{ns}title") or entry.findtext("title") or "").strip()
        link_el = entry.find(f"{ns}link") or entry.find("link")
        link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
        author = (entry.findtext(f"{ns}author/{ns}name") or entry.findtext("author/name") or "").strip()
        eid = (entry.findtext(f"{ns}id") or entry.findtext("id") or link or title).strip()
        return eid, title, link, author
    return None


async def check_feeds(client: discord.Client):
    now = int(time.time())
    feeds = db.list_feeds(None)
    if not feeds:
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        for f in feeds:
            if now - int(f.get("last_check") or 0) < 900:
                continue
            url = f["url"]
            if not await asyncio.to_thread(is_safe_public_url, url):
                db.update_feed_state(f["id"], f["last_seen"], now, "Blocked unsafe URL")
                continue
            try:
                async with session.get(url, headers={"User-Agent": "GriedonEmbeds/1.0"}, max_redirects=3) as resp:
                    if resp.status != 200:
                        db.update_feed_state(f["id"], f["last_seen"], now, f"HTTP {resp.status}")
                        continue
                    raw = await resp.content.read(1048576)
                parsed = _parse_feed_latest(raw.decode("utf-8", errors="replace"))
                if not parsed:
                    db.update_feed_state(f["id"], f["last_seen"], now, "No items found")
                    continue
                item_id, title, link, author = parsed
                if item_id and item_id != f["last_seen"]:
                    if f["last_seen"]:
                        guild = client.get_guild(int(f["guild_id"]))
                        ch = client.get_channel(int(f["channel_id"]))
                        saved = db.get_message_cached(int(f["message_id"]))
                        if guild and isinstance(ch, discord.TextChannel) and saved:
                            resolved = variables.resolve_layout(
                                saved["layout"],
                                guild=guild,
                                channel=ch,
                                overrides={"title": title, "link": link, "author": author},
                            )
                            payload = builder.build_payload(resolved, saved["id"])
                            await ch.send(**payload, allowed_mentions=SAFE_AUTOMATION_MENTIONS)
                    db.update_feed_state(f["id"], item_id, now, "")
                else:
                    db.update_feed_state(f["id"], f["last_seen"], now, "")
            except Exception as err:
                db.update_feed_state(f["id"], f["last_seen"], now, str(err)[:150])