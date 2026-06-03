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

ROLES_CATEGORIES = {
    1510603939664629891: 1510952722214551653,  # test
    1510603296321306774: 1510952983867686922,  # tier s
    1510603326331293846: 1510953111089451079,  # tier a
    1510603348276023547: 1510953268799606865,  # tier b
}

STARTS_CHANNELS = {
    1510603939664629891: 1510952816334733343,  # test
    1510603296321306774: 1510953048753836073,  # tier s
    1510603326331293846: 1510953181285322772,  # tier a
    1510603348276023547: 1510953323757568101,  # tier b
}

REFRESH_ROLES = {1510604555040198816, 1510601395999346819}
ADMIN_ROLE_ID = 1510608237878181958

def get_collection():
    mongo_client = MongoClient(os.getenv("MONGO_URL"), serverSelectionTimeoutMS=5000)
    db = mongo_client["freshboyswag"]
    return db["channels"]

def get_category_for_user(member):
    for role in member.roles:
        if role.id in ROLES_CATEGORIES:
            return ROLES_CATEGORIES[role.id]
    return None

def has_refresh_role(member):
    return any(role.id in REFRESH_ROLES for role in member.roles)

class ChannelModal(Modal, title="Создать канал"):
    channel_name = TextInput(
        label="название",
        placeholder="ник в игре",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        try:
            collection = get_collection()
            existing_entry = collection.find_one({"user_id": user_id})
        except Exception as e:
            await interaction.response.send_message(
                "ошибка базы данных", ephemeral=True
            )
            print(f"MongoDB ошибка: {e}")
            return

        if existing_entry:
            existing = interaction.guild.get_channel(existing_entry["channel_id"])
            if existing:
                await interaction.response.send_message(
                    f"у тебя уже есть канал: {existing.mention}", ephemeral=True
                )
                return

        category_id = get_category_for_user(interaction.user)
        if not category_id:
            await interaction.response.send_message(
                "нет нужной роли", ephemeral=True
            )
            return

        category = interaction.guild.get_channel(category_id)
        name = self.channel_name.value.strip().replace(" ", "-").lower()

        admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True),
        }
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True)

        channel = await interaction.guild.create_text_channel(
            name, category=category, overwrites=overwrites
        )

        collection.insert_one({"user_id": user_id, "channel_id": channel.id})

        await interaction.response.send_message(
            f"канал {channel.mention} создан!", ephemeral=True
        )

class PanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👀 Создать личный канал", style=discord.ButtonStyle.green, custom_id="reg_channel")
    async def reg_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChannelModal())

@bot.tree.command(name="starts", description="Отправить кнопки во все каналы")
@commands.has_permissions(administrator=True)
async def starts(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    for role_id, channel_id in STARTS_CHANNELS.items():
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            async for message in channel.history(limit=100):
                if message.author == bot.user:
                    await message.delete()
            await channel.send("freshboyswag", view=PanelView())
    await interaction.followup.send("сообщения обновлены", ephemeral=True)

@bot.tree.command(name="refresh", description="Сбросить квоту на создание канала")
async def refresh(interaction: discord.Interaction, user: str):
    if not has_refresh_role(interaction.user):
        await interaction.response.send_message(
            "нет прав", ephemeral=True
        )
        return

    match = re.search(r"\d{17,20}", user)
    if not match:
        await interaction.response.send_message(
            "тег или айди", ephemeral=True
        )
        return

    user_id = match.group()

    try:
        collection = get_collection()
        result = collection.delete_one({"user_id": user_id})
    except Exception as e:
        await interaction.response.send_message(
            "ошибка базы данных", ephemeral=True
        )
        print(f"MongoDB ошибка: {e}")
        return

    if result.deleted_count == 0:
        await interaction.response.send_message(
            "он еще не регал", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "квота на создание личного канала сброшена", ephemeral=True
    )

@bot.event
async def on_ready():
    bot.add_view(PanelView())
    print(f"Бот запущен: {bot.user}")

@bot.command()
@commands.has_permissions(administrator=True)
async def sync(ctx):
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"синхронизировано {len(synced)} команд")
    except Exception as e:
        await ctx.send(f"ошибка: {e}")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_server():
    HTTPServer(("0.0.0.0", 10000), Handler).serve_forever()

Thread(target=run_server, daemon=True).start()

bot.run(os.getenv("TOKEN"))
