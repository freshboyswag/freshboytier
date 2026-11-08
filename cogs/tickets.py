import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View

from config import SUPERADMINS
from database import get_tickets_collection, get_ticket_settings, is_recruitment_open


# ───────────────────────────────────────────────
# Доступ
# ───────────────────────────────────────────────

def has_ticket_access(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if member.id in SUPERADMINS:
        return True
    settings = get_ticket_settings()
    access_ids = set(settings.get("access_role_ids", []))
    return any(role.id in access_ids for role in member.roles)


def _style(field: dict) -> discord.TextStyle:
    return discord.TextStyle.paragraph if field.get("style") == "paragraph" else discord.TextStyle.short


# ───────────────────────────────────────────────
# Модалка подачи заявки — поля собираются динамически из настроек
# ───────────────────────────────────────────────

class TicketModal(Modal):
    def __init__(self, fields: list):
        super().__init__(title="Подать заявку"[:45])
        active_fields = [f for f in fields if f.get("label")]  # поля с пустым заголовком — "выключены"
        self._field_defs = active_fields
        self._inputs = []
        for field in active_fields[:5]:  # Discord не даёт больше 5 полей в модалке
            text_input = TextInput(
                label=field["label"][:45],
                placeholder=field.get("placeholder", "")[:100],
                style=_style(field),
                required=field.get("required", True),
                max_length=field.get("max_length") or 1000,
            )
            self.add_item(text_input)
            self._inputs.append((field["id"], field["label"], text_input))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        settings = get_ticket_settings()
        guild = interaction.guild
        category = guild.get_channel(settings["channel_category"])
        username = interaction.user.name

        ping_role_ids = settings.get("ping_role_ids", [])
        access_role_ids = settings.get("access_role_ids", [])
        visible_role_ids = set(ping_role_ids) | set(access_role_ids)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role_id in visible_role_ids:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            f"ticket-{username}",
            category=category,
            overwrites=overwrites,
        )

        submitted_fields = []
        embed = discord.Embed(title="Новая заявка", color=0x2b2d31)
        for field_id, label, text_input in self._inputs:
            embed.add_field(name=label, value=text_input.value or "-", inline=False)
            submitted_fields.append({"id": field_id, "label": label, "value": text_input.value})
        embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
        embed.add_field(name="Username", value=interaction.user.name, inline=True)
        embed.add_field(name="ID", value=str(interaction.user.id), inline=True)
        embed.timestamp = discord.utils.utcnow()

        mention_text = " ".join(f"<@&{role_id}>" for role_id in ping_role_ids) or None
        ticket_msg = await ticket_channel.send(
            content=mention_text,
            embed=embed,
            view=TicketActionsView(),
        )

        try:
            col = get_tickets_collection()
            col.insert_one({
                "applicant_id": str(interaction.user.id),
                "applicant_name": interaction.user.name,
                "channel_id": str(ticket_channel.id),
                "ticket_msg_id": str(ticket_msg.id),
                "results_msg_id": None,
                "fields": submitted_fields,
            })
        except Exception as e:
            print(f"[ERROR] MongoDB tickets: {e}")

        await interaction.followup.send(f"заявка подана! {ticket_channel.mention}", ephemeral=True)


# ───────────────────────────────────────────────
# Принятие / отклонение
# ───────────────────────────────────────────────

async def _process_accept(interaction: discord.Interaction, applicant, applicant_name: str,
                           ticket_channel: discord.TextChannel, ticket_data: dict):
    settings = get_ticket_settings()
    guild = interaction.guild
    results_channel = guild.get_channel(settings["channel_results"])
    logs_channel = guild.get_channel(settings["channel_accepted_logs"])

    results_msg_id = ticket_data.get("results_msg_id")
    if results_msg_id and results_channel:
        try:
            msg = await results_channel.fetch_message(int(results_msg_id))
            await msg.delete()
        except Exception:
            pass

    applicant_mention = applicant.mention if applicant else f"`{applicant_name}` (вышел с сервера)"

    embed = discord.Embed(color=0x57f287)
    embed.description = (
        f"Заявка пользователя {applicant_mention}\n\n"
        f"На вступление в семью была принята! 🎉\n"
        f"Рассматривал заявку: {interaction.user.mention}"
    )
    if results_channel:
        await results_channel.send(content=applicant_mention, embed=embed)

    if logs_channel:
        log_embed = discord.Embed(title="Заявка принята", color=0x57f287)
        log_embed.add_field(name="Пользователь", value=f"{applicant_mention} (`{applicant_name}`)", inline=False)
        for field in ticket_data.get("fields", []):
            log_embed.add_field(name=field.get("label", "?"), value=field.get("value") or "-", inline=False)
        log_embed.add_field(name="Рассматривал", value=interaction.user.mention, inline=False)
        log_embed.timestamp = discord.utils.utcnow()
        await logs_channel.send(embed=log_embed)

    try:
        col = get_tickets_collection()
        col.delete_one({"channel_id": str(ticket_channel.id)})
    except Exception as e:
        print(f"[ERROR] MongoDB: {e}")

    await ticket_channel.delete()


class RejectModal(Modal, title="Причина отклонения"):
    reason = TextInput(
        label="Причина",
        placeholder="укажи причину отклонения",
        style=discord.TextStyle.short,
        min_length=1, max_length=300,
    )

    def __init__(self, applicant, applicant_name: str, ticket_channel: discord.TextChannel, ticket_data: dict):
        super().__init__()
        self.applicant = applicant
        self.applicant_name = applicant_name
        self.ticket_channel = ticket_channel
        self.ticket_data = ticket_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        settings = get_ticket_settings()
        guild = interaction.guild
        results_channel = guild.get_channel(settings["channel_results"])
        logs_channel = guild.get_channel(settings["channel_rejected_logs"])

        results_msg_id = self.ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        applicant_mention = self.applicant.mention if self.applicant else f"`{self.applicant_name}` (вышел с сервера)"

        embed = discord.Embed(color=0xff4444)
        embed.description = (
            f"Заявка пользователя {applicant_mention}\n\n"
            f"На вступление в семью была отклонена!\n"
            f"Причина: {self.reason.value}\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        if logs_channel:
            log_embed = discord.Embed(title="Заявка отклонена", color=0xff4444)
            log_embed.add_field(name="Пользователь", value=f"{applicant_mention} (`{self.applicant_name}`)", inline=False)
            for field in self.ticket_data.get("fields", []):
                log_embed.add_field(name=field.get("label", "?"), value=field.get("value") or "-", inline=False)
            log_embed.add_field(name="Причина отклонения", value=self.reason.value, inline=False)
            log_embed.add_field(name="Рассматривал", value=interaction.user.mention, inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            await logs_channel.send(embed=log_embed)

        try:
            col = get_tickets_collection()
            col.delete_one({"channel_id": str(self.ticket_channel.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")

        await self.ticket_channel.delete()


class TicketActionsView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def get_ticket_data(self, channel_id: str):
        try:
            col = get_tickets_collection()
            return col.find_one({"channel_id": channel_id})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")
            return None

    async def _resolve_applicant(self, guild: discord.Guild, ticket_data: dict):
        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        if not applicant:
            try:
                applicant = await guild.fetch_member(int(ticket_data["applicant_id"]))
            except Exception:
                applicant = None
        return applicant

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, custom_id="ticket_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        await interaction.response.defer()
        applicant = await self._resolve_applicant(interaction.guild, ticket_data)
        await _process_accept(
            interaction,
            applicant=applicant,
            applicant_name=ticket_data.get("applicant_name", "?"),
            ticket_channel=interaction.channel,
            ticket_data=ticket_data,
        )

    @discord.ui.button(label="Взять на рассмотрение", style=discord.ButtonStyle.blurple, custom_id="ticket_review")
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()

        settings = get_ticket_settings()
        guild = interaction.guild
        channel = interaction.channel

        # сносим обе роли (пинг + доступ) из прав именно этого канала — не трогая
        # сами роли на сервере. Независимо от ролей в будущем, остаётся только
        # тот, кто явно получил персональный доступ ниже.
        role_ids = set(settings.get("ping_role_ids", [])) | set(settings.get("access_role_ids", []))
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role:
                try:
                    await channel.set_permissions(role, overwrite=None)
                except discord.HTTPException:
                    pass

        # инициатор уже имеет персональный доступ с момента создания канала — не трогаем
        await channel.set_permissions(interaction.user, view_channel=True, send_messages=True)

        await channel.send(
            f"{interaction.user.mention} взял заявку на рассмотрение"
        )

    @discord.ui.button(label="Вызвать на обзвон", style=discord.ButtonStyle.blurple, custom_id="ticket_call")
    async def call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()

        settings = get_ticket_settings()
        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.followup.send("данные тикета не найдены", ephemeral=True)
            return

        applicant = await self._resolve_applicant(guild, ticket_data)
        results_channel = guild.get_channel(settings["channel_results"])
        call_channel_id = settings["channel_call"]

        call_embed = discord.Embed(
            title="Вы были вызваны на обзвон",
            description=f"Зайдите в голосовой канал <#{call_channel_id}> и ожидайте, пока вас мувнут",
            color=0x1ABC9C,
        )

        dm_sent = False
        if applicant:
            try:
                await applicant.send(embed=call_embed)
                dm_sent = True
            except discord.Forbidden:
                pass

        results_msg = None
        if not dm_sent and results_channel and applicant:
            results_msg = await results_channel.send(content=applicant.mention, embed=call_embed)

        if results_msg:
            try:
                col = get_tickets_collection()
                col.update_one(
                    {"channel_id": str(interaction.channel.id)},
                    {"$set": {"results_msg_id": str(results_msg.id)}}
                )
            except Exception as e:
                print(f"[ERROR] MongoDB: {e}")

        await interaction.followup.send("уведомление отправлено", ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, custom_id="ticket_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_ticket_access(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        applicant = await self._resolve_applicant(interaction.guild, ticket_data)
        await interaction.response.send_modal(
            RejectModal(
                applicant=applicant,
                applicant_name=ticket_data.get("applicant_name", "?"),
                ticket_channel=interaction.channel,
                ticket_data=ticket_data,
            )
        )


# ───────────────────────────────────────────────
# Панель подачи заявки
# ───────────────────────────────────────────────

class TicketPanelLayout(discord.ui.LayoutView):
    """Components V2: картинка, текст и меню подачи заявки в одном непрерывном контейнере
    (в отличие от двух обычных embed'ов тут реально нет шва между картинкой и текстом)."""

    def __init__(self):
        super().__init__(timeout=None)
        settings = get_ticket_settings()
        open_now = is_recruitment_open()

        container = discord.ui.Container(accent_colour=discord.Colour(0x2b2d31))

        image_url = settings.get("panel_image_url")
        if image_url:
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url)))

        title = settings.get("panel_title") or ""
        description = settings.get("panel_description") or ""
        container.add_item(discord.ui.TextDisplay(f"## {title}\n\n{description}"))

        select = discord.ui.Select(
            placeholder="Выбери действие..." if open_now else "Набор закрыт",
            custom_id="submit_ticket_select",
            options=[discord.SelectOption(label="Подать заявку", value="submit_ticket", emoji="📩")],
            disabled=not open_now,
        )
        select.callback = self._on_submit_select
        container.add_item(discord.ui.ActionRow(select))

        self.add_item(container)

    async def _on_submit_select(self, interaction: discord.Interaction):
        if not is_recruitment_open():
            await interaction.response.send_message("❌ набор в семью сейчас закрыт, попробуй позже", ephemeral=True)
            return
        settings = get_ticket_settings()
        await interaction.response.send_modal(TicketModal(settings.get("fields", [])))
        # сбрасываем визуально выбранный пункт селекта, иначе после закрытия формы
        # без отправки клиент не даёт выбрать тот же пункт снова
        try:
            await interaction.message.edit(view=TicketPanelLayout())
        except discord.HTTPException:
            pass


# оставлено для обратной совместимости с тем, что ещё может ссылаться на старое имя
TicketPanelView = TicketPanelLayout


# ───────────────────────────────────────────────
# Ког
# ───────────────────────────────────────────────

class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketPanelView())
        bot.add_view(TicketActionsView())


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
