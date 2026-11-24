
import asyncio
import time

import discord
from fastapi import HTTPException

import backup
import db
import timeutil
import webhooks
from routes import SAFE_MENTIONS, check_form_channels, find_channel, make_view

CHECK_SECONDS = 30
MAX_LATE_SECONDS = 3600


async def delete_previous(client, message):
    try:
        channel = client.get_channel(int(message["channel_id"]))
        if channel is None:
            return

        target = await channel.fetch_message(
            int(message["posted_message_id"])
        )
        await target.delete()

    except (discord.HTTPException, ValueError, TypeError):
        pass


async def run_schedule(client, s):
    message = db.get_message(s["message_id"])

    if not message:
        return "Failed: the message was deleted"

    if str(message["guild_id"] or "") != s["guild_id"]:
        return "Failed: the message was moved to another server"

    guild = client.get_guild(int(s["guild_id"]))

    if guild is None:
        return "Failed: the bot is not in that server any more"

    try:
        check_form_channels(message["layout"], guild)
        view = make_view(message["layout"], message["id"])

        if s["action"] == "post":
            channel = find_channel(
                client, s["guild_id"], s["channel_id"]
            )

            if s["replace_previous"] and message["posted_message_id"]:
                await delete_previous(client, message)

            sent = await channel.send(
                **view,
                allowed_mentions=SAFE_MENTIONS
            )

            db.set_posted(
                message["id"],
                s["guild_id"],
                str(channel.id),
                str(sent.id)
            )

        else:
            if not message["posted_message_id"]:
                return "Failed: this message hasn't been posted yet"

            channel = find_channel(
                client, s["guild_id"], message["channel_id"]
            )

            if message["webhook_id"]:
                await webhooks.edit(
                    client, channel, message["webhook_id"], message["posted_message_id"], view, SAFE_MENTIONS
                )
            else:
                target = await channel.fetch_message(
                    int(message["posted_message_id"])
                )

                await target.edit(
                    **view,
                    allowed_mentions=SAFE_MENTIONS
                )

        db.add_version(
            message["id"],
            message["name"],
            message["layout"]
        )

        return "OK"

    except HTTPException as err:
        return "Failed: " + str(err.detail)[:200]

    except discord.NotFound:
        return "Failed: the posted message no longer exists"

    except discord.HTTPException as err:
        return "Failed: Discord refused it (" + str(err)[:150] + ")"


def finish(s, status):
    now = int(time.time())

    if s["repeat"] == "once":
        db.finish_schedule(
            s["id"], now, status, None, s["run_date"], False
        )
        return

    try:
        new_date, next_run = timeutil.advance(
            s["run_date"],
            s["run_time"],
            s["tz"],
            s["repeat"],
            now
        )

        db.finish_schedule(
            s["id"], now, status, next_run, new_date, True
        )

    except ValueError:
        db.finish_schedule(
            s["id"],
            now,
            status + " (schedule stopped)",
            None,
            s["run_date"],
            False
        )


async def tick(client):
    now = int(time.time())

    for s in db.due_schedules(now):
        if now - s["next_run"] > MAX_LATE_SECONDS:
            status = "Skipped: the bot was offline at the scheduled time"
        else:
            status = await run_schedule(client, s)

        finish(s, status)

        print(f"Schedule {s['id']}: {status}")


_last_cleanup = 0.0


async def maybe_cleanup():
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < 86400:
        return
    _last_cleanup = now
    removed = await asyncio.to_thread(db.cleanup)
    if removed:
        print(f"Cleanup: removed {removed} leftover rows")


async def run_loop(client):
    await client.wait_until_ready()
    print("Scheduler started.")

    while not client.is_closed():
        try:
            await tick(client)

        except Exception as err:
            print("Scheduler error:", err)

        try:
            await maybe_cleanup()
        except Exception as err:
            print("Cleanup error:", type(err).__name__)

        try:
            await backup.maybe_backup()

        except Exception as err:
            print("Backup error:", type(err).__name__)

        await asyncio.sleep(CHECK_SECONDS)
