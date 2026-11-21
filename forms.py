import discord

SHORT_MAX = 200
PARAGRAPH_MAX = 600


class FormModal(discord.ui.Modal):
    def __init__(self, form):
        title = str(form.get("title") or form.get("name") or "Form").strip()[:45] or "Form"
        super().__init__(title=title, timeout=900)
        self.form = form
        self.inputs = []
        for field in form.get("fields", [])[:5]:
            paragraph = field.get("style") == "paragraph"
            item = discord.ui.TextInput(
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
                placeholder=str(field.get("placeholder") or "")[:100] or None,
                required=bool(field.get("required", True)),
                max_length=PARAGRAPH_MAX if paragraph else SHORT_MAX,
            )
            self.inputs.append(item)
            label = str(field.get("label") or "Question")[:45]
            self.add_item(discord.ui.Label(text=label, component=item))

    async def on_submit(self, interaction):
        try:
            channel = interaction.client.get_channel(int(self.form.get("channel_id") or 0))
        except ValueError:
            channel = None
        same_server = (
            channel is not None
            and getattr(channel, "guild", None) is not None
            and channel.guild.id == interaction.guild_id
        )
        if not same_server:
            await interaction.response.send_message(
                "Sorry, I couldn't deliver your answers (channel not found in this server).", ephemeral=True
            )
            return

        name = self.form.get("name") or "Form"
        lines = [f"## {name}", f"From {interaction.user.mention} ({interaction.user.name})"]
        for field, item in zip(self.form.get("fields", []), self.inputs):
            answer = item.value.strip() or "(no answer)"
            label = str(field.get("label") or "Question")[:45]
            lines.append(f"**{label}**\n{answer}")

        view = discord.ui.LayoutView(timeout=None)
        box = discord.ui.Container(accent_colour=discord.Colour(0x5865F2))
        box.add_item(discord.ui.TextDisplay("\n\n".join(lines)))
        view.add_item(box)
        try:
            await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message("Sorry, I couldn't deliver your answers.", ephemeral=True)
            return

        thanks = str(self.form.get("thanks") or "Thanks! Your response was sent.")[:1900]
        await interaction.response.send_message(thanks, ephemeral=True)
