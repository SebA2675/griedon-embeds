import os
import discord

def get_token():
    token = os.getenv("BOT_TOKEN")
    if token:
        return token
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return None

client = discord.Client(intents=discord.Intents.default())

@client.event
async def on_ready():
    print(f"Online as {client.user}")

token = get_token()
if not token:
    raise SystemExit("No BOT_TOKEN found.")
client.run(token)
