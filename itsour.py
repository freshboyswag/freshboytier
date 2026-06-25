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

        guild = interaction.guild
        ticket_data = await self.get_ticket_data(str(interaction.channel.id))
        if not ticket_data:
            await interaction.response.send_message("данные тикета не найдены", ephemeral=True)
            return

        applicant = guild.get_member(int(ticket_data["applicant_id"]))
        if not applicant:
            try:
                applicant = await guild.fetch_member(int(ticket_data["applicant_id"]))
            except Exception:
                pass

        # Лог действия
        log_time = fmt_time()
        log_entry = {"type": "log", "time": log_time, "text": f"[logs][{log_time}] {interaction.user.display_name} нажал кнопку Принять"}
        await ticket_log_append(str(interaction.channel.id), log_entry)

        await interaction.response.send_modal(
            AcceptGameDataModal(
                ticket_data=ticket_data,
                applicant=applicant,
                interaction_channel=interaction.channel
            )
        )

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
MP_LOGS_CHANNEL_ID = 1518245288547192962
MP_RETENTION_DAYS = 14

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

def get_counters_collection():
    return get_db()["counters"]

async def get_next_mp_number() -> int:
    try:
        from pymongo import ReturnDocument
        col = get_counters_collection()
        doc = col.find_one_and_update(
            {"_id": "mp_counter"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return doc["value"]
    except Exception as e:
        print(f"[ERROR] get_next_mp_number: {e}")
        return 0

def build_reg_embed(data: dict) -> discord.Embed:
    max_slots = data["max_slots"]
    main_list = data["main_list"]
    title = data.get("title", "Список")
    mp_number = data.get("mp_number", "?")
    closed = data.get("closed", False)

    if closed:
        embed = discord.Embed(
            title=f"Мероприятие #{mp_number} закрыто",
            color=0xff4444
        )
        return embed

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
            lines.append(f"\u00a0\u00a0{counter}. <@{u['id']}>")
            counter += 1

    description = f"Основной список {len(main_list)}/{max_slots}"
    body = "\n".join(lines) if lines else "пусто"

    embed = discord.Embed(title=f"{title}", description=description, color=0x1ABC9C)
    embed.add_field(name="\u200b", value=body, inline=False)
    return embed


def build_mp_log_embed(data: dict, closed: bool = False) -> discord.Embed:
    mp_number = data.get("mp_number", "?")
    title = data.get("title", "Список")
    max_slots = data.get("max_slots", "?")
    creator_id = data.get("creator_id")
    created_at = data.get("created_at")

    embed = discord.Embed(
        title=f"Мероприятие #{mp_number}",
        color=0xff4444 if closed else 0x2ecc71
    )
    embed.add_field(name="Название", value=title, inline=False)
    embed.add_field(name="Слотов", value=str(max_slots), inline=True)
    if creator_id:
        embed.add_field(name="Создал", value=f"<@{creator_id}>", inline=True)
    if created_at:
        try:
            dt = discord.utils.parse_time(created_at)
            embed.add_field(name="Дата создания", value=discord.utils.format_dt(dt, style="f"), inline=True)
        except Exception:
            pass
    if closed:
        closer_id = data.get("closer_id")
        closed_at = data.get("closed_at")
        if closer_id:
            embed.add_field(name="Закрыл", value=f"<@{closer_id}>", inline=True)
        if closed_at:
            try:
                dt = discord.utils.parse_time(closed_at)
                embed.add_field(name="Дата закрытия", value=discord.utils.format_dt(dt, style="f"), inline=True)
            except Exception:
                pass
    embed.timestamp = discord.utils.utcnow()
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
        view = RegView() if not data.get("closed") else RegView()
        await message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"[ERROR] update reg embed: {e}")

async def log_mp_event(data: dict, text: str):
    """Отправляет строку лога в лог-ветку МП (в канале MP_LOGS_CHANNEL_ID)."""
    try:
        mp_log_thread_id = data.get("mp_log_thread_id")
        if not mp_log_thread_id:
            return
        thread = bot.get_channel(int(mp_log_thread_id))
        if not thread:
            log_channel = bot.get_channel(MP_LOGS_CHANNEL_ID)
            if log_channel:
                thread = await bot.fetch_channel(int(mp_log_thread_id))
        if thread:
            await thread.send(text)
    except Exception as e:
        print(f"[ERROR] log_mp_event: {e}")

def insert_by_tier(main_list: list, new_entry: dict) -> list:
    tier_order_labels = [label for label, _ in TIER_ORDER]
    new_tier = new_entry.get("tier", "No Tier")
    new_tier_idx = tier_order_labels.index(new_tier) if new_tier in tier_order_labels else len(tier_order_labels)

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

    # Логируем сообщения в тикетах (текстовые каналы, не треды)
    if not isinstance(message.channel, discord.Thread) and not message.author.bot:
        await _log_ticket_message(message)
        return

    if not isinstance(message.channel, discord.Thread):
        return
    if message.author.bot:
        return
    if "+" not in message.content:
        return

    data = await get_thread_message_reg(message.channel, 0)
    if not data:
        return
    if data.get("closed"):
        return

    try:
        col = get_reg_collection()
        msg_map = data.get("msg_map", {})
        msg_map[str(message.id)] = str(message.author.id)
        col.update_one({"_id": data["_id"]}, {"$set": {"msg_map": msg_map}})
    except Exception as e:
        print(f"[ERROR] on_message reg: {e}")

    # Лог: откинул плюс
    await log_mp_event(data, f"{message.author.display_name} откинул плюс")


# Логирование сообщений в тикетах
async def _log_ticket_message(message: discord.Message):
    """Записывает сообщение в ticket_log если это канал тикета."""
    try:
        col = get_tickets_collection()
        ticket = col.find_one({"channel_id": str(message.channel.id)})
        if not ticket:
            return
        discord_id = ticket.get("applicant_id")
        if not discord_id:
            return
        log_time = fmt_time(message.created_at)
        entry = {
            "type": "message",
            "message_id": str(message.id),
            "author_id": str(message.author.id),
            "author_nick": message.author.display_name,
            "time": log_time,
            "text": f"[{log_time}]{message.author.display_name}: {message.content}",
            "deleted": False,
        }
        get_members_server_col().update_one(
            {"discord_id": discord_id},
            {"$push": {"ticket_log": entry}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] _log_ticket_message: {e}")


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

    channel = bot.get_channel(payload.channel_id)
    if not isinstance(channel, discord.Thread):
        return

    data = await get_thread_message_reg(channel, 0)
    if not data:
        return
    if data.get("closed"):
        return

    try:
        msg = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    if msg.author.bot or "+" not in msg.content:
        return

    user_id = str(msg.author.id)
    main_list = data.get("main_list", [])
    max_slots = data["max_slots"]

    if any(u["id"] == user_id for u in main_list):
        return
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

    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on reaction add: {e}")

    # Лог: вписал
    await log_mp_event(data, f"{reactor.display_name} вписал {member.display_name}")


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
    if data.get("closed"):
        return

    try:
        msg = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    user_id = str(msg.author.id)
    main_list = data.get("main_list", [])
    new_list = [u for u in main_list if u["id"] != user_id]

    if len(new_list) == len(main_list):
        return

    data["main_list"] = new_list
    await save_reg_data(int(data["message_id"]), {"main_list": new_list})

    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on reaction remove: {e}")

    # Лог: выписал
    await log_mp_event(data, f"{reactor.display_name} выписал {msg.author.display_name}")


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    channel = bot.get_channel(payload.channel_id)
    # Логируем удаление в тикетах (текстовые каналы)
    if not isinstance(channel, discord.Thread):
        await _log_ticket_message_delete(payload)
        return

    data = await get_thread_message_reg(channel, 0)
    if not data:
        return
    if data.get("closed"):
        return

    msg_map = data.get("msg_map", {})
    user_id = msg_map.get(str(payload.message_id))
    if not user_id:
        return

    main_list = data.get("main_list", [])
    was_in_list = any(u["id"] == user_id for u in main_list)
    new_list = [u for u in main_list if u["id"] != user_id]

    msg_map.pop(str(payload.message_id), None)
    data["main_list"] = new_list

    await save_reg_data(int(data["message_id"]), {"main_list": new_list, "msg_map": msg_map})

    try:
        reg_channel = bot.get_channel(int(data["channel_id"]))
        reg_msg = await reg_channel.fetch_message(int(data["message_id"]))
        await update_reg_embed(reg_msg, data)
    except Exception as e:
        print(f"[ERROR] update embed on message delete: {e}")

    # Лог
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    nick = user_id
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            nick = member.display_name

    if was_in_list:
        await log_mp_event(data, f"{nick} убрал плюс и был выписан из списка")
    else:
        await log_mp_event(data, f"{nick} убрал плюс")


@bot.event
async def on_raw_message_delete_ticket(payload: discord.RawMessageDeleteEvent):
    """Отдельный обработчик для логирования удалённых сообщений в тикетах."""
    pass  # Обрабатывается в on_raw_message_delete ниже через общий handler


async def _log_ticket_message_delete(payload: discord.RawMessageDeleteEvent):
    """Помечает удалённое сообщение в ticket_log."""
    try:
        col = get_tickets_collection()
        channel_id = str(payload.channel_id)
        ticket = col.find_one({"channel_id": channel_id})
        if not ticket:
            return
        discord_id = ticket.get("applicant_id")
        if not discord_id:
            return

        # Пробуем узнать кто удалил через аудит лог
        guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
        deleter = None
        if guild:
            import asyncio
            await asyncio.sleep(0.5)
            try:
                async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.message_delete):
                    if (discord.utils.utcnow() - entry.created_at).total_seconds() < 5:
                        deleter = entry.user.display_name
                        break
            except Exception:
                pass

        log_time = fmt_time()
        if deleter:
            deleted_text = f"[{log_time}][удалено модером: {deleter}]"
        else:
            deleted_text = f"[{log_time}][удалено автором]"

        get_members_server_col().update_one(
            {"discord_id": discord_id, "ticket_log.message_id": str(payload.message_id)},
            {"$set": {
                "ticket_log.$.deleted": True,
                "ticket_log.$.deleted_text": deleted_text,
            }}
        )
    except Exception as e:
        print(f"[ERROR] _log_ticket_message_delete: {e}")


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

        # Короткий лог с тегами в ветку "плюсы"
        try:
            reg_channel = interaction.guild.get_channel(int(self.reg_data.get("channel_id", 0)))
            if reg_channel:
                reg_msg = await reg_channel.fetch_message(self.reg_message_id)
                if reg_msg and reg_msg.thread:
                    absent_mentions = " ".join(f"<@{u['id']}>" for u in not_in_voice)
                    absent_line = f"Отсутствуют: {absent_mentions}" if absent_mentions else "Все присутствуют"
                    short_log_text = (
                        f"{interaction.user.display_name} провёл проверку по войсу {voice_channel.name}"
                        + chr(10) + absent_line
                    )
                    await reg_msg.thread.send(short_log_text)
        except Exception as e:
            print(f"[ERROR] voice check log: {e}")

        # Полный лог без тегов в лог-ветку МП
        full_log_lines = []
        full_log_lines.append(f"**{interaction.user.display_name} провёл проверку по войсу: {voice_channel.name}**")
        full_log_lines.append("")
        full_log_lines.append(f"**Присутствуют ({len(in_voice)}/{len(main_list)}):**")
        if in_voice:
            for u in in_voice:
                full_log_lines.append("✅ " + u["nick"])
        else:
            full_log_lines.append("никого")
        full_log_lines.append("")
        full_log_lines.append(f"**Отсутствуют ({len(not_in_voice)}/{len(main_list)}):**")
        if not_in_voice:
            for u in not_in_voice:
                full_log_lines.append("❌ " + u["nick"])
        else:
            full_log_lines.append("все на месте")
        full_log_text = "\n".join(full_log_lines)

        await log_mp_event(self.reg_data, full_log_text)

        absent_nicks = ", ".join(u["nick"] for u in not_in_voice)
        if absent_nicks:
            await log_mp_event(self.reg_data, f"Отсутствуют и тегнуты в канал: {absent_nicks}")


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

        closed = data.get("closed", False)

        if closed:
            options = [
                discord.SelectOption(label="ℹ️ Инфо об МП", value="info"),
            ]
        else:
            options = [
                discord.SelectOption(label="📋 Сформировать список", value="form"),
                discord.SelectOption(label="📣 Тегнуть список", value="tag"),
                discord.SelectOption(label="🎤 Проверка по войсу", value="voice"),
                discord.SelectOption(label="ℹ️ Инфо об МП", value="info"),
                discord.SelectOption(label="🔒 Закрыть МП", value="close"),
            ]
        super().__init__(placeholder="выберите действие...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.values[0] == "form":
            thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
            if not thread:
                await interaction.followup.send("ветка не найдена", ephemeral=True)
                return

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

            # Лог в ветку с логами МП — сформировал список
            list_lines = [f"{interaction.user.display_name} сформировал список из {len(valid_users)} человек:"]
            for u in valid_users:
                list_lines.append(u["nick"])
            await log_mp_event(self.data, "\n".join(list_lines))

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

        elif self.values[0] == "info":
            mp_number = self.data.get("mp_number", "?")
            creator_id = self.data.get("creator_id")
            created_at = self.data.get("created_at")
            thread_link = self.data.get("mp_log_thread_link", "не найдена")

            lines = [f"**Мероприятие #{mp_number}**"]
            if creator_id:
                lines.append(f"Создал: <@{creator_id}>")
            if created_at:
                try:
                    dt = discord.utils.parse_time(created_at)
                    lines.append(f"Создано: {discord.utils.format_dt(dt, style='f')}")
                except Exception:
                    pass
            lines.append(f"Ветка с логами: {thread_link}")

            if self.data.get("closed"):
                closer_id = self.data.get("closer_id")
                closed_at = self.data.get("closed_at")
                if closer_id:
                    lines.append(f"Закрыл: <@{closer_id}>")
                if closed_at:
                    try:
                        dt = discord.utils.parse_time(closed_at)
                        lines.append(f"Закрыто: {discord.utils.format_dt(dt, style='f')}")
                    except Exception:
                        pass

            await interaction.followup.send("\n".join(lines), ephemeral=True)

        elif self.values[0] == "close":
            if self.data.get("closed"):
                await interaction.followup.send("МП уже закрыто", ephemeral=True)
                return

            closed_at_iso = discord.utils.utcnow().isoformat()
            self.data["closed"] = True
            self.data["closer_id"] = str(interaction.user.id)
            self.data["closed_at"] = closed_at_iso

            await save_reg_data(int(self.reg_message.id), {
                "closed": True,
                "closer_id": str(interaction.user.id),
                "closed_at": closed_at_iso
            })

            await update_reg_embed(self.reg_message, self.data)

            # Закрываем ветку "плюсы"
            try:
                thread = self.reg_message.thread if hasattr(self.reg_message, "thread") and self.reg_message.thread else None
                if thread:
                    await thread.send(f"{interaction.user.mention} завершил мероприятие")
                    await thread.edit(locked=True, archived=True)
            except Exception as e:
                print(f"[ERROR] close plus thread: {e}")

            # Лог-ветка МП — итоговый список перед закрытием
            main_list = self.data.get("main_list", [])
            final_lines = [f"Итоговый список перед закрытием ({len(main_list)} человек):"]
            for u in main_list:
                final_lines.append(u["nick"])
            await log_mp_event(self.data, "\n".join(final_lines))

            await log_mp_event(self.data, f"Мероприятие закрыто {interaction.user.display_name}")
            try:
                mp_log_thread_id = self.data.get("mp_log_thread_id")
                if mp_log_thread_id:
                    log_thread = bot.get_channel(int(mp_log_thread_id))
                    if log_thread:
                        await log_thread.edit(locked=True, archived=True)
            except Exception as e:
                print(f"[ERROR] close mp log thread: {e}")

            # Обновляем эмбед в канале логов МП
            try:
                mp_log_msg_id = self.data.get("mp_log_message_id")
                if mp_log_msg_id:
                    log_channel = bot.get_channel(MP_LOGS_CHANNEL_ID)
                    if log_channel:
                        log_msg = await log_channel.fetch_message(int(mp_log_msg_id))
                        await log_msg.edit(embed=build_mp_log_embed(self.data, closed=True))
            except Exception as e:
                print(f"[ERROR] update mp log embed on close: {e}")

            await interaction.followup.send(f"Мероприятие #{self.data.get('mp_number')} закрыто", ephemeral=True)


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


# ───────────────────────────────────────────────
# АВТОУДАЛЕНИЕ ЗАКРЫТЫХ МП ЧЕРЕЗ 14 ДНЕЙ
# ───────────────────────────────────────────────

async def mp_cleanup_loop():
    import asyncio
    from datetime import timedelta
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            col = get_reg_collection()
            now = discord.utils.utcnow()
            cutoff = now - timedelta(days=MP_RETENTION_DAYS)
            closed_regs = list(col.find({"closed": True}))
            for reg in closed_regs:
                closed_at = reg.get("closed_at")
                if not closed_at:
                    continue
                try:
                    closed_dt = discord.utils.parse_time(closed_at)
                except Exception:
                    continue
                if closed_dt < cutoff:
                    col.delete_one({"_id": reg["_id"]})
                    print(f"[INFO] Удалено старое МП #{reg.get('mp_number')}")
        except Exception as e:
            print(f"[ERROR] mp_cleanup_loop: {e}")
        await asyncio.sleep(3600)  # раз в час проверяем



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
    if not is_log_enabled("joins"):
        return
    channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(color=0x2ecc71)
    embed.set_author(name=f"{member.display_name} зашёл на сервер", icon_url=member.display_avatar.url)
    embed.add_field(name="Ник", value=member.display_name, inline=True)
    embed.add_field(name="Упоминание", value=member.mention, inline=True)
    embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.add_field(name="Зашёл", value=discord.utils.format_dt(discord.utils.utcnow(), style="f"), inline=True)
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
    if not is_log_enabled("joins"):
        return
    channel = member.guild.get_channel(MEMBER_LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(color=0xff4444)
    embed.set_author(name=f"{member.display_name} вышел с сервера", icon_url=member.display_avatar.url)
    embed.add_field(name="Ник", value=member.display_name, inline=True)
    embed.add_field(name="Упоминание", value=member.mention, inline=True)
    embed.add_field(name="Юзернейм", value=f"@{member.name}", inline=True)
    embed.add_field(name="ID", value=str(member.id), inline=True)
    embed.add_field(name="Аккаунт создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)

    # Считаем сколько пробыл
    try:
        db = get_db()
        doc = db["member_joins"].find_one({"user_id": str(member.id)})
        if doc and doc.get("joined_at"):
            joined_at = discord.utils.parse_time(doc["joined_at"])
            now = discord.utils.utcnow()
            delta = now - joined_at
            days = delta.days
            hours = delta.seconds // 3600
            joined_str = discord.utils.format_dt(joined_at, style="f")
            embed.add_field(name="Зашёл на сервер", value=joined_str, inline=True)
            embed.add_field(name="Пробыл", value=f"{days} дн. {hours} ч.", inline=True)
            db["member_joins"].delete_one({"user_id": str(member.id)})
    except Exception as e:
        print(f"[ERROR] member remove log: {e}")

    embed.timestamp = discord.utils.utcnow()
    await channel.send(embed=embed)



# ═══════════════════════════════════════════════
# СИСТЕМА УЧЁТА УЧАСТНИКОВ
# ═══════════════════════════════════════════════

DATABASE_CHANNEL_ID = 1519121489486544937

# Роли с доступом к просмотру БД
DB_VIEW_ROLES  = {1510604555040198816, 1510601395999346819, 1510610391267545138, 1510610350532329642, 1510614323754434670}
# Роли с доступом к редактированию БД
DB_EDIT_ROLES  = {1510604555040198816, 1510601395999346819}

# Роли которые можно выдать при принятии
MEMBER_ROLES = {
    "Tier S":    1510603296321306774,
    "Tier A":    1510603326331293846,
    "Tier B":    1510603348276023547,
    "Antisocial":1510600886563504148,
    "Test":      1510603939664629891,
    "Qual":      1510604916207390740,
}

import io
import json
from datetime import timezone, timedelta

UTC3 = timezone(timedelta(hours=3))

def has_db_view_role(member):
    return any(r.id in DB_VIEW_ROLES for r in member.roles)

def has_db_edit_role(member):
    return any(r.id in DB_EDIT_ROLES for r in member.roles)

def get_members_game_col():
    return get_db()["members_game"]

def get_members_server_col():
    return get_db()["members_server"]

def get_members_uid_counter():
    try:
        from pymongo import ReturnDocument
        col = get_counters_collection()
        doc = col.find_one_and_update(
            {"_id": "members_uid_counter"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return doc["value"]
    except Exception as e:
        print(f"[ERROR] members uid counter: {e}")
        return 0

def fmt_time(dt=None) -> str:
    """Форматирует время в UTC+3 как 24.06.2026 15:12"""
    if dt is None:
        dt = discord.utils.utcnow()
    local = dt.astimezone(UTC3)
    return local.strftime("%d.%m.%Y %H:%M")

def format_fullname(raw: str) -> str:
    """Конвертирует имя_фамилия -> Имя Фамилия"""
    if raw == "-":
        return "-"
    return raw.replace("_", " ").title()

def build_database_embed() -> discord.Embed:
    embed = discord.Embed(title="База данных участников", color=0x1ABC9C)
    embed.description = "Используй кнопки ниже для управления базой данных"
    embed.timestamp = discord.utils.utcnow()
    return embed


# ───────────────────────────────────────────────
# ПАГИНАЦИЯ УЧЁТА
# ───────────────────────────────────────────────

class MembersListView(View):
    def __init__(self, records: list, page: int = 0, per_page: int = 20):
        super().__init__(timeout=120)
        self.records = records
        self.page = page
        self.per_page = per_page
        self._update_buttons()

    def _update_buttons(self):
        total_pages = max(1, (len(self.records) + self.per_page - 1) // self.per_page)
        for item in self.children:
            if hasattr(item, 'custom_id'):
                if item.custom_id == 'ml_prev':
                    item.disabled = self.page == 0
                elif item.custom_id == 'ml_next':
                    item.disabled = self.page >= total_pages - 1

    def get_page_embed(self) -> discord.Embed:
        total = len(self.records)
        total_pages = max(1, (total + self.per_page - 1) // self.per_page)
        start = self.page * self.per_page
        end = min(start + self.per_page, total)
        page_records = self.records[start:end]

        lines = []
        for r in page_records:
            uid = r.get("uid", "?")
            username = r.get("discord_username", "-")
            dsid = r.get("discord_id", "-")
            full_name = r.get("full_name", "-")
            static = r.get("static", "-")
            lines.append(f"`#{uid}` @{username} ({dsid}) {full_name} {static}")

        embed = discord.Embed(
            title=f"Учёт участников",
            description="\n".join(lines) if lines else "пусто",
            color=0x1ABC9C
        )
        embed.set_footer(text=f"Страница {self.page + 1}/{total_pages} | {end - start} из {total}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.grey, custom_id="ml_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.grey, custom_id="ml_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, (len(self.records) + self.per_page - 1) // self.per_page)
        self.page = min(total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    @discord.ui.button(label="20/стр", style=discord.ButtonStyle.blurple, custom_id="ml_per_page")
    async def change_per_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Переключаем между 20 и 100
        self.per_page = 100 if self.per_page == 20 else 20
        button.label = f"{self.per_page}/стр"
        self.page = 0
        self._update_buttons()
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)


# ───────────────────────────────────────────────
# ПОИСК УЧАСТНИКА
# ───────────────────────────────────────────────

class MemberSearchModal(Modal, title="Поиск участника"):
    query = TextInput(
        label="uid, Discord ID, имя_фамилия или статик",
        placeholder="например: 42 или 186404 или harukaze_antisocial",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        q = self.query.value.strip()
        col = get_members_game_col()

        doc = None
        # Попробуем uid
        if q.isdigit() and len(q) < 10:
            doc = col.find_one({"uid": int(q)})
        # Discord ID (длинное число)
        if not doc and q.isdigit():
            doc = col.find_one({"discord_id": q})
        # full_name
        if not doc:
            doc = col.find_one({"full_name": q})
        # static
        if not doc:
            doc = col.find_one({"static": q})
        # discord_username
        if not doc:
            doc = col.find_one({"discord_username": q})

        if not doc:
            await interaction.followup.send("участник не найден", ephemeral=True)
            return

        guild = interaction.guild
        discord_id = doc.get("discord_id")
        member = guild.get_member(int(discord_id)) if discord_id and discord_id != "-" else None

        embed = discord.Embed(title=f"Участник #{doc.get('uid', '?')}", color=0x1ABC9C)
        embed.add_field(name="Discord", value=f"<@{discord_id}> (@{doc.get('discord_username', '-')})" if discord_id and discord_id != "-" else "-", inline=False)
        embed.add_field(name="Discord ID", value=discord_id or "-", inline=True)
        embed.add_field(name="Имя Фамилия", value=format_fullname(doc.get("full_name", "-")), inline=True)
        embed.add_field(name="Статик", value=doc.get("static", "-"), inline=True)
        embed.add_field(name="Заметка", value=doc.get("note", "-"), inline=False)

        if member:
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="На сервере с", value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "-", inline=True)
            embed.add_field(name="Акк создан", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)

        # Серверные данные
        srv = get_members_server_col().find_one({"discord_id": discord_id})
        if srv:
            if srv.get("accepted_by"):
                embed.add_field(name="Принял", value=f"<@{srv['accepted_by']}>", inline=True)
            if srv.get("accepted_at"):
                embed.add_field(name="Дата принятия", value=srv["accepted_at"], inline=True)
            if srv.get("roles_given"):
                roles_str = ", ".join(srv["roles_given"])
                embed.add_field(name="Роли при принятии", value=roles_str, inline=False)

        view = MemberEditView(doc=doc, discord_id=discord_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ───────────────────────────────────────────────
# РЕДАКТИРОВАНИЕ ЗАПИСИ
# ───────────────────────────────────────────────

class MemberEditView(View):
    def __init__(self, doc: dict, discord_id: str):
        super().__init__(timeout=120)
        self.doc = doc
        self.discord_id = discord_id

    @discord.ui.button(label="✏️ Изменить данные", style=discord.ButtonStyle.blurple, custom_id="member_edit")
    async def edit_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return

        options = [
            discord.SelectOption(label="Discord Username", value="discord_username"),
            discord.SelectOption(label="Discord ID", value="discord_id"),
            discord.SelectOption(label="Имя Фамилия", value="full_name"),
            discord.SelectOption(label="Статик", value="static"),
            discord.SelectOption(label="Заметка", value="note"),
            discord.SelectOption(label="Все данные сразу", value="all"),
        ]
        select = discord.ui.Select(placeholder="Что изменить?", options=options)

        async def select_callback(select_interaction: discord.Interaction):
            field = select.values[0]
            if field == "all":
                await select_interaction.response.send_modal(MemberEditAllModal(doc=self.doc))
            else:
                await select_interaction.response.send_modal(MemberEditFieldModal(doc=self.doc, field=field))

        select.callback = select_callback
        view = View(timeout=60)
        view.add_item(select)
        await interaction.response.send_message("Выбери что изменить:", view=view, ephemeral=True)


class MemberEditFieldModal(Modal):
    def __init__(self, doc: dict, field: str):
        labels = {
            "discord_username": "Discord Username",
            "discord_id": "Discord ID",
            "full_name": "Имя Фамилия (имя_фамилия)",
            "static": "Статик",
            "note": "Заметка",
        }
        super().__init__(title=f"Изменить: {labels.get(field, field)}")
        self.doc = doc
        self.field = field
        self.value_input = TextInput(
            label=labels.get(field, field),
            default=str(doc.get(field, "")),
            style=discord.TextStyle.short,
            min_length=1,
            max_length=200
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        col = get_members_game_col()
        col.update_one({"uid": self.doc["uid"]}, {"$set": {self.field: self.value_input.value.strip()}})
        await interaction.followup.send(f"✅ `{self.field}` обновлено", ephemeral=True)


class MemberEditAllModal(Modal, title="Изменить все данные"):
    def __init__(self, doc: dict):
        super().__init__()
        self.doc = doc

    discord_username = TextInput(label="Discord Username", style=discord.TextStyle.short, max_length=100)
    discord_id_field = TextInput(label="Discord ID", style=discord.TextStyle.short, max_length=20)
    full_name = TextInput(label="Имя Фамилия (имя_фамилия)", style=discord.TextStyle.short, max_length=100)
    static = TextInput(label="Статик", style=discord.TextStyle.short, max_length=20)
    note = TextInput(label="Заметка", style=discord.TextStyle.short, max_length=300, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        col = get_members_game_col()
        col.update_one({"uid": self.doc["uid"]}, {"$set": {
            "discord_username": self.discord_username.value.strip(),
            "discord_id": self.discord_id_field.value.strip(),
            "full_name": self.full_name.value.strip(),
            "static": self.static.value.strip(),
            "note": self.note.value.strip() if self.note.value else "-",
        }})
        await interaction.followup.send("✅ Все данные обновлены", ephemeral=True)


# ───────────────────────────────────────────────
# СОЗДАНИЕ ЗАПИСИ ВРУЧНУЮ
# ───────────────────────────────────────────────

class MemberCreateModal(Modal, title="Создать запись участника"):
    discord_username = TextInput(label="Discord Username", style=discord.TextStyle.short, max_length=100)
    discord_id_field = TextInput(label="Discord ID", style=discord.TextStyle.short, max_length=20)
    full_name = TextInput(label="Имя Фамилия (имя_фамилия)", placeholder="имя_фамилия или -", style=discord.TextStyle.short, max_length=100)
    static = TextInput(label="Статик", placeholder="номер или -", style=discord.TextStyle.short, max_length=20)
    note = TextInput(label="Заметка", style=discord.TextStyle.short, max_length=300, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        discord_id = self.discord_id_field.value.strip()
        col = get_members_game_col()

        # Проверяем не существует ли уже
        existing = col.find_one({"discord_id": discord_id})
        if existing:
            await interaction.followup.send(f"запись с Discord ID `{discord_id}` уже существует (uid #{existing['uid']})", ephemeral=True)
            return

        uid = get_members_uid_counter()
        guild = interaction.guild
        member = guild.get_member(int(discord_id)) if discord_id.isdigit() else None

        doc = {
            "uid": uid,
            "discord_username": self.discord_username.value.strip(),
            "discord_id": discord_id,
            "full_name": self.full_name.value.strip() or "-",
            "static": self.static.value.strip() or "-",
            "note": self.note.value.strip() if self.note.value else "-",
        }
        col.insert_one(doc)

        # Создаём серверную запись
        srv_doc = {
            "discord_id": discord_id,
            "added_manually": True,
            "added_by": str(interaction.user.id),
            "added_at": fmt_time(),
            "ticket_log": [],
            "roles_given": [],
        }
        if member:
            srv_doc["joined_server_at"] = member.joined_at.isoformat() if member.joined_at else None
            srv_doc["account_created_at"] = member.created_at.isoformat()
        get_members_server_col().insert_one(srv_doc)

        await interaction.followup.send(f"✅ Запись создана: uid #{uid}", ephemeral=True)


# ───────────────────────────────────────────────
# ГЛАВНАЯ ПАНЕЛЬ КНОПОК БАЗЫ ДАННЫХ
# ───────────────────────────────────────────────

class DatabasePanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Учёт участников", style=discord.ButtonStyle.blurple, custom_id="db_list")
    async def db_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        col = get_members_game_col()
        records = list(col.find({}, {"_id": 0}).sort("uid", 1))
        view = MembersListView(records=records)
        await interaction.followup.send(embed=view.get_page_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="🔍 Инфо по участнику", style=discord.ButtonStyle.grey, custom_id="db_search")
    async def db_search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_modal(MemberSearchModal())

    @discord.ui.button(label="➕ Создать запись", style=discord.ButtonStyle.green, custom_id="db_create")
    async def db_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.send_modal(MemberCreateModal())

    @discord.ui.button(label="🔄 Синхронизировать", style=discord.ButtonStyle.grey, custom_id="db_sync")
    async def db_sync(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_edit_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        col_game = get_members_game_col()
        col_srv = get_members_server_col()
        records = list(col_game.find({}))
        updated = 0

        for rec in records:
            discord_id = rec.get("discord_id")
            if not discord_id or discord_id == "-":
                continue
            try:
                member = guild.get_member(int(discord_id))
                if not member:
                    member = await guild.fetch_member(int(discord_id))
                if member:
                    col_game.update_one({"_id": rec["_id"]}, {"$set": {
                        "discord_username": member.name,
                    }})
                    col_srv.update_one({"discord_id": discord_id}, {"$set": {
                        "joined_server_at": member.joined_at.isoformat() if member.joined_at else None,
                        "account_created_at": member.created_at.isoformat(),
                    }}, upsert=True)
                    updated += 1
            except Exception:
                pass

        await interaction.followup.send(f"✅ Синхронизировано: {updated} из {len(records)}", ephemeral=True)

    @discord.ui.button(label="📥 Скачать БД", style=discord.ButtonStyle.grey, custom_id="db_download")
    async def db_download(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_db_view_role(interaction.user):
            await interaction.response.send_message("нет прав", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        col_game = get_members_game_col()
        col_srv = get_members_server_col()

        game_records = list(col_game.find({}, {"_id": 0}))
        srv_records = list(col_srv.find({}, {"_id": 0}))

        game_json = json.dumps(game_records, ensure_ascii=False, indent=2)
        srv_json = json.dumps(srv_records, ensure_ascii=False, indent=2)

        game_file = discord.File(io.BytesIO(game_json.encode("utf-8")), filename="members_game.json")
        srv_file = discord.File(io.BytesIO(srv_json.encode("utf-8")), filename="members_server.json")

        try:
            await interaction.user.send(files=[game_file, srv_file])
            await interaction.followup.send("✅ Файлы отправлены в лс", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Не удалось отправить в лс — открой личные сообщения", ephemeral=True)


# ───────────────────────────────────────────────
# ЛОГИРОВАНИЕ ПЕРЕПИСКИ В ТИКЕТАХ
# ───────────────────────────────────────────────

async def ticket_log_append(channel_id: str, entry: dict):
    """Добавляет запись в ticket_log в members_server по channel_id тикета."""
    try:
        col = get_tickets_collection()
        ticket = col.find_one({"channel_id": channel_id})
        if not ticket:
            return
        discord_id = ticket.get("applicant_id")
        if not discord_id:
            return
        get_members_server_col().update_one(
            {"discord_id": discord_id},
            {"$push": {"ticket_log": entry}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] ticket_log_append: {e}")


# ───────────────────────────────────────────────
# ПРИНЯТИЕ С МОДАЛКОЙ И ВЫБОРОМ РОЛЕЙ
# ───────────────────────────────────────────────

class AcceptGameDataModal(Modal, title="Игровые данные участника"):
    full_name = TextInput(
        label="Имя Фамилия (имя_фамилия)",
        placeholder="harukaze_antisocial",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=100
    )
    static = TextInput(
        label="Статик",
        placeholder="186404",
        style=discord.TextStyle.short,
        min_length=1,
        max_length=20
    )
    note = TextInput(
        label="Заметка",
        placeholder="необязательно",
        style=discord.TextStyle.short,
        required=False,
        max_length=300
    )

    def __init__(self, ticket_data: dict, applicant: discord.Member, interaction_channel):
        super().__init__()
        self.ticket_data = ticket_data
        self.applicant = applicant
        self.interaction_channel = interaction_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Показываем select menu для выбора ролей
        options = [discord.SelectOption(label=name, value=str(role_id)) for name, role_id in MEMBER_ROLES.items()]
        select = discord.ui.Select(
            placeholder="Выбери роли для выдачи...",
            options=options,
            min_values=1,
            max_values=len(options)
        )

        game_data = {
            "full_name": self.full_name.value.strip(),
            "static": self.static.value.strip(),
            "note": self.note.value.strip() if self.note.value else "-",
        }

        ticket_data = self.ticket_data
        applicant = self.applicant
        ticket_channel = self.interaction_channel

        async def select_callback(select_interaction: discord.Interaction):
            await select_interaction.response.defer(ephemeral=True)

            guild = select_interaction.guild
            selected_role_ids = [int(v) for v in select.values]
            selected_role_names = [name for name, rid in MEMBER_ROLES.items() if rid in selected_role_ids]

            # Выдаём роли
            roles_given = []
            for role_id in selected_role_ids:
                role = guild.get_role(role_id)
                if role and applicant:
                    try:
                        await applicant.add_roles(role, reason="принятие в семью")
                        roles_given.append(role.name)
                    except Exception as e:
                        print(f"[ERROR] выдача роли {role_id}: {e}")

            # Лог действия
            log_time = fmt_time()
            log_entry = f"[logs][{log_time}] {select_interaction.user.display_name} выдал роли: {', '.join(roles_given)} и заполнил игровые данные (имя: {game_data['full_name']}, статик: {game_data['static']}, заметка: {game_data['note']})"
            await ticket_log_append(str(ticket_channel.id), {"type": "log", "time": log_time, "text": log_entry})

            # Сохраняем в members_game
            discord_id = str(applicant.id) if applicant else ticket_data.get("applicant_id")
            col_game = get_members_game_col()
            existing = col_game.find_one({"discord_id": discord_id})
            if existing:
                col_game.update_one({"discord_id": discord_id}, {"$set": {
                    "full_name": game_data["full_name"],
                    "static": game_data["static"],
                    "discord_username": applicant.name if applicant else existing.get("discord_username", "-"),
                }})
            else:
                uid = get_members_uid_counter()
                col_game.insert_one({
                    "uid": uid,
                    "discord_username": applicant.name if applicant else "-",
                    "discord_id": discord_id,
                    "full_name": game_data["full_name"],
                    "static": game_data["static"],
                    "note": game_data["note"],
                })

            # Сохраняем в members_server
            get_members_server_col().update_one(
                {"discord_id": discord_id},
                {"$set": {
                    "accepted_by": str(select_interaction.user.id),
                    "accepted_at": log_time,
                    "roles_given": roles_given,
                    "ticket_fields": ticket_data.get("fields", {}),
                    "joined_server_at": applicant.joined_at.isoformat() if applicant and applicant.joined_at else None,
                    "account_created_at": applicant.created_at.isoformat() if applicant else None,
                }},
                upsert=True
            )

            # Финальный лог принятия
            accept_log = f"[logs][{fmt_time()}] {select_interaction.user.display_name} принял заявку участника и завершил её рассмотрение"
            await ticket_log_append(str(ticket_channel.id), {"type": "log", "time": fmt_time(), "text": accept_log})

            # Стандартная логика завершения тикета
            guild2 = select_interaction.guild
            results_channel = guild2.get_channel(RESULTS_CHANNEL_ID)
            logs_channel = guild2.get_channel(LOGS_ACCEPTED_ID)

            results_msg_id = ticket_data.get("results_msg_id")
            if results_msg_id and results_channel:
                try:
                    msg = await results_channel.fetch_message(int(results_msg_id))
                    await msg.delete()
                except Exception:
                    pass

            embed = discord.Embed(color=0x2ecc71)
            embed.description = (
                f"Заявка пользователя {applicant.mention if applicant else ticket_data['applicant_name']}\n\n"
                f"На вступление в семью была принята! 🎉\n"
                f"Рассматривал заявку: {select_interaction.user.mention}"
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
                log_embed.add_field(name="Выданные роли", value=", ".join(roles_given) if roles_given else "-", inline=False)
                log_embed.add_field(name="Принял", value=select_interaction.user.mention, inline=False)
                log_embed.timestamp = discord.utils.utcnow()
                await logs_channel.send(embed=log_embed)

            try:
                col = get_tickets_collection()
                col.delete_one({"channel_id": str(ticket_channel.id)})
            except Exception as e:
                print(f"[ERROR] MongoDB: {e}")

            await ticket_channel.delete()
            await select_interaction.followup.send("✅ готово", ephemeral=True)

        select.callback = select_callback
        view = View(timeout=120)
        view.add_item(select)
        await interaction.followup.send("Выбери роли для выдачи:", view=view, ephemeral=True)



# ═══════════════════════════════════════════════
# КОМАНДЫ
# ═══════════════════════════════════════════════


@bot.tree.command(name="logs", description="Включить или выключить логи")
async def logs_cmd(interaction: discord.Interaction, type: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    allowed = ["voice", "joins"]
    if type not in allowed:
        await interaction.followup.send(f"неизвестный тип. доступные: {', '.join(allowed)}", ephemeral=True)
        return

    current = is_log_enabled(type)
    set_log_enabled(type, not current)
    status = "включены ✅" if not current else "выключены ❌"
    await interaction.followup.send(f"логи `{type}` {status}", ephemeral=True)


@bot.tree.command(name="panels", description="Отправить панель в нужный канал")
async def panels(interaction: discord.Interaction, panel: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return

    allowed = ["tickets", "otpusk", "private", "database", "all"]
    if panel not in allowed:
        await interaction.response.send_message(f"неизвестная панель. доступные: {', '.join(allowed)}", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    sent = []

    async def send_panel(channel_id, embed_fn, view_fn):
        ch = interaction.guild.get_channel(channel_id)
        if not ch:
            return False
        async for message in ch.history(limit=100):
            if message.author == bot.user:
                await message.delete()
        await ch.send(embed=embed_fn(), view=view_fn())
        return True

    if panel in ("private", "all"):
        ok = await send_panel(PANEL_CHANNEL_ID, build_panel_embed, PanelView)
        if ok:
            sent.append("private")

    if panel in ("tickets", "all"):
        ok = await send_panel(TICKETS_CHANNEL_ID, build_ticket_panel_embed, TicketPanelView)
        if ok:
            sent.append("tickets")

    if panel in ("otpusk", "all"):
        ok = await send_panel(VACATION_CHANNEL_ID, build_vacation_panel_embed, VacationPanelView)
        if ok:
            sent.append("otpusk")

    if panel in ("database", "all"):
        ok = await send_panel(DATABASE_CHANNEL_ID, build_database_embed, DatabasePanelView)
        if ok:
            sent.append("database")

    await interaction.followup.send(f"отправлено: {', '.join(sent) if sent else 'ничего'}", ephemeral=True)


@bot.tree.command(name="write", description="Одноразовая заливка начальной базы участников")
async def write_base(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("нет прав", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    import json as _json
    records = [
      {"uid": 1, "discord_username": "kurkk1", "discord_id": "852497017800097792", "full_name": "kuroki_antisocial", "static": "185686", "note": "-"},
      {"uid": 2, "discord_username": "s1lzorzzz", "discord_id": "588037655305125918", "full_name": "-", "static": "-", "note": "не знаю"},
      {"uid": 3, "discord_username": "femtochkaumer", "discord_id": "678225170682609685", "full_name": "graffuckla_antiscl", "static": "133371", "note": "-"},
      {"uid": 4, "discord_username": "qwkrill", "discord_id": "571253851580268554", "full_name": "-", "static": "-", "note": "не знаю"},
      {"uid": 5, "discord_username": "yumekomidari", "discord_id": "1379540005176872980", "full_name": "milka_antisocial", "static": "4599", "note": "-"},
      {"uid": 6, "discord_username": "ded3862", "discord_id": "739781664661700689", "full_name": "sodju_antisocial", "static": "48745", "note": "-"},
      {"uid": 7, "discord_username": "daic1k", "discord_id": "872451801302040617", "full_name": "aegis_antisocial", "static": "17266", "note": "-"},
      {"uid": 8, "discord_username": "akumara000_1", "discord_id": "1503779885137530981", "full_name": "akumara_antisocial", "static": "186001", "note": "-"},
      {"uid": 9, "discord_username": "alhimik.", "discord_id": "710335782140903435", "full_name": "-", "static": "-", "note": "не хочет играть"},
      {"uid": 10, "discord_username": "apokemons", "discord_id": "948539754478174228", "full_name": "youto_antiscl", "static": "167237", "note": "-"},
      {"uid": 11, "discord_username": "arsik_716", "discord_id": "771828343011409930", "full_name": "-", "static": "-", "note": "не хочет играть"},
      {"uid": 12, "discord_username": "idnhf", "discord_id": "740412410866696302", "full_name": "ataract_antisocial", "static": "138587", "note": "-"},
      {"uid": 13, "discord_username": "bat9boom", "discord_id": "1093953460371738837", "full_name": "-", "static": "-", "note": "не живой"},
      {"uid": 14, "discord_username": "q_tu", "discord_id": "593760280748621836", "full_name": "black_antisocial", "static": "38596", "note": "-"},
      {"uid": 15, "discord_username": "buivolcore", "discord_id": "564711604617347073", "full_name": "-", "static": "-", "note": "чисто плюсом"},
      {"uid": 16, "discord_username": "shinka100", "discord_id": "1140539622997372948", "full_name": "codeine_antiscl", "static": "125650", "note": "-"},
      {"uid": 17, "discord_username": "danielo909", "discord_id": "580019096683413514", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 18, "discord_username": "dezolatorgucci", "discord_id": "872422901331152897", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 19, "discord_username": "bybitownerz", "discord_id": "1266270138387402814", "full_name": "-", "static": "-", "note": "друг принка хз что с ним"},
      {"uid": 20, "discord_username": "umbra4xx", "discord_id": "886322342433812540", "full_name": "dollar_currency", "static": "17606", "note": "-"},
      {"uid": 21, "discord_username": "nxkturn3", "discord_id": "1081467052377784392", "full_name": "dreams_hunter", "static": "99847", "note": "-"},
      {"uid": 22, "discord_username": ".comkaa", "discord_id": "988114981872631888", "full_name": "jabeb_winline", "static": "1440", "note": "-"},
      {"uid": 23, "discord_username": "sundulb", "discord_id": "331065359626928128", "full_name": "geshka_vex", "static": "22093", "note": "-"},
      {"uid": 24, "discord_username": "bandit_of_venice", "discord_id": "295931615375523840", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 25, "discord_username": "sellamnp", "discord_id": "1007535281924227193", "full_name": "harukaze_antisocial", "static": "186404", "note": "-"},
      {"uid": 26, "discord_username": "vccxcc", "discord_id": "535039074193506304", "full_name": "-", "static": "-", "note": "не знаю"},
      {"uid": 27, "discord_username": "trybles", "discord_id": "374213478455443458", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 28, "discord_username": "ilyashka0", "discord_id": "913102464654389380", "full_name": "itadori_talentless", "static": "15468", "note": "-"},
      {"uid": 29, "discord_username": "fxuckcute", "discord_id": "898969187324686347", "full_name": "-", "static": "-", "note": "норм должен зайти"},
      {"uid": 30, "discord_username": "kmspokuni_", "discord_id": "649504229928468502", "full_name": "jonatan_antisocial", "static": "186277", "note": "-"},
      {"uid": 31, "discord_username": "psiqq", "discord_id": "473532385338589186", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 32, "discord_username": "kageshisa_", "discord_id": "1428289234770464799", "full_name": "kagesha_antisocial", "static": "7983", "note": "-"},
      {"uid": 33, "discord_username": "kiss812", "discord_id": "661211279448604704", "full_name": "karamelka_antiscl", "static": "19531", "note": "-"},
      {"uid": 34, "discord_username": ".kazurai", "discord_id": "1307296722786975784", "full_name": "kazurai_antisocial", "static": "27399", "note": "-"},
      {"uid": 35, "discord_username": "twizzy911", "discord_id": "641930694758760449", "full_name": "-", "static": "-", "note": "плюс гешки возможно"},
      {"uid": 36, "discord_username": "siniy3633", "discord_id": "564402051614900224", "full_name": "kirya_antisocial", "static": "187522", "note": "-"},
      {"uid": 37, "discord_username": ".kisesex", "discord_id": "932232775866654801", "full_name": "-", "static": "-", "note": "не знаю"},
      {"uid": 38, "discord_username": "kodi_s7", "discord_id": "917331466369781820", "full_name": "kodi_antisocial", "static": "89296", "note": "-"},
      {"uid": 39, "discord_username": "krunya1337", "discord_id": "491987704456675329", "full_name": "krunya_antisocial", "static": "186333", "note": "-"},
      {"uid": 40, "discord_username": "asd000_00", "discord_id": "1485676423250313226", "full_name": "light_antisocial", "static": "185688", "note": "-"},
      {"uid": 41, "discord_username": "xm1shanya", "discord_id": "1085812699893612594", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 42, "discord_username": "a6i6ok", "discord_id": "934500241087017060", "full_name": "maestro_antisocial", "static": "186911", "note": "-"},
      {"uid": 43, "discord_username": "maurizio222", "discord_id": "393045330037440518", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 44, "discord_username": ".prekracno", "discord_id": "706541114336739438", "full_name": "maximka_cortez", "static": "17155", "note": "-"},
      {"uid": 45, "discord_username": "bladina2015_53659", "discord_id": "1486790238750900314", "full_name": "-", "static": "-", "note": "не знаю"},
      {"uid": 46, "discord_username": "temamops", "discord_id": "1423646491586596975", "full_name": "-", "static": "-", "note": "euro_violence возможно"},
      {"uid": 47, "discord_username": "k1dutzu", "discord_id": "1224333734787022869", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 48, "discord_username": "obito52", "discord_id": "1244067170976010305", "full_name": "-", "static": "-", "note": "не заинвайчен"},
      {"uid": 49, "discord_username": "pulll123", "discord_id": "293034161999183874", "full_name": "-", "static": "-", "note": "тип харуказе возможно"},
      {"uid": 50, "discord_username": "pizdabank", "discord_id": "1344373504828768286", "full_name": "-", "static": "-", "note": "еблан"},
      {"uid": 51, "discord_username": "52mogila", "discord_id": "536041095549812757", "full_name": "prince_meersalz", "static": "1235", "note": "-"},
      {"uid": 52, "discord_username": "hanma48", "discord_id": "730790319351660604", "full_name": "-", "static": "-", "note": "не знаю заинвайтится"},
      {"uid": 53, "discord_username": "sajiha", "discord_id": "679553785059606550", "full_name": "sajiha_antisocial", "static": "190531", "note": "-"},
      {"uid": 54, "discord_username": "dreamer6478", "discord_id": "300157185634074637", "full_name": "-", "static": "-", "note": "плюс гешки"},
      {"uid": 55, "discord_username": "_takashik_", "discord_id": "670602580715634701", "full_name": "takashi_antisocial", "static": "20484", "note": "афк кент аегиса"},
      {"uid": 56, "discord_username": "takura1337", "discord_id": "946736184103673876", "full_name": "takura_blade", "static": "188980", "note": "-"},
      {"uid": 57, "discord_username": "trewq124e", "discord_id": "1026916085385138276", "full_name": "tony_antisocial", "static": "17399", "note": "-"},
      {"uid": 58, "discord_username": "jevhora1", "discord_id": "1152293598205857882", "full_name": "trident_antisocial", "static": "149711", "note": "-"},
      {"uid": 59, "discord_username": "viknixx", "discord_id": "625218978419048448", "full_name": "vinix_screamz", "static": "143351", "note": "-"},
      {"uid": 60, "discord_username": "xxoneknotfg", "discord_id": "841753700455809114", "full_name": "eternal_unticvare", "static": "143763", "note": "-"},
      {"uid": 61, "discord_username": "you0235", "discord_id": "395884974873772033", "full_name": "you_antisocial", "static": "2579", "note": "-"},
      {"uid": 62, "discord_username": "hate812", "discord_id": "1293666315147546685", "full_name": "lancer_antisocial", "static": "195832", "note": "-"},
      {"uid": 63, "discord_username": "dissociativ3disorder", "discord_id": "977992138124062761", "full_name": "bill_antisocial", "static": "130621", "note": "-"},
      {"uid": 64, "discord_username": "tolik_yt", "discord_id": "904769506700849162", "full_name": "fitusi_hellary", "static": "11367", "note": "-"},
    ]

    col = get_members_game_col()
    inserted = 0
    skipped = 0
    for rec in records:
        existing = col.find_one({"discord_id": rec["discord_id"]})
        if not existing:
            col.insert_one(rec)
            inserted += 1
        else:
            skipped += 1

    # Устанавливаем счётчик uid
    get_counters_collection().update_one(
        {"_id": "members_uid_counter"},
        {"$set": {"value": 64}},
        upsert=True
    )

    await interaction.followup.send(f"✅ Залито: {inserted}, пропущено (уже есть): {skipped}. Счётчик uid установлен на 64.", ephemeral=True)


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

    mp_number = await get_next_mp_number()
    created_at_iso = discord.utils.utcnow().isoformat()

    data = {
        "title": title,
        "max_slots": slots,
        "main_list": [],
        "mp_number": mp_number,
        "creator_id": str(interaction.user.id),
        "created_at": created_at_iso,
        "closed": False,
    }

    embed = build_reg_embed(data)
    view = RegView()
    msg = await interaction.channel.send(embed=embed, view=view)

    # Создаём ветку для плюсов
    thread = await msg.create_thread(name="плюсы")

    # Создаём эмбед в канале логов МП + ветку для системных логов
    mp_log_message_id = None
    mp_log_thread_id = None
    mp_log_thread_link = None
    try:
        log_channel = interaction.guild.get_channel(MP_LOGS_CHANNEL_ID)
        if log_channel:
            log_embed = build_mp_log_embed(data, closed=False)
            log_msg = await log_channel.send(embed=log_embed)
            mp_log_message_id = str(log_msg.id)
            log_thread = await log_msg.create_thread(name=f"логи МП #{mp_number}")
            mp_log_thread_id = str(log_thread.id)
            mp_log_thread_link = log_thread.jump_url
    except Exception as e:
        print(f"[ERROR] mp log channel: {e}")

    data["mp_log_message_id"] = mp_log_message_id
    data["mp_log_thread_id"] = mp_log_thread_id
    data["mp_log_thread_link"] = mp_log_thread_link

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

    await interaction.followup.send(f"мероприятие #{mp_number} создано", ephemeral=True)


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
    bot.loop.create_task(mp_cleanup_loop())
    bot.add_view(VacationPanelView())
    bot.add_view(VacationApproveView())
    bot.add_view(DatabasePanelView())
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
