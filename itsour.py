import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import os
import re
from pymongo import MongoClient
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────────────
# ОСНОВНОЙ БОТ (личные каналы)
# ───────────────────────────────────────────────
PANEL_CHANNEL_ID = 1514639955027034212

TIER_ROLES = {
    1510603348276023547: 1510953268799606865,  # тир б -> категория
    1510603326331293846: 1510953111089451079,  # тир а -> категория
    1510603296321306774: 1510952983867686922,  # тир с -> категория
}
NO_TIER_CATEGORY_ID = 1510952722214551653
REFRESH_ROLES = {1510604555040198816, 1510601395999346819}
ADMIN_ROLE_ID = 1510608237878181958

# ───────────────────────────────────────────────
# ТИКЕТ-СИСТЕМА
# ───────────────────────────────────────────────
TICKETS_CHANNEL_ID    = 1510606227355205712
RESULTS_CHANNEL_ID    = 1510626562637041714
LOGS_ACCEPTED_ID      = 1515439516666826823
LOGS_REJECTED_ID      = 1515439610547929088
TICKET_CATEGORY_ID    = 1515440541012066325
TICKET_VIEW_ROLE_ID   = 1510614323754434670
RECRUIT_ROLE_IDS      = {1510614323754434670, 1510610391267545138, 1510601395999346819, 1510604555040198816}
ACCEPT_ROLE_ID        = 1510603939664629891
CALL_CHANNEL_ID       = 1510628196914171984
RESULTS_DM_CHANNEL_ID = 1510626562637041714

# ───────────────────────────────────────────────
# ЛОГИ
# ───────────────────────────────────────────────
VOICE_LOG_CHANNEL_ID = 1515730296161439975

MONGO_URL = os.getenv("MONGO_URL")


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return client["freshboyswag"]

def get_collection():
    return get_db()["channels"]

def get_tickets_collection():
    return get_db()["tickets"]

def get_logs_collection():
    return get_db()["logs_settings"]

def has_refresh_role(member):
    return any(role.id in REFRESH_ROLES for role in member.roles)

def has_recruit_role(member):
    return any(role.id in RECRUIT_ROLE_IDS for role in member.roles)

def get_category_for_member(member: discord.Member):
    member_role_ids = {role.id for role in member.roles}
    for role_id, category_id in TIER_ROLES.items():
        if role_id in member_role_ids:
            return category_id
    return NO_TIER_CATEGORY_ID

def is_log_enabled(log_type: str) -> bool:
    try:
        col = get_logs_collection()
        doc = col.find_one({"type": log_type})
        return doc.get("enabled", False) if doc else False
    except Exception:
        return False

def set_log_enabled(log_type: str, enabled: bool):
    try:
        col = get_logs_collection()
        col.update_one({"type": log_type}, {"$set": {"enabled": enabled}}, upsert=True)
    except Exception as e:
        print(f"[ERROR] MongoDB logs: {e}")


# ═══════════════════════════════════════════════
# ЛИЧНЫЕ КАНАЛЫ
# ═══════════════════════════════════════════════

class ChannelModal(Modal, title="Создать личное дело"):
    channel_name = TextInput(
        label="Название",
        placeholder="ник в игре",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        try:
            collection = get_collection()
            existing_entry = collection.find_one({"user_id": user_id})
        except Exception as e:
            await interaction.followup.send("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB: {e}")
            return

        if existing_entry:
            await interaction.followup.send("у тебя уже есть канал или квота израсходована", ephemeral=True)
            return

        category_id = get_category_for_member(interaction.user)
        category = interaction.guild.get_channel(category_id)

        if not category:
            await interaction.followup.send("категория не найдена, обратись к администратору", ephemeral=True)
            return

        name = self.channel_name.value.strip().replace(" ", "-").lower()
        admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(name, category=category, overwrites=overwrites)
        collection.insert_one({"user_id": user_id, "channel_id": channel.id})
        await interaction.followup.send(f"канал {channel.mention} создан!", ephemeral=True)


class PanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать личное дело", style=discord.ButtonStyle.blurple, custom_id="reg_channel", emoji="📁")
    async def reg_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal())


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📁 Создание личного дела",
        description=(
            "Твой канал будет виден только тебе, людям с ролью <@&1510608237878181958> и админам.\n\n"
            "В нем должны быть твои откаты, которые ты после мп должен будешь залить на ютуб/рутуб, "
            "твои скрины и откаты с гг при необходимости. "
            "Тут же ты можешь задавать вопросы по скиллу"
        ),
        color=0x1ABC9C
    )
    embed.set_image(url="https://cdn.discordapp.com/attachments/1510631159414128700/1510639319969304646/0531.gif")
    return embed


# ═══════════════════════════════════════════════
# ТИКЕТ-СИСТЕМА
# ═══════════════════════════════════════════════

class TicketModal(Modal, title="Подать заявку"):
    nick = TextInput(
        label="ник / статик / имя / возраст",
        placeholder="black / 133371 / мартин / 22",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )
    online = TextInput(
        label="средний онлайн / прайм-тайм",
        placeholder="6-8 часов / 16:00 - 23:00",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )
    history = TextInput(
        label="история семей",
        placeholder="alliance, faraday, cartel",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=200
    )
    source = TextInput(
        label="откуда узнали о семье",
        placeholder="знакомый / маркет / игра",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=200
    )
    clips = TextInput(
        label="откаты спешик/тяжка + сайга по 10 минут",
        placeholder="ссылки только с youtube/rutube/google drive",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        ticket_view_role = guild.get_role(TICKET_VIEW_ROLE_ID)
        category = guild.get_channel(TICKET_CATEGORY_ID)
        username = interaction.user.name

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if ticket_view_role:
            overwrites[ticket_view_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            f"ticket-{username}",
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(title="Новая заявка", color=0x2b2d31)
        embed.add_field(name="ник / статик / имя / возраст", value=self.nick.value, inline=False)
        embed.add_field(name="средний онлайн / прайм-тайм", value=self.online.value, inline=False)
        embed.add_field(name="история семей", value=self.history.value, inline=False)
        embed.add_field(name="откуда узнали о семье", value=self.source.value, inline=False)
        embed.add_field(name="откаты спешик/тяжка + сайга по 10 минут", value=self.clips.value, inline=False)
        embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
        embed.add_field(name="Username", value=interaction.user.name, inline=True)
        embed.add_field(name="ID", value=str(interaction.user.id), inline=True)
        embed.timestamp = discord.utils.utcnow()

        mention_text = f"<@&{TICKET_VIEW_ROLE_ID}>"
        ticket_msg = await ticket_channel.send(
            content=mention_text,
            embed=embed,
            view=TicketActionsView(applicant_id=interaction.user.id)
        )

        try:
            col = get_tickets_collection()
            col.insert_one({
                "applicant_id": str(interaction.user.id),
                "applicant_name": interaction.user.name,
                "channel_id": str(ticket_channel.id),
                "ticket_msg_id": str(ticket_msg.id),
                "results_msg_id": None,
                "fields": {
                    "nick": self.nick.value,
                    "online": self.online.value,
                    "history": self.history.value,
                    "source": self.source.value,
                    "clips": self.clips.value,
                }
            })
        except Exception as e:
            print(f"[ERROR] MongoDB tickets: {e}")

        await interaction.followup.send(f"заявка подана! {ticket_channel.mention}", ephemeral=True)


class RejectModal(Modal, title="Причина отклонения"):
    reason = TextInput(
        label="Причина",
        placeholder="укажи причину отклонения",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=300
    )

    def __init__(self, applicant: discord.Member, ticket_channel: discord.TextChannel, ticket_data: dict):
        super().__init__()
        self.applicant = applicant
        self.ticket_channel = ticket_channel
        self.ticket_data = ticket_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)
        logs_channel = guild.get_channel(LOGS_REJECTED_ID)

        results_msg_id = self.ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        embed = discord.Embed(color=0xff4444)
        embed.description = (
            f"Заявка пользователя {self.applicant.mention}\n\n"
            f"На вступление в семью была отклонена!\n"
            f"Причина: {self.reason.value}\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        if logs_channel:
            log_embed = discord.Embed(title="Заявка отклонена", color=0xff4444)
            log_embed.add_field(name="Пользователь", value=f"{self.applicant.mention} (`{self.applicant.name}`)", inline=False)
            fields = self.ticket_data.get("fields", {})
            log_embed.add_field(name="ник / статик / имя / возраст", value=fields.get("nick", "-"), inline=False)
            log_embed.add_field(name="средний онлайн / прайм-тайм", value=fields.get("online", "-"), inline=False)
            log_embed.add_field(name="история семей", value=fields.get("history", "-"), inline=False)
            log_embed.add_field(name="откуда узнали о семье", value=fields.get("source", "-"), inline=False)
            log_embed.add_field(name="откаты", value=fields.get("clips", "-"), inline=False)
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
    def __init__(self, applicant_id: int = None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    async def get_ticket_data(self, channel_id: str):
        try:
            col = get_tickets_collection()
            return col.find_one({"channel_id": channel_id})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")
            return None

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, custom_id="ticket_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.followup.send("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)
        logs_channel = guild.get_channel(LOGS_ACCEPTED_ID)

        results_msg_id = ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        if applicant:
            accept_role = guild.get_role(ACCEPT_ROLE_ID)
            if accept_role:
                try:
                    await applicant.add_roles(accept_role)
                except Exception as e:
                    print(f"[ERROR] Не удалось выдать роль: {e}")

        embed = discord.Embed(color=0x2ecc71)
        embed.description = (
            f"Заявка пользователя {applicant.mention if applicant else ticket_data['applicant_name']}\n\n"
            f"На вступление в семью была принята! 🎉\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        if logs_channel:
            log_embed = discord.Embed(title="Заявка принята", color=0x2ecc71)
            log_embed.add_field(name="Пользователь", value=f"{applicant.mention if applicant else ticket_data['applicant_name']} (`{ticket_data['applicant_name']}`)", inline=False)
            fields = ticket_data.get("fields", {})
            log_embed.add_field(name="ник / статик / имя / возраст", value=fields.get("nick", "-"), inline=False)
            log_embed.add_field(name="средний онлайн / прайм-тайм", value=fields.get("online", "-"), inline=False)
            log_embed.add_field(name="история семей", value=fields.get("history", "-"), inline=False)
            log_embed.add_field(name="откуда узнали о семье", value=fields.get("source", "-"), inline=False)
            log_embed.add_field(name="откаты", value=fields.get("clips", "-"), inline=False)
            log_embed.add_field(name="Принял", value=interaction.user.mention, inline=False)
            log_embed.timestamp = discord.utils.utcnow()
            await logs_channel.send(embed=log_embed)

        try:
            col = get_tickets_collection()
            col.delete_one({"channel_id": str(interaction.channel.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB: {e}")

        await interaction.channel.delete()

    @discord.ui.button(label="Взять на рассмотрение", style=discord.ButtonStyle.green, custom_id="ticket_review")
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.send(f"{interaction.user.mention} начал рассмотрение заявки")

    @discord.ui.button(label="Вызвать на обзвон", style=discord.ButtonStyle.green, custom_id="ticket_call")
    async def call(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer()

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.followup.send("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        results_channel = guild.get_channel(RESULTS_CHANNEL_ID)

        call_embed = discord.Embed(
            title="Вы были вызваны на обзвон",
            description=f"Зайдите в голосовой канал <#{CALL_CHANNEL_ID}> и ожидайте пока вас мувнут",
            color=0x1ABC9C
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
            results_msg = await results_channel.send(
                content=applicant.mention,
                embed=call_embed
            )

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
        if not has_recruit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        if not applicant:
            await interaction.response.send_message("пользователь не найден", ephemeral=True)
            return

        await interaction.response.send_modal(
            RejectModal(
                applicant=applicant,
                ticket_channel=interaction.channel,
                ticket_data=ticket_data
            )
        )


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 Подать заявку", style=discord.ButtonStyle.blurple, custom_id="submit_ticket")
    async def submit_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())


def build_ticket_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="👋 Путь в семью начинается здесь!",
        description=(
            f"Обычно приглашение на обзвон отправляется в личные сообщения, "
            f"если лс закрыт то оно придёт в канал <#{RESULTS_DM_CHANNEL_ID}>\n\n"
            "Все поля обязательны для заполнения\nЗаявки, заполненные не по форме, будут сразу закрыты\n"
            "Заявки без откатов будут закрыты, писать «покажу на демке», «кину позже» не нужно\n"
            "Откаты с гг должны быть не старше 2 недель\n"
            "Тикеты рассматриваются в течение 48 часов"
        ),
        color=0x2b2d31
    )
    embed.set_image(url="https://media.discordapp.net/attachments/1468989054451191840/1515329976151310376/photo_2026-06-13_15-20-03.jpg?ex=6a2e9c83&is=6a2d4b03&hm=6737177b88f5f1d677b6eb893b72a2d1691fb2da128946e5165160f23f2dda8c&=&format=webp&width=900&height=900")
    return embed


# ═══════════════════════════════════════════════
# ВОЙС ЛОГИ
# ═══════════════════════════════════════════════

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if not is_log_enabled("voice"):
        return

    guild = member.guild
    log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
    if not log_channel:
        return

    # Зашёл в канал
    if before.channel is None and after.channel is not None:
        embed = discord.Embed(color=0x2ecc71)
        embed.description = f"🟢 {member.mention} зашёл в **{after.channel.name}**"
        embed.timestamp = discord.utils.utcnow()
        await log_channel.send(embed=embed)
        return

    # Вышел из канала
    if before.channel is not None and after.channel is None:
        embed = discord.Embed(color=0xff4444)
        embed.description = f"🔴 {member.mention} вышел из **{before.channel.name}**"
        embed.timestamp = discord.utils.utcnow()
        await log_channel.send(embed=embed)
        return

    # Сам перешёл между каналами — мув через аудит лог обрабатывается в on_audit_log_entry_create
    # Здесь только самостоятельный переход (без модератора)
    if before.channel is not None and after.channel is not None and before.channel != after.channel:
        import asyncio
        await asyncio.sleep(1)
        # Проверяем — если недавно был мув в аудит логе, значит это был мув, не логируем здесь
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_move):
                if (discord.utils.utcnow() - entry.created_at).total_seconds() < 3:
                    # Был мув — on_audit_log_entry_create уже залогировал
                    return
        except Exception:
            pass
        # Мува не было — сам перешёл
        embed = discord.Embed(color=0x3498db)
        embed.description = f"🔄 {member.mention} перешёл из **{before.channel.name}** в **{after.channel.name}**"
        embed.timestamp = discord.utils.utcnow()
        await log_channel.send(embed=embed)


@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    if not is_log_enabled("voice"):
        return

    guild = entry.guild
    log_channel = guild.get_channel(VOICE_LOG_CHANNEL_ID)
    if not log_channel:
        return

    # Мув участника
    if entry.action == discord.AuditLogAction.member_move:
        target = entry.target
        channel = entry.extra.channel if hasattr(entry, "extra") and entry.extra and hasattr(entry.extra, "channel") else None
        embed = discord.Embed(color=0x3498db)
        if channel:
            embed.description = f"➡️ {entry.user.mention} переместил {target.mention} в **{channel.name}**"
        else:
            embed.description = f"➡️ {entry.user.mention} переместил {target.mention} в другой канал"
        embed.timestamp = discord.utils.utcnow()
        await log_channel.send(embed=embed)

    # Кик из войса
    elif entry.action == discord.AuditLogAction.member_disconnect:
        target = entry.target
        embed = discord.Embed(color=0xff4444)
        embed.description = f"⛔ {entry.user.mention} выкинул {target.mention} из голосового канала"
        embed.timestamp = discord.utils.utcnow()
        await log_channel.send(embed=embed)

    # Мут / размут / деф / андеф сервером
    elif entry.action == discord.AuditLogAction.member_update:
        target = entry.target
        if not target:
            return

        before_val = entry.before
        after_val = entry.after

        # Мут
        if hasattr(before_val, "mute") and hasattr(after_val, "mute") and before_val.mute != after_val.mute:
            embed = discord.Embed(color=0xe74c3c if after_val.mute else 0x2ecc71)
            if after_val.mute:
                embed.description = f"🔇 {target.mention} был замучен {entry.user.mention}"
            else:
                embed.description = f"🔊 {target.mention} был размучен {entry.user.mention}"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)

        # Деф
        if hasattr(before_val, "deaf") and hasattr(after_val, "deaf") and before_val.deaf != after_val.deaf:
            embed = discord.Embed(color=0xe74c3c if after_val.deaf else 0x2ecc71)
            if after_val.deaf:
                embed.description = f"🎧 {entry.user.mention} выключил наушники {target.mention}"
            else:
                embed.description = f"🎧 {entry.user.mention} включил наушники {target.mention}"
            embed.timestamp = discord.utils.utcnow()
            await log_channel.send(embed=embed)



# ═══════════════════════════════════════════════
# СИСТЕМА ПЛЮСОВ
# ═══════════════════════════════════════════════

REG_ADMIN_ROLES = {1510610350532329642, 1510610391267545138, 1510601395999346819, 1510604555040198816}
CHECKMARK_EMOJI = "✅"

# Тиры — порядок важен (С > А > Б > нотир)
TIER_ORDER = [
    ("Tier S", 1510603296321306774),
    ("Tier A", 1510603326331293846),
    ("Tier B", 1510603348276023547),
    ("No Tier", None),
]

VOICE_CHECK_CHANNELS = [
    1510602284373905540,
    1510602312647835768,
    1510607276761940060,
    1510607301260869702,
    1510609306498760854,
    1510609331966709881,
    1515761158311641099,
]

def has_reg_admin_role(member):
    return any(role.id in REG_ADMIN_ROLES for role in member.roles)

def get_tier_label(member: discord.Member) -> str:
    member_role_ids = {r.id for r in member.roles}
    for label, role_id in TIER_ORDER:
        if role_id is None:
            return label
        if role_id in member_role_ids:
            return label
    return "No Tier"

def get_reg_collection():
    return get_db()["reg_lists"]

def build_reg_embed(data: dict) -> discord.Embed:
    max_slots = data["max_slots"]
    main_list = data["main_list"]
    title = data.get("title", "Список")

    # Группируем по тирам сохраняя глобальный порядковый номер
    tier_groups = {label: [] for label, _ in TIER_ORDER}
    for entry in main_list:
        tier = entry.get("tier", "No Tier")
        if tier not in tier_groups:
            tier = "No Tier"
        tier_groups[tier].append(entry)

    lines = []
    counter = 1
    for label, _ in TIER_ORDER:
        members = tier_groups[label]
        if not members:
            continue
        lines.append(f"**{label}:**")
        for u in members:
            lines.append(f"  {counter}. <@{u['id']}>")
            counter += 1

    description = f"Основной список {len(main_list)}/{max_slots}"
    body = "\n".join(lines) if lines else "пусто"

    embed = discord.Embed(title=title, description=description, color=0x1ABC9C)
    embed.add_field(name="​", value=body, inline=False)
    return embed


async def get_reg_data(message_id: int):
    try:
        col = get_reg_collection()
        return col.find_one({"message_id": str(message_id)})
    except Exception as e:
        print(f"[ERROR] MongoDB reg: {e}")
        return None

async def save_reg_data(message_id: int, update: dict):
    try:
        col = get_reg_collection()
        col.update_one({"message_id": str(message_id)}, {"$set": update})
    except Exception as e:
        print(f"[ERROR] MongoDB reg save: {e}")

async def update_reg_embed(message: discord.Message, data: dict):
    try:
        embed = build_reg_embed(data)
        await message.edit(embed=embed, view=RegView())
    except Exception as e:
        print(f"[ERROR] update reg embed: {e}")

def insert_by_tier(main_list: list, new_entry: dict) -> list:
    """Вставляет участника в нужное место по тиру."""
    tier_order_labels = [label for label, _ in TIER_ORDER]
    new_tier = new_entry.get("tier", "No Tier")
    new_tier_idx = tier_order_labels.index(new_tier) if new_tier in tier_order_labels else len(tier_order_labels)

    # Находим позицию — после последнего участника с таким же или более высоким тиром
    insert_pos = len(main_list)
    for i, u in enumerate(main_list):
        u_tier = u.get("tier", "No Tier")
        u_tier_idx = tier_order_labels.index(u_tier) if u_tier in tier_order_labels else len(tier_order_labels)
        if u_tier_idx > new_tier_idx:
            insert_pos = i
            break

    main_list.insert(insert_pos, new_entry)
    return main_list

async def get_thread_message_reg(thread: discord.Thread, message_id: int):
    """Находит запись в MongoDB по thread_id."""
    try:
        col = get_reg_collection()
        return col.find_one({"thread_id": str(thread.id)})
    except Exception as e:
        print(f"[ERROR] get_thread_message_reg: {e}")
        return None


# ───────────────────────────────────────────────
# ИВЕНТЫ ДЛЯ ПЛЮСОВ
# ───────────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    # Только ветки
    if not isinstance(message.channel, discord.Thread):
        return
    if message.author.bot:
        return
    if "+" not in message.content:
        return

    # Ищем запись по thread_id
    data = await get_thread_message_reg(message.channel, 0)
    if not data:
        return

    # Сохраняем message_id → user_id маппинг для будущего удаления
    try:
        col = get_reg_collection()
        msg_map = data.get("msg_map", {})
        msg_map[str(message.id)] = str(message.author.id)
        col.update_one({"_id": data["_id"]}, {"$set": {"msg_map": msg_map}})
    except Exception as e:
        print(f"[ERROR] on_message reg: {e}")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != CHECKMARK_EMOJI:
        return
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    reactor = guild.get_member(payload.user_id)
    if not reactor or not has_reg_admin_role(reactor):
        return

    # Ищем в какой ветке это произошло
    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.Thread):
        return

    data = await get_thread_message_reg(channel, 0)
    if not data:
        return

    # Получаем сообщение
    try:
        msg = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    if msg.author.bot or "+" not in msg.content:
        return

    user_id = str(msg.author.id)
    main_list = data.get("main_list", [])
    max_slots = data["max_slots"]

    # Уже в списке?
    if any(u["id"] == user_id for u in main_list):
        return

    # Слоты заполнены?
    if len(main_list) >= max_slots:
        return

    member = guild.get_member(int(user_id))
    if not member:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            return

    tier = get_tier_label(member)
    new_entry = {"id": user_id, "nick": member.display_name, "tier": tier}
    main_list = insert_by_tier(main_list, new_entry)

    data["main_list"] = main_list
    await save_reg_data(int(data["message_id"]), {"main_list": main_list})

    # Обновляем эмбед
    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on reaction add: {e}")


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != CHECKMARK_EMOJI:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    reactor = guild.get_member(payload.user_id)
    if not reactor or not has_reg_admin_role(reactor):
        return

    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.Thread):
        return

    data = await get_thread_message_reg(channel, 0)
    if not data:
        return

    # Находим автора сообщения
    try:
        msg = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    user_id = str(msg.author.id)
    main_list = data.get("main_list", [])
    new_list = [u for u in main_list if u["id"] != user_id]

    if len(new_list) == len(main_list):
        return  # не было в списке

    data["main_list"] = new_list
    await save_reg_data(int(data["message_id"]), {"main_list": new_list})

    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on reaction remove: {e}")


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.Thread):
        return

    data = await get_thread_message_reg(channel, 0)
    if not data:
        return

    msg_map = data.get("msg_map", {})
    user_id = msg_map.get(str(payload.message_id))
    if not user_id:
        return

    main_list = data.get("main_list", [])
    new_list = [u for u in main_list if u["id"] != user_id]

    if len(new_list) == len(main_list):
        return

    # Убираем из маппинга
    msg_map.pop(str(payload.message_id), None)
    data["main_list"] = new_list

    await save_reg_data(int(data["message_id"]), {"main_list": new_list, "msg_map": msg_map})

    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on message delete: {e}")


# ───────────────────────────────────────────────
# ПРОВЕРКА ПО ВОЙСУ
# ───────────────────────────────────────────────

class VoiceChannelSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, reg_message_id: int, reg_data: dict):
        self.reg_message_id = reg_message_id
        self.reg_data = reg_data

        options = []
        for ch_id in VOICE_CHECK_CHANNELS:
            ch = guild.get_channel(ch_id)
            if ch:
                options.append(discord.SelectOption(label=ch.name, value=str(ch.id)))

        if not options:
            options.append(discord.SelectOption(label="каналы не найдены", value="none"))

        super().__init__(placeholder="выберите голосовой канал...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.values[0] == "none":
            await interaction.followup.send("голосовые каналы не найдены", ephemeral=True)
            return

        voice_channel = interaction.guild.get_channel(int(self.values[0]))
        if not voice_channel:
            await interaction.followup.send("канал не найден", ephemeral=True)
            return

        members_in_voice = {str(m.id) for m in voice_channel.members}
        main_list = self.reg_data.get("main_list", [])

        in_voice = [u for u in main_list if u["id"] in members_in_voice]
        not_in_voice = [u for u in main_list if u["id"] not in members_in_voice]

        lines = []
        lines.append(f"**Проверка по войсу: {voice_channel.name}**")
        lines.append("")
        lines.append(f"**Присутствуют ({len(in_voice)}/{len(main_list)}):**")
        if in_voice:
            for u in in_voice:
                lines.append("✅ " + u["nick"])
        else:
            lines.append("никого")
        lines.append("")
        lines.append(f"**Отсутствуют ({len(not_in_voice)}/{len(main_list)}):**")
        if not_in_voice:
            for u in not_in_voice:
                lines.append("❌ " + u["nick"])
        else:
            lines.append("все на месте")

        result = "\n".join(lines)
        await interaction.followup.send(result, ephemeral=True)

        # Лог в ветку — тегаем только отсутствующих
        try:
            reg_channel = interaction.guild.get_channel(int(self.reg_data.get("channel_id", 0)))
            if reg_channel:
                reg_msg = await reg_channel.fetch_message(self.reg_message_id)
                if reg_msg and reg_msg.thread:
                    absent_mentions = " ".join(f"<@{u['id']}>" for u in not_in_voice)
                    log_text = (
                        f"{interaction.user.display_name} провёл проверку по войсу **{voice_channel.name}**"
                        + (f"Отсутствуют: {absent_mentions}" if absent_mentions else "Все присутствуют")
                    )
                    await reg_msg.thread.send(log_text)
        except Exception as e:
            print(f"[ERROR] voice check log: {e}")


class VoiceSelectView(View):
    def __init__(self, reg_message_id: int, reg_data: dict):
        super().__init__(timeout=30)
        self.reg_message_id = reg_message_id
        self.reg_data = reg_data

    async def setup(self, guild: discord.Guild):
        self.add_item(VoiceChannelSelect(guild, self.reg_message_id, self.reg_data))


# ───────────────────────────────────────────────
# МОД МЕНЮ
# ───────────────────────────────────────────────

class ModMenuSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, reg_message: discord.Message, data: dict):
        self.reg_message = reg_message
        self.data = data
        self.guild = guild

        options = [
            discord.SelectOption(label="📋 Сформировать список", value="form"),
            discord.SelectOption(label="📣 Тегнуть список", value="tag"),
            discord.SelectOption(label="🎤 Проверка по войсу", value="voice"),
        ]
        super().__init__(placeholder="выберите действие...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.values[0] == "form":
            thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
            if not thread:
                await interaction.followup.send("ветка не найдена", ephemeral=True)
                return

            # Полная пересборка списка из ветки
            valid_users = []
            async for msg in thread.history(limit=500, oldest_first=True):
                if msg.author.bot or "+" not in msg.content:
                    continue
                has_check = False
                for reaction in msg.reactions:
                    if str(reaction.emoji) == CHECKMARK_EMOJI:
                        async for user in reaction.users():
                            if not user.bot:
                                m = interaction.guild.get_member(user.id)
                                if m and has_reg_admin_role(m):
                                    has_check = True
                                    break
                    if has_check:
                        break
                if has_check:
                    uid = str(msg.author.id)
                    if not any(u["id"] == uid for u in valid_users):
                        member = interaction.guild.get_member(int(uid))
                        if not member:
                            try:
                                member = await interaction.guild.fetch_member(int(uid))
                            except Exception:
                                continue
                        tier = get_tier_label(member)
                        valid_users = insert_by_tier(valid_users, {"id": uid, "nick": member.display_name, "tier": tier})

            self.data["main_list"] = valid_users
            await save_reg_data(int(self.reg_message.id), {"main_list": valid_users})
            await update_reg_embed(self.reg_message, self.data)
            await interaction.followup.send(f"список сформирован: {len(valid_users)} участников", ephemeral=True)

        elif self.values[0] == "tag":
            main_list = self.data.get("main_list", [])
            if not main_list:
                await interaction.followup.send("список пуст", ephemeral=True)
                return
            mentions = " ".join(f"<@{u['id']}>" for u in main_list)
            thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
            if thread:
                await thread.send(mentions)
                await interaction.followup.send("список тегнут в ветку", ephemeral=True)
            else:
                await interaction.followup.send(mentions)

        elif self.values[0] == "voice":
            view = VoiceSelectView(reg_message_id=self.reg_message.id, reg_data=self.data)
            await view.setup(interaction.guild)
            await interaction.followup.send("выберите голосовой канал для проверки:", view=view, ephemeral=True)


class ModMenuView(View):
    def __init__(self, guild: discord.Guild, reg_message: discord.Message, data: dict):
        super().__init__(timeout=30)
        self.add_item(ModMenuSelect(guild, reg_message, data))


class RegView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛠 Мод", style=discord.ButtonStyle.grey, custom_id="reg_mod_menu")
    async def reg_mod_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_reg_admin_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        data = await get_reg_data(interaction.message.id)
        if not data:
            await interaction.followup.send("список не найден", ephemeral=True)
            return

        view = ModMenuView(interaction.guild, interaction.message, data)
        await interaction.followup.send("выберите действие:", view=view, ephemeral=True)



# ═══════════════════════════════════════════════
# СИСТЕМА ОТПУСКОВ
# ═══════════════════════════════════════════════

VACATION_CHANNEL_ID   = 1515989899793268877
VACATION_ROLE_ID      = 1515990627832172665
VACATION_REQUESTS_ID  = 1516073358729678968
VACATION_MOD_ROLES    = {1510610350532329642, 1510610391267545138, 1510601395999346819, 1510604555040198816}

def has_vacation_mod_role(member):
    return any(role.id in VACATION_MOD_ROLES for role in member.roles)

def get_vacation_collection():
    return get_db()["vacations"]


class VacationModal(Modal, title="Заявка на отпуск"):
    until = TextInput(
        label="До какого отпуск?",
        placeholder="например: 25 июня",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )
    reason = TextInput(
        label="Причина отпуска",
        placeholder="укажи причину",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        requests_channel = guild.get_channel(VACATION_REQUESTS_ID)
        if not requests_channel:
            await interaction.followup.send("канал для заявок не найден", ephemeral=True)
            return

        # Проверяем не в отпуске ли уже
        vacation_role = guild.get_role(VACATION_ROLE_ID)
        if vacation_role and vacation_role in interaction.user.roles:
            await interaction.followup.send("ты уже в отпуске", ephemeral=True)
            return

        embed = discord.Embed(title="Новая заявка на отпуск", color=0x1ABC9C)
        embed.add_field(name="Участник", value=interaction.user.mention, inline=False)
        embed.add_field(name="До какого", value=self.until.value, inline=True)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)
        embed.timestamp = discord.utils.utcnow()

        view = VacationApproveView(applicant_id=interaction.user.id)
        await requests_channel.send(embed=embed, view=view)

        await interaction.followup.send("заявка отправлена, ожидай решения", ephemeral=True)


class VacationApproveView(View):
    def __init__(self, applicant_id: int = None):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    @discord.ui.button(label="✅ Принять", style=discord.ButtonStyle.green, custom_id="vacation_accept")
    async def vacation_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_vacation_mod_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild

        # Получаем айди заявителя из эмбеда через regex
        embed = interaction.message.embeds[0]
        applicant_id = None
        for field in embed.fields:
            if field.name == "Участник":
                match = re.search(r"\d{17,20}", field.value)
                if match:
                    applicant_id = int(match.group())
                break

        if not applicant_id:
            await interaction.followup.send("не удалось найти участника", ephemeral=True)
            return

        applicant = guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await guild.fetch_member(applicant_id)
            except Exception:
                await interaction.followup.send("участник не найден на сервере", ephemeral=True)
                return

        vacation_role = guild.get_role(VACATION_ROLE_ID)

        # Сохраняем все роли кроме @everyone и отпускной
        roles_to_save = [r for r in applicant.roles if r.id != guild.default_role.id and r.id != VACATION_ROLE_ID]
        role_ids = [r.id for r in roles_to_save]

        try:
            col = get_vacation_collection()
            col.update_one(
                {"user_id": str(applicant_id)},
                {"$set": {"user_id": str(applicant_id), "saved_roles": role_ids}},
                upsert=True
            )
        except Exception as e:
            print(f"[ERROR] MongoDB vacation: {e}")

        # Снимаем все роли
        try:
            if roles_to_save:
                await applicant.remove_roles(*roles_to_save, reason="отпуск")
        except Exception as e:
            print(f"[ERROR] снять роли: {e}")

        # Выдаём отпускную роль
        try:
            if vacation_role:
                await applicant.add_roles(vacation_role, reason="отпуск принят")
        except Exception as e:
            print(f"[ERROR] выдать отпускную роль: {e}")

        # Уведомление в лс
        try:
            await applicant.send("ваша заявка на отпуск одобрена")
        except discord.Forbidden:
            pass

        # Редактируем сообщение — сохраняем поля заявки, добавляем результат
        old_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=old_embed.title, color=0x2ecc71)
        for field in old_embed.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name="Решение", value=f"✅ **Одобрена** модератором {interaction.user.mention}", inline=False)
        new_embed.timestamp = discord.utils.utcnow()
        await interaction.message.edit(embed=new_embed, view=None)

    @discord.ui.button(label="❌ Отклонить", style=discord.ButtonStyle.red, custom_id="vacation_reject")
    async def vacation_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_vacation_mod_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild

        embed = interaction.message.embeds[0]
        applicant_id = None
        for field in embed.fields:
            if field.name == "Участник":
                match = re.search(r"\d{17,20}", field.value)
                if match:
                    applicant_id = int(match.group())
                break

        if not applicant_id:
            await interaction.followup.send("не удалось найти участника", ephemeral=True)
            return

        applicant = guild.get_member(applicant_id)
        if not applicant:
            try:
                applicant = await guild.fetch_member(applicant_id)
            except Exception:
                await interaction.followup.send("участник не найден на сервере", ephemeral=True)
                return

        # Уведомление в лс
        try:
            await applicant.send("ваша заявка на отпуск отклонена")
        except discord.Forbidden:
            pass

        # Редактируем сообщение — сохраняем поля заявки, добавляем результат
        old_embed = interaction.message.embeds[0]
        new_embed = discord.Embed(title=old_embed.title, color=0xff4444)
        for field in old_embed.fields:
            new_embed.add_field(name=field.name, value=field.value, inline=field.inline)
        new_embed.add_field(name="Решение", value=f"❌ **Отклонена** модератором {interaction.user.mention}", inline=False)
        new_embed.timestamp = discord.utils.utcnow()
        await interaction.message.edit(embed=new_embed, view=None)


class VacationPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🏖️ Взять отпуск", style=discord.ButtonStyle.green, custom_id="vacation_take")
    async def vacation_take(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VacationModal())

    @discord.ui.button(label="🔙 Выйти из отпуска", style=discord.ButtonStyle.grey, custom_id="vacation_return")
    async def vacation_return(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        vacation_role = guild.get_role(VACATION_ROLE_ID)

        if vacation_role not in interaction.user.roles:
            await interaction.followup.send("ты не в отпуске", ephemeral=True)
            return

        try:
            col = get_vacation_collection()
            doc = col.find_one({"user_id": str(interaction.user.id)})
        except Exception as e:
            await interaction.followup.send("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB vacation: {e}")
            return

        # Снимаем отпускную роль
        try:
            await interaction.user.remove_roles(vacation_role, reason="выход из отпуска")
        except Exception as e:
            print(f"[ERROR] снять отпускную роль: {e}")

        # Возвращаем сохранённые роли
        if doc and doc.get("saved_roles"):
            roles_to_restore = []
            for role_id in doc["saved_roles"]:
                role = guild.get_role(role_id)
                if role:
                    roles_to_restore.append(role)
            if roles_to_restore:
                try:
                    await interaction.user.add_roles(*roles_to_restore, reason="выход из отпуска")
                except Exception as e:
                    print(f"[ERROR] вернуть роли: {e}")

        # Удаляем запись из БД
        try:
            col.delete_one({"user_id": str(interaction.user.id)})
        except Exception as e:
            print(f"[ERROR] MongoDB vacation delete: {e}")

        await interaction.followup.send("ты вышел из отпуска, роли возвращены", ephemeral=True)


def build_vacation_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏖️ Заявка на отпуск",
        description=(
            "Вы можете подать заявку на отпуск если не будете играть какое-то время по каким-угодно причинам.\n\n"
            "При одобрении вам будут сняты все роли и выдана <@&1515990627832172665>, "
            "в любой момент вы можете зайти в этот канал, вернуться из отпуска и вам вернут все роли. "
            "В форме нужно будет указать срок и причину по которой будете отсутствовать"
        ),
        color=0x1ABC9C
    )
    return embed


# ═══════════════════════════════════════════════
# ЛОГИ ВХОДА/ВЫХОДА С СЕРВЕРА
# ═══════════════════════════════════════════════

MEMBER_LOG_CHANNEL_ID = 1515644806406340708

@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(color=0x2ecc71)
    embed.set_author(name=f"{member.display_name} зашёл на сервер", icon_url=member.display_avatar.url)
    embed.add_field(name="Ник", value=member.display_name, inline=True)
    embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="d"), inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()

    # Сохраняем дату входа в MongoDB
    try:
        db = get_db()
        db["member_joins"].update_one(
            {"user_id": str(member.id)},
            {"$set": {"user_id": str(member.id), "joined_at": discord.utils.utcnow().isoformat()}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] member join log: {e}")

    await channel.send(embed=embed)


@bot.event
async def on_member_remove(member: discord.Member):
    channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(color=0xff4444)
    embed.set_author(name=f"{member.display_name} вышел с сервера", icon_url=member.display_avatar.url)
    embed.add_field(name="Ник", value=member.display_name, inline=True)
    embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="d"), inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)

    # Считаем сколько пробыл
    try:
        db = get_db()
        doc = db["member_joins"].find_one({"user_id": str(member.id)})
        if doc and doc.get("joined_at"):
            from datetime import timezone
            joined_at = discord.utils.parse_time(doc["joined_at"])
            now = discord.utils.utcnow()
            delta = now - joined_at
            days = delta.days
            hours = delta.seconds // 3600
            joined_str = discord.utils.format_dt(joined_at, style="d")
            embed.add_field(name="Зашёл на сервер", value=joined_str, inline=True)
            embed.add_field(name="Пробыл", value=f"{days} дн. {hours} ч.", inline=True)
            db["member_joins"].delete_one({"user_id": str(member.id)})
    except Exception as e:
        print(f"[ERROR] member remove log: {e}")

    embed.timestamp = discord.utils.utcnow()
    await channel.send(embed=embed)


# ═══════════════════════════════════════════════
# КОМАНДЫ
# ═══════════════════════════════════════════════


@bot.tree.command(name="vacation", description="Отправить панель отпусков")
async def vacation(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    channel = interaction.guild.get_channel(VACATION_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("канал не найден", ephemeral=True)
        return

    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    await channel.send(embed=build_vacation_panel_embed(), view=VacationPanelView())
    await interaction.followup.send("панель отпусков отправлена", ephemeral=True)


@bot.tree.command(name="logs", description="Включить или выключить логи")
async def logs_cmd(interaction: discord.Interaction, type: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    allowed = ["voice"]
    if type not in allowed:
        await interaction.followup.send(f"неизвестный тип. доступные: {', '.join(allowed)}", ephemeral=True)
        return

    current = is_log_enabled(type)
    set_log_enabled(type, not current)
    status = "включены ✅" if not current else "выключены ❌"
    await interaction.followup.send(f"логи `{type}` {status}", ephemeral=True)


@bot.tree.command(name="starts", description="Отправить панель личных каналов")
async def starts(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    channel = interaction.guild.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("канал не найден", ephemeral=True)
        return

    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    await channel.send(embed=build_panel_embed(), view=PanelView())
    await interaction.followup.send("панель отправлена", ephemeral=True)


@bot.tree.command(name="tickets", description="Отправить панель заявок в канал tickets")
async def tickets(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    channel = interaction.guild.get_channel(TICKETS_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("канал tickets не найден", ephemeral=True)
        return

    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    await channel.send(embed=build_ticket_panel_embed(), view=TicketPanelView())
    await interaction.followup.send("панель заявок отправлена", ephemeral=True)


@bot.tree.command(name="refresh", description="Сбросить квоту на создание канала")
async def refresh(interaction: discord.Interaction, user: str):
    if not has_refresh_role(interaction.user):
        await interaction.response.send_message("нет прав", ephemeral=True)
        return

    match = re.search(r"\d{17,20}", user)
    if not match:
        await interaction.response.send_message("укажи тег или айди пользователя", ephemeral=True)
        return

    user_id = match.group()
    try:
        collection = get_collection()
        result = collection.delete_one({"user_id": user_id})
    except Exception as e:
        await interaction.response.send_message("ошибка базы данных", ephemeral=True)
        print(f"[ERROR] MongoDB: {e}")
        return

    if result.deleted_count == 0:
        await interaction.response.send_message("он ещё не регал", ephemeral=True)
        return

    await interaction.response.send_message("квота на создание личного канала сброшена", ephemeral=True)



@bot.tree.command(name="reg", description="Создать список участников")
async def reg(interaction: discord.Interaction, title: str, slots: int):
    if not has_reg_admin_role(interaction.user):
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    if slots < 1 or slots > 500:
        await interaction.response.send_message("количество слотов должно быть от 1 до 500", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    data = {
        "title": title,
        "max_slots": slots,
        "main_list": [],
    }

    embed = build_reg_embed(data)
    view = RegView()
    msg = await interaction.channel.send(embed=embed, view=view)

    # Создаём ветку для плюсов
    thread = await msg.create_thread(name="плюсы")

    # Сохраняем в БД
    try:
        col = get_reg_collection()
        col.insert_one({
            "message_id": str(msg.id),
            "channel_id": str(interaction.channel.id),
            "thread_id": str(thread.id),
            **data
        })
    except Exception as e:
        print(f"[ERROR] MongoDB reg: {e}")

    await interaction.followup.send("список создан", ephemeral=True)


@bot.tree.command(name="sync", description="Синхронизировать команды")
async def sync_slash(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(f"синхронизировано {len(synced)} команд", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"ошибка: {e}", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    try:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await ctx.send(f"синхронизировано {len(synced)} команд")
    except Exception as e:
        await ctx.send(f"ошибка: {e}")


@bot.event
async def on_ready():
    bot.add_view(PanelView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketActionsView())
    bot.add_view(RegView())
    bot.loop.create_task(reg_sync_loop())
    bot.add_view(VacationPanelView())
    bot.add_view(VacationApproveView())
    print(f"Бот запущен: {bot.user}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def run_server():
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


Thread(target=run_server, daemon=True).start()
bot.run(os.getenv("TOKEN"))
