import os
import time
import asyncio
from urllib.parse import urlencode

import aiohttp
import discord
import jwt
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import access
import db
import interactions
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


def make_token(subject, minutes):
    payload = {"sub": subject, "exp": int(time.time()) + minutes * 60}
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
    return sub


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[DASHBOARD_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True, "bot": str(client.user) if client.user else None}


@app.get("/auth/login")
async def login():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify",
        "state": make_token("login-state", 5),
    }
    return RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params))


@app.get("/auth/callback")
async def callback(code: str = "", state: str = ""):
    data = read_token(state)
    if not code or not data or data.get("sub") != "login-state":
        raise HTTPException(400, "Bad login request")
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
                raise HTTPException(400, "Discord rejected the login")
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
    pass_token = make_token(user_id, 60 * 24 * 7)
    return RedirectResponse(f"{DASHBOARD_ORIGIN}/dashboard/#token={pass_token}")


@app.get("/me")
async def me(authorization: str = Header(default=None)):
    uid = require_user(authorization)
    if uid != OWNER_ID:
        mine = await access.accessible_guilds(client, uid, OWNER_ID)
        if not mine:
            raise HTTPException(403, "You need to be an Administrator of a server where this bot is installed.")
    return {"user_id": uid, "is_owner": uid == OWNER_ID}


app.include_router(make_router(require_user, lambda: client, OWNER_ID))


class EmbedBot(discord.Client):
    async def setup_hook(self):
        port = int(get_setting("SERVER_PORT") or get_setting("PORT") or 8080)
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        self.api_server = uvicorn.Server(config)
        asyncio.create_task(self.api_server.serve())
        print(f"API listening on port {port}")

    async def leave_unapproved(self, guild):
        db.add_pending(str(guild.id), guild.name)
        print(f"Leaving unapproved server {guild.name} ({guild.id})")
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


db.init_db()
client = EmbedBot(intents=discord.Intents.default())

token = get_setting("BOT_TOKEN")
if not token:
    raise SystemExit("No BOT_TOKEN found.")
client.run(token)
