import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import os
import re
from pymongo import MongoClient
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Канал куда отправляется панель
PANEL_CHANNEL_ID = 1514639955027034212

# Роли тиров и соответствующие им категории
# Если у пользователя нет ни одной тир-роли — он попадает в "нотир"
TIER_ROLES = {
    1510603348276023547: 1510953268799606865,  # тир б -> категория
    1510603326331293846: 1510953111089451079,  # тир а -> категория
    1510603296321306774: 1510952983867686922,  # тир с -> категория
}
NO_TIER_CATEGORY_ID = 1510952722214551653  # категория для нотир

REFRESH_ROLES = {1510604555040198816, 1510601395999346819}
ADMIN_ROLE_ID = 1510608237878181958

MONGO_URL = os.getenv("MONGO_URL")


def get_collection():
    mongo_client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    db = mongo_client["freshboyswag"]
    return db["channels"]


def has_refresh_role(member):
    return any(role.id in REFRESH_ROLES for role in member.roles)


def get_category_for_member(member: discord.Member):
    """
    Возвращает ID категории для участника исходя из его тира.
    Приоритет: тир б > тир а > тир с > нотир.
    """
    member_role_ids = {role.id for role in member.roles}
    for role_id, category_id in TIER_ROLES.items():
        if role_id in member_role_ids:
            return category_id
    return NO_TIER_CATEGORY_ID


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
        print(f"[DEBUG] Нажата кнопка, user_id: {user_id}")

        try:
            collection = get_collection()
            existing_entry = collection.find_one({"user_id": user_id})
            print(f"[DEBUG] Запись в БД: {existing_entry}")
        except Exception as e:
            await interaction.followup.send("ошибка базы данных", ephemeral=True)
            print(f"[ERROR] MongoDB ошибка: {e}")
            return

        if existing_entry:
            await interaction.followup.send(
                "у тебя уже есть канал или квота израсходована", ephemeral=True
            )
            return

        category_id = get_category_for_member(interaction.user)
        category = interaction.guild.get_channel(category_id)

        if not category:
            await interaction.followup.send(
                "категория не найдена, обратись к администратору", ephemeral=True
            )
            print(f"[ERROR] Категория {category_id} не найдена на сервере")
            return

        name = self.channel_name.value.strip().replace(" ", "-").lower()

        admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await interaction.guild.create_text_channel(
            name, category=category, overwrites=overwrites
        )

        result = collection.insert_one({"user_id": user_id, "channel_id": channel.id})
        print(f"[DEBUG] Запись сохранена: {result.inserted_id}")

        await interaction.followup.send(f"канал {channel.mention} создан!", ephemeral=True)


class PanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Создать личное дело",
        style=discord.ButtonStyle.blurple,
        custom_id="reg_channel",
        emoji="📁"
    )
    async def reg_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal())


def build_embed() -> discord.Embed:
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


@bot.tree.command(name="starts", description="Отправить панель в канал")
@commands.has_permissions(administrator=True)
async def starts(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    channel = interaction.guild.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        await interaction.followup.send("канал не найден", ephemeral=True)
        return

    async for message in channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    await channel.send(embed=build_embed(), view=PanelView())
    await interaction.followup.send("панель отправлена", ephemeral=True)


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
    print(f"[DEBUG] Рефреш для user_id: {user_id}")

    try:
        collection = get_collection()
        entry = collection.find_one({"user_id": user_id})
        print(f"[DEBUG] Запись перед удалением: {entry}")
        result = collection.delete_one({"user_id": user_id})
        print(f"[DEBUG] Удалено записей: {result.deleted_count}")
    except Exception as e:
        await interaction.response.send_message("ошибка базы данных", ephemeral=True)
        print(f"[ERROR] MongoDB ошибка: {e}")
        return

    if result.deleted_count == 0:
        await interaction.response.send_message("он ещё не регал", ephemeral=True)
        return

    await interaction.response.send_message(
        "квота на создание личного канала сброшена", ephemeral=True
    )


@bot.tree.command(name="sync", description="Синхронизировать команды")
@commands.has_permissions(administrator=True)
async def sync_slash(interaction: discord.Interaction):
    try:
        synced = await bot.tree.sync()
        await interaction.response.send_message(
            f"синхронизировано {len(synced)} команд", ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"ошибка: {e}", ephemeral=True)


@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"синхронизировано {len(synced)} команд")
    except Exception as e:
        await ctx.send(f"ошибка: {e}")


@bot.event
async def on_ready():
    bot.add_view(PanelView())
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
