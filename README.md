# Griedon Embeds

A private Discord embed dashboard and bot supporting **Components V2 Layouts** and **Classic Embeds**.

## Features
- Visual 3-panel builder with Undo/Redo, Live Dark/Light & Phone/Wide preview, Raw JSON editor
- Interactive buttons, dropdowns (`min_values`/`max_values`), private replies, pop-up forms (with Responses, CSV export, Accept/Deny), and Paginated Embeds
- Permanent Discord variables (`{{usermention}}`, `{{servername}}`, `{{membercount}}`, `{{date}}`, etc.) + custom variables playground
- Scheduling (once, intervals, daily, weekly, monthly, pause/resume) & Multi-message Sequences
- Automation: Welcome/Goodbye, Keyword Auto-Replies, RSS/YouTube/Reddit Feeds, Sticky Messages, `/embed_list` & `/embed_send`
- Nightly SQLite backups to GitHub, 30-day Trash & Restore, Audit Log, Brand Kit, and Editor Roles

## Environment Variables (never commit to Git)
- `BOT_TOKEN`
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `OWNER_ID`
- `JWT_SECRET`
- `API_URL`
- `DASHBOARD_ORIGIN` (e.g. `https://nation9.me`)
- `GITHUB_BACKUP_TOKEN` (optional, for nightly `data.db` backups)

## Update Routine
1. Commit changes to `main`.
2. In Bot-Hosting console, press **Stop**, then **Start**.
3. Confirm console prints `Online as ...` and `API listening on port ...`.