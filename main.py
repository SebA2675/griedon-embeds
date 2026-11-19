import os
import asyncio
import discord
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_setting("DASHBOARD_ORIGIN") or "https://example.me"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True, "bot": str(client.user) if client.user else None}


class EmbedBot(discord.Client):
    async def setup_hook(self):
        port = int(get_setting("SERVER_PORT") or get_setting("PORT") or 8080)
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        self.api_server = uvicorn.Server(config)
        asyncio.create_task(self.api_server.serve())
        print(f"API listening on port {port}")

    async def on_ready(self):
        print(f"Online as {self.user}")


client = EmbedBot(intents=discord.Intents.default())

token = get_setting("BOT_TOKEN")
if not token:
    raise SystemExit("No BOT_TOKEN found.")
client.run(token)
