import os
import re
import time
import asyncio
from collections import defaultdict, deque
from urllib.parse import urlencode

import aiohttp
import discord
import jwt
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

import access
import automation
import builder
import variables
import db
import interactions
import scheduler
from routes import make_router


def get_setting(name):
    value = os.getenv(name)
    if value:
        return value
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None


CLIENT_ID = get_setting("DISCORD_CLIENT_ID")
CLIENT_SECRET = get_setting("DISCORD_CLIENT_SECRET")
OWNER_ID = get_setting("OWNER_ID")
JWT_SECRET = get_setting("JWT_SECRET")
API_URL = (get_setting("API_URL") or "").rstrip("/")
DASHBOARD_ORIGIN = (get_setting("DASHBOARD_ORIGIN") or "https://example.me").rstrip("/")
if DASHBOARD_ORIGIN.startswith("http://"):
    DASHBOARD_ORIGIN = "https://" + DASHBOARD_ORIGIN[7:]
REDIRECT_URI = f"{API_URL}/auth/callback"

missing = [
    name
    for name, value in [
        ("DISCORD_CLIENT_ID", CLIENT_ID),
        ("DISCORD_CLIENT_SECRET", CLIENT_SECRET),
        ("OWNER_ID", OWNER_ID),
        ("JWT_SECRET", JWT_SECRET),
        ("API_URL", API_URL),
    ]
    if not value
]
if missing:
    raise SystemExit("Missing settings: " + ", ".join(missing))


def make_token(subject, minutes, extra=None):
    now = int(time.time())
    payload = {"sub": subject, "iat": now, "exp": now + minutes * 60}
    payload.update(extra or {})
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def read_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def require_user(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not logged in")
    data = read_token(authorization[7:])
    sub = str((data or {}).get("sub", ""))
    if not sub.isdigit():
        raise HTTPException(401, "Invalid login")
    if int(data.get("iat") or 0) < db.get_valid_after(sub):
        raise HTTPException(401, "Logged out")
    if too_many("u:" + sub, 300):
        raise HTTPException(429, "Too many requests. Slow down.")
    return sub


app = FastAPI()

MAX_BODY_BYTES = 4000000
RATE_WINDOW = 60
IP_LIMIT = 240
AUTH_LIMIT = 20
_hits = defaultdict(deque)


def too_many(key, limit):
    now = time.time()
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v or v[-1] < now - RATE_WINDOW]:
            del _hits[k]
    q = _hits[key]
    while q and q[0] < now - RATE_WINDOW:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


@app.middleware("http")
async def guard(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")
    auth = request.url.path.startswith("/auth/")
    if too_many(("a:" if auth else "i:") + ip, AUTH_LIMIT if auth else IP_LIMIT):
        return JSONResponse({"detail": "Too many requests. Slow down."}, status_code=429, headers={"Retry-After": "60"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


_STARTED_AT = time.time()


@app.get("/health")
async def health():
    ready = bool(client.is_ready()) if client else False
    lat = round(client.latency * 1000) if (ready and client.latency is not None) else None
    return {
        "ok": True,
        "ready": ready,
        "bot": str(client.user) if client.user else None,
        "uptime_seconds": int(time.time() - _STARTED_AT),
        "discord_latency_ms": lat,
        "servers": len(client.guilds) if ready else 0,
    }


@app.get("/auth/login")
async def login(n: str = ""):
    if not re.fullmatch(r"[A-Za-z0-9]{16,64}", n):
        raise HTTPException(400, "Bad login request")
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify",
        "state": make_token("login-state", 5, {"n": n}),
    }
    return RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.get("/invite")
async def invite_bot():
    params = {
        "client_id": CLIENT_ID,
        "permissions": "275415022656",
        "scope": "bot applications.commands",
    }
    return RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.get("/auth/callback")
async def callback(code: str = "", state: str = ""):
    data = read_token(state)
    if not code or not data or data.get("sub") != "login-state":
        print("Login callback refused: missing code or expired state")
        return RedirectResponse(f"{DASHBOARD_ORIGIN}/dashboard/")
    async with aiohttp.ClientSession() as session:
        form = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
        async with session.post("https://discord.com/api/oauth2/token", data=form) as resp:
            if resp.status != 200:
                print("Login callback refused: Discord rejected the code (probably already used)")
                return RedirectResponse(f"{DASHBOARD_ORIGIN}/dashboard/")
            tokens = await resp.json()
        headers = {"Authorization": "Bearer " + tokens["access_token"]}
        async with session.get("https://discord.com/api/users/@me", headers=headers) as resp:
            if resp.status != 200:
                raise HTTPException(400, "Could not read your Discord account")
            user = await resp.json()
    user_id = str(user["id"])
    if user_id != OWNER_ID:
        mine = await access.accessible_guilds(client, user_id, OWNER_ID)
        if not mine:
            return RedirectResponse(f"{DASHBOARD_ORIGIN}/dashboard/#error=noaccess")
    pass_token = make_token(user_id, 60 * 12)
    nonce = str(data.get("n", ""))
    return RedirectResponse(f"{DASHBOARD_ORIGIN}/dashboard/#token={pass_token}&n={nonce}")


@app.get("/me")
async def me(authorization: str = Header(default=None)):
    uid = require_user(authorization)
    if uid != OWNER_ID:
        mine = await access.accessible_guilds(client, uid, OWNER_ID)
        if not mine:
            raise HTTPException(403, "You need to be an Administrator of a server where this bot is installed.")
    u = client.get_user(int(uid))
    if not u:
        try:
            u = await client.fetch_user(int(uid))
        except Exception:
            u = None
    uname = (getattr(u, "global_name", None) or getattr(u, "display_name", None) or getattr(u, "name", None) or "") if u else ""
    av = str(u.display_avatar.url) if u and getattr(u, "display_avatar", None) else ""
    return {"user_id": uid, "is_owner": uid == OWNER_ID, "username": uname, "avatar_url": av}


@app.post("/logout")
async def logout(authorization: str = Header(default=None)):
    uid = require_user(authorization)
    db.revoke_tokens(uid)
    return {"logged_out": True}


app.include_router(make_router(require_user, lambda: client, OWNER_ID))


class EmbedBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self):
        @self.tree.command(name="embed_list", description="List saved dashboard messages for this server")
        async def cmd_embed_list(inter: discord.Interaction):
            if not inter.guild or not inter.user.guild_permissions.administrator:
                await inter.response.send_message("Only server Administrators can use this.", ephemeral=True)
                return
            msgs = db.list_messages([str(inter.guild.id)])
            if not msgs:
                await inter.response.send_message("No saved messages for this server yet.", ephemeral=True)
                return
            lines = [f"- **{m['id']}**: {m['name']}" for m in msgs[:25]]
            await inter.response.send_message("\n".join(lines), ephemeral=True)

        @self.tree.command(name="embed_send", description="Post a saved dashboard message to this channel")
        @discord.app_commands.describe(message_id="The numeric message ID from /embed_list")
        async def cmd_embed_send(inter: discord.Interaction, message_id: int):
            if not inter.guild or not inter.user.guild_permissions.administrator:
                await inter.response.send_message("Only server Administrators can use this.", ephemeral=True)
                return
            m = db.get_message(message_id)
            if not m or str(m.get("guild_id")) != str(inter.guild.id):
                await inter.response.send_message("Message not found for this server.", ephemeral=True)
                return
            resolved = variables.resolve_layout(m["layout"], guild=inter.guild, channel=inter.channel, user=inter.user)
            payload = builder.build_payload(resolved, m["id"])
            sent = await inter.channel.send(**payload, allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True, replied_user=False))
            db.set_posted(m["id"], str(inter.guild.id), str(inter.channel.id), str(sent.id))
            await inter.response.send_message(f"Posted **{m['name']}**!", ephemeral=True)

        @self.tree.command(name="faq", description="Send a server custom command message")
        @discord.app_commands.describe(command="Name of the custom command")
        async def cmd_faq(inter: discord.Interaction, command: str):
            if not inter.guild:
                await inter.response.send_message("Use this inside a server.", ephemeral=True)
                return
            cmds = db.list_custom_commands(str(inter.guild.id))
            hit = next((c for c in cmds if c["name"] == command.strip().lower()), None)
            if not hit:
                await inter.response.send_message("Custom command not found.", ephemeral=True)
                return
            m = db.get_message_cached(int(hit["message_id"]))
            if not m or str(m.get("guild_id")) != str(inter.guild.id):
                await inter.response.send_message("Saved message no longer exists.", ephemeral=True)
                return
            resolved = variables.resolve_layout(m["layout"], guild=inter.guild, channel=inter.channel, user=inter.user)
            payload = builder.build_payload(resolved, m["id"])
            await inter.response.send_message(**payload, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False))

        @cmd_faq.autocomplete("command")
        async def cmd_faq_ac(inter: discord.Interaction, current: str):
            if not inter.guild:
                return []
            q = current.strip().lower()
            cmds = db.list_custom_commands(str(inter.guild.id))
            return [
                discord.app_commands.Choice(name=f"{c['name']} — {c['description'][:40]}", value=c["name"])
                for c in cmds if not q or q in c["name"]
            ][:25]

        @self.tree.context_menu(name="Edit in dashboard")
        async def ctx_edit_dashboard(inter: discord.Interaction, message: discord.Message):
            if not inter.guild or not (inter.user.guild_permissions.administrator or db.is_editor(inter.guild.id, inter.user.id)):
                await inter.response.send_message("Only Admins/Editors can use this.", ephemeral=True)
                return
            await inter.response.send_message(f"Open the dashboard to edit: {DASHBOARD_ORIGIN}/dashboard/", ephemeral=True)

        await self.tree.sync()
        port = int(get_setting("SERVER_PORT") or get_setting("PORT") or 8080)
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        self.api_server = uvicorn.Server(config)
        asyncio.create_task(self.api_server.serve())
        print(f"API listening on port {port}")
        self.sched_task = asyncio.create_task(scheduler.run_loop(self))

    async def leave_unapproved(self, guild):
        db.add_pending(str(guild.id), guild.name)
        print(f"Leaving unapproved server {guild.name} (first run).")
        try:
            await guild.leave()
        except discord.HTTPException as err:
            print("Could not leave:", err)

    async def on_ready(self):
        print(f"Online as {self.user}")
        if db.allowed_count() == 0:
            for g in self.guilds:
                db.allow_guild(str(g.id), g.name)
            print("Approved all servers the bot is currently in (first run).")
        allowed = set(db.list_allowed_ids())
        for g in list(self.guilds):
            if str(g.id) not in allowed:
                await self.leave_unapproved(g)

    async def on_guild_join(self, guild):
        if str(guild.id) not in set(db.list_allowed_ids()):
            await self.leave_unapproved(guild)

    async def on_interaction(self, interaction):
        await interactions.handle(interaction)

    async def on_member_join(self, member):
        await automation.handle_member_event(member, "welcome")

    async def on_member_remove(self, member):
        await automation.handle_member_event(member, "goodbye")

    async def on_message(self, message):
        await automation.handle_message(self, message)


db.init_db()
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = EmbedBot(intents=intents)

token = get_setting("BOT_TOKEN")
if not token:
    raise SystemExit("No BOT_TOKEN found.")
client.run(token)
