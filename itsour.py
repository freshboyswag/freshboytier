import discord
from discord.ext import commands
from discord.ui import Modal, TextInput, View
import os
import json
import re

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "channels.json"

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

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)

def get_category_for_user(member):
    for role in member.roles:
        if role.id in ROLES_CATEGORIES:
            return ROLES_CATEGORIES[role.id]
    return None

def has_refresh_role(member):
    return any(role.id in REFRESH_ROLES for role in member.roles)

class ChannelModal(Modal, title="Создать канал"):
    channel_name = TextInput(
        label="Название канала",
        placeholder="мой-канал",
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        db = load_db()
        user_id = str(interaction.user.id)

        if user_id in db:
            existing = interaction.guild.get_channel(db[user_id])
            if existing:
                await interaction.response.send_message(
                    f"❌ Ты уже регал канал: {existing.mention}", ephemeral=True
                )
                return

        category_id = get_category_for_user(interaction.user)
        if not category_id:
            await interaction.response.send_message(
                "❌ У тебя нет нужной роли!", ephemeral=True
            )
            return

        category = interaction.guild.get_channel(category_id)
        name = self.channel_name.value.strip().replace(" ", "-").lower()
        channel = await interaction.guild.create_text_channel(name, category=category)

        db[user_id] = channel.id
        save_db(db)

        await interaction.response.send_message(
            f"✅ Канал {channel.mention} создан!", ephemeral=True
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
            await channel.send("freshboyswag", view=PanelView())
    await interaction.followup.send("сообщения обновлены", ephemeral=True)

@bot.tree.command(name="refresh", description="Сбросить квоту на создание канала")
async def refresh(interaction: discord.Interaction, user: str):
    if not has_refresh_role(interaction.user):
        await interaction.response.send_message(
            "❌ У тебя нет прав на эту команду!", ephemeral=True
        )
        return

    # Определяем айди — упоминание или голый айди
    match = re.search(r"\d{17,20}", user)
    if not match:
        await interaction.response.send_message(
            "❌ Укажи упоминание или айди юзера", ephemeral=True
        )
        return

    user_id = match.group()
    db = load_db()

    if user_id not in db:
        await interaction.response.send_message(
            "❌ Этот юзер ещё не регал канал", ephemeral=True
        )
        return

    del db[user_id]
    save_db(db)

    await interaction.response.send_message(
        "квота на создание личного канала сброшена", ephemeral=True
    )

@bot.event
async def on_ready():
    bot.add_view(PanelView())
    await bot.tree.sync()
    print(f"Бот запущен: {bot.user}")

bot.run(os.getenv("TOKEN"))