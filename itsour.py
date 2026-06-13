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
TICKET_CATEGORY_ID    = 1515440541012066325  # категория где создаются тикеты
# Роль которая видит канал тикета (только она + автор)
TICKET_VIEW_ROLE_ID   = 1510614323754434670
# Роли которые могут использовать кнопки в тикете
RECRUIT_ROLE_IDS      = {1510614323754434670, 1510610391267545138, 1510601395999346819, 1510604555040198816}
ACCEPT_ROLE_ID        = 1510603939664629891
CALL_CHANNEL_ID       = 1510628196914171984
RESULTS_DM_CHANNEL_ID = 1510626562637041714  # канал results если лс закрыт

MONGO_URL = os.getenv("MONGO_URL")


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return client["freshboyswag"]

def get_collection():
    return get_db()["channels"]

def get_tickets_collection():
    return get_db()["tickets"]

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

        # Сохраняем тикет в БД
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

        # Удаляем сообщение об обзвоне из results если было
        results_msg_id = self.ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        # Эмбед в results
        embed = discord.Embed(color=0xff4444)
        embed.description = (
            f"Заявка пользователя {self.applicant.mention}\n\n"
            f"На вступление в семью была отклонена!\n"
            f"Причина: {self.reason.value}\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        # Лог в logs-rejected
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

        # Удаляем из БД и канал тикета
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

    def get_custom_ids(self):
        return {
            "accept": f"ticket_accept",
            "review": f"ticket_review",
            "call": f"ticket_call",
            "reject": f"ticket_reject",
        }

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

        # Удаляем сообщение об обзвоне из results если было
        results_msg_id = ticket_data.get("results_msg_id")
        if results_msg_id and results_channel:
            try:
                msg = await results_channel.fetch_message(int(results_msg_id))
                await msg.delete()
            except Exception:
                pass

        # Выдаём роль
        if applicant:
            accept_role = guild.get_role(ACCEPT_ROLE_ID)
            if accept_role:
                try:
                    await applicant.add_roles(accept_role)
                except Exception as e:
                    print(f"[ERROR] Не удалось выдать роль: {e}")

        # Эмбед в results
        embed = discord.Embed(color=0x2ecc71)
        embed.description = (
            f"Заявка пользователя {applicant.mention if applicant else ticket_data['applicant_name']}\n\n"
            f"На вступление в семью была принята! 🎉\n"
            f"Рассматривал заявку: {interaction.user.mention}"
        )
        if results_channel:
            await results_channel.send(embed=embed)

        # Лог в logs-accepted
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

        # Удаляем из БД и канал
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

        # Сохраняем ID сообщения в results если отправили туда
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
            "Все поля обязательны для заполнения. Заявки, заполненные не по форме, будут сразу закрыты\n\n"
            "Заявки без откатов будут закрыты, писать «покажу на демке», «кину позже» не нужно\n\n"
            "Откаты с гг должны быть не старше 2 недель\n\n"
            "Тикеты рассматриваются в течение 48 часов"
        ),
        color=0x2b2d31
    )
    embed.set_image(url="https://media.discordapp.net/attachments/1468989054451191840/1515329976151310376/photo_2026-06-13_15-20-03.jpg?ex=6a2e9c83&is=6a2d4b03&hm=6737177b88f5f1d677b6eb893b72a2d1691fb2da128946e5165160f23f2dda8c&=&format=webp&width=900&height=900")
    return embed


# ═══════════════════════════════════════════════
# КОМАНДЫ
# ═══════════════════════════════════════════════

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
